from __future__ import annotations

import base64
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
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

    def test_rejects_url_credentials(self):
        result = loader._request(7, {"url": "https://user:pass@example.test/"})
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"]["code"], "NETWORK_POLICY")

    def test_transparent_command_without_arguments(self):
        with patch.object(loader, "_find_wasm_command", return_value="/bin/wasm"):
            self.assertEqual(
                loader._build_command(Path("/bundle/hermes"), []),
                ["/bin/wasm", str(Path("/bundle/hermes"))],
            )

    def test_transparent_command_forwards_version(self):
        with patch.object(loader, "_find_wasm_command", return_value="/bin/wasm"):
            self.assertEqual(
                loader._build_command(Path("/bundle/hermes"), ["--version"]),
                ["/bin/wasm", str(Path("/bundle/hermes")), "--version"],
            )

    def test_transparent_command_forwards_model(self):
        with patch.object(loader, "_find_wasm_command", return_value="/bin/wasm"):
            self.assertEqual(
                loader._build_command(Path("/bundle/hermes"), ["model"]),
                ["/bin/wasm", str(Path("/bundle/hermes")), "model"],
            )


if __name__ == "__main__":
    unittest.main()
