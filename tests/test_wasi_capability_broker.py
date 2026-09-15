from __future__ import annotations

import io
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import wasi_capability_broker as broker


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        body = b"broker-ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):
        pass


class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()

    def test_handshake_and_net_request(self):
        port = self.server.server_address[1]
        incoming = io.BytesIO()
        broker.write_frame(incoming, {"type": "request", "id": 1, "method": "handshake"})
        broker.write_frame(incoming, {
            "type": "request",
            "id": 2,
            "method": "net.request",
            "params": {"url": f"http://127.0.0.1:{port}/", "timeout": 5},
        })
        incoming.seek(0)
        output = io.BytesIO()
        self.assertEqual(broker.Broker(incoming, output).serve(), 0)
        output.seek(0)
        first = broker.read_frame(output)
        second = broker.read_frame(output)
        self.assertEqual(first["result"]["capabilities"], ["net.request"])
        self.assertEqual(second["result"]["status"], 200)
        self.assertEqual(second["result"]["body"]["encoding"], "base64")

    def test_network_policy_rejects_credentials(self):
        with self.assertRaises(broker.BrokerError) as caught:
            broker._request({"url": "https://user:pass@example.test/"})
        self.assertEqual(caught.exception.code, "NETWORK_POLICY")

    def test_oversized_frame_is_rejected(self):
        with self.assertRaises(broker.BrokerError) as caught:
            broker.read_frame(io.BytesIO((broker.MAX_FRAME + 1).to_bytes(4, "big")))
        self.assertEqual(caught.exception.code, "FRAME_TOO_LARGE")


if __name__ == "__main__":
    unittest.main()
