from __future__ import annotations

import base64
import io
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
import socket

from pathlib import Path
from unittest.mock import patch

import loader


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"loader-ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class LoaderTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_native_request(self):
        port = self.server.server_address[1]
        result = loader._request(7, {"url": f"http://127.0.0.1:{port}/"})
        self.assertTrue(result["ok"])
        body = base64.b64decode(result["result"]["body"]["data"])
        self.assertEqual(body, b"loader-ok")

    def test_socket_bind_and_listen_dispatch(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            sockets = {1: listener}
            loader._socket_dispatch(sockets, {"method": "socket.bind", "id": 1, "host": "127.0.0.1", "port": 0})
            loader._socket_dispatch(sockets, {"method": "socket.listen", "id": 1, "backlog": 1})
            self.assertEqual(listener.getsockname()[0], "127.0.0.1")
            self.assertGreater(listener.getsockname()[1], 0)
        finally:
            listener.close()


    def test_rejects_url_credentials(self):
        result = loader._request(7, {"url": "https://user:pass@example.test/"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "NETWORK_POLICY")

    def test_wasm_command_without_arguments(self):
        with patch.object(loader, "_find_wasm_command", return_value="/bin/wasm"):
            self.assertEqual(
                loader._build_command(Path("/bundle/hermes.wasm"), []),
                ["/bin/wasm", "hermes.wasm"],
            )

    def test_wasm_command_forwards_version(self):
        with patch.object(loader, "_find_wasm_command", return_value="/bin/wasm"):
            self.assertEqual(
                loader._build_command(Path("/bundle/hermes.wasm"), ["--version"]),
                ["/bin/wasm", "hermes.wasm", "--version"],
            )

    def test_wasm_command_forwards_model(self):
        with patch.object(loader, "_find_wasm_command", return_value="/bin/wasm"):
            self.assertEqual(
                loader._build_command(Path("/bundle/hermes.wasm"), ["model"]),
                ["/bin/wasm", "hermes.wasm", "model"],
            )

    def test_routes_stdout_event_as_decoded_bytes(self):
        output = io.BytesIO()
        with patch.object(loader.sys, "stdout", type("Stream", (), {"buffer": output})()):
            loader._route_output_event({
                "type": "event",
                "event": "stdio.output",
                "stream": "stdout",
                "data": {"encoding": "base64", "data": base64.b64encode(b"hello\\n").decode()},
            })
        self.assertEqual(output.getvalue(), b"hello\\n")

    def test_routes_stderr_event_as_decoded_bytes(self):
        output = io.BytesIO()
        with patch.object(loader.sys, "stderr", type("Stream", (), {"buffer": output})()):
            loader._route_output_event({
                "type": "event",
                "event": "stdio.output",
                "stream": "stderr",
                "data": {"encoding": "base64", "data": base64.b64encode(b"warning\\n").decode()},
            })
        self.assertEqual(output.getvalue(), b"warning\\n")

    def test_rejects_socket_event_from_host_output(self):
        with self.assertRaises(loader.LoaderError) as context:
            loader._route_output_event({"type": "event", "event": "socket.data"})
        self.assertEqual(context.exception.code, "UNEXPECTED_EVENT")


if __name__ == "__main__":
    unittest.main()
