#!/usr/bin/env python3
"""Host-side capability broker for the WASI Hermes runtime.

The runtime and broker communicate over stdin/stdout using length-prefixed JSON
frames. Diagnostics are written to stderr. The first implemented capability is
bounded HTTPS/HTTP ``net.request``; arbitrary sockets are intentionally not
exposed.
"""
from __future__ import annotations

import argparse
import base64
import json
import os
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, BinaryIO

PROTOCOL_VERSION = 1
MAX_FRAME = 4 * 1024 * 1024
MAX_REQUEST_BODY = 2 * 1024 * 1024
MAX_RESPONSE_BODY = 8 * 1024 * 1024
DEFAULT_TIMEOUT = 30.0


class BrokerError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def read_frame(stream: BinaryIO) -> dict[str, Any] | None:
    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise BrokerError("MALFORMED_FRAME", "truncated frame header")
    size = int.from_bytes(header, "big")
    if size > MAX_FRAME:
        raise BrokerError("FRAME_TOO_LARGE", f"frame exceeds {MAX_FRAME} bytes")
    payload = stream.read(size)
    if len(payload) != size:
        raise BrokerError("MALFORMED_FRAME", "truncated frame payload")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BrokerError("MALFORMED_FRAME", "frame is not UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise BrokerError("MALFORMED_FRAME", "frame must be a JSON object")
    return value


def write_frame(stream: BinaryIO, value: dict[str, Any]) -> None:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_FRAME:
        raise BrokerError("FRAME_TOO_LARGE", f"response exceeds {MAX_FRAME} bytes")
    stream.write(len(payload).to_bytes(4, "big"))
    stream.write(payload)
    stream.flush()


def response(request_id: Any, *, result: dict[str, Any] | None = None, error: BrokerError | None = None) -> dict[str, Any]:
    if error is not None:
        return {
            "type": "response",
            "id": request_id,
            "ok": False,
            "error": {"code": error.code, "message": error.message},
        }
    return {"type": "response", "id": request_id, "ok": True, "result": result or {}}


def _validate_url(raw: Any) -> str:
    if not isinstance(raw, str) or len(raw) > 4096:
        raise BrokerError("INVALID_ARGUMENT", "url must be a string of at most 4096 bytes")
    parsed = urllib.parse.urlsplit(raw)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname:
        raise BrokerError("NETWORK_POLICY", "only http and https URLs with a hostname are allowed")
    if parsed.username or parsed.password:
        raise BrokerError("NETWORK_POLICY", "credentials in URLs are not allowed")
    if parsed.fragment:
        raise BrokerError("NETWORK_POLICY", "URL fragments are not sent to providers")
    return raw


def _request(params: dict[str, Any]) -> dict[str, Any]:
    url = _validate_url(params.get("url"))
    method = params.get("method", "GET")
    if not isinstance(method, str) or not method.isalpha() or len(method) > 16:
        raise BrokerError("INVALID_ARGUMENT", "method must be an alphabetic token")
    method = method.upper()
    headers = params.get("headers", {})
    if not isinstance(headers, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in headers.items()):
        raise BrokerError("INVALID_ARGUMENT", "headers must be a string map")
    body = params.get("body")
    if body is None:
        data = None
    elif isinstance(body, str):
        data = body.encode("utf-8")
    elif isinstance(body, dict) and body.get("encoding") == "base64" and isinstance(body.get("data"), str):
        try:
            data = base64.b64decode(body["data"], validate=True)
        except ValueError as exc:
            raise BrokerError("INVALID_ARGUMENT", "body is not valid base64") from exc
    else:
        raise BrokerError("INVALID_ARGUMENT", "body must be UTF-8 text or base64 data")
    if data is not None and len(data) > MAX_REQUEST_BODY:
        raise BrokerError("REQUEST_TOO_LARGE", f"request body exceeds {MAX_REQUEST_BODY} bytes")
    try:
        timeout = float(params.get("timeout", DEFAULT_TIMEOUT))
    except (TypeError, ValueError) as exc:
        raise BrokerError("INVALID_ARGUMENT", "timeout must be numeric") from exc
    if not 0 < timeout <= 300:
        raise BrokerError("INVALID_ARGUMENT", "timeout must be between 0 and 300 seconds")

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as handle:
            result_body = handle.read(MAX_RESPONSE_BODY + 1)
            if len(result_body) > MAX_RESPONSE_BODY:
                raise BrokerError("RESPONSE_TOO_LARGE", f"response exceeds {MAX_RESPONSE_BODY} bytes")
            result_headers = {str(key): str(value) for key, value in handle.headers.items()}
            return {
                "status": int(handle.status),
                "headers": result_headers,
                "body": {"encoding": "base64", "data": base64.b64encode(result_body).decode("ascii")},
            }
    except BrokerError:
        raise
    except urllib.error.HTTPError as exc:
        body = exc.read(MAX_RESPONSE_BODY)
        return {
            "status": int(exc.code),
            "headers": {str(key): str(value) for key, value in exc.headers.items()},
            "body": {"encoding": "base64", "data": base64.b64encode(body).decode("ascii")},
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise BrokerError("NETWORK_ERROR", "host request failed") from exc


@dataclass
class Broker:
    input_stream: BinaryIO
    output_stream: BinaryIO
    verbose: bool = False

    def log(self, message: str) -> None:
        if self.verbose:
            print(f"[wasi-broker] {message}", file=sys.stderr, flush=True)

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("id")
        if request.get("type") != "request":
            raise BrokerError("MALFORMED_REQUEST", "type must be request")
        method = request.get("method")
        if method == "handshake":
            return response(request_id, result={"protocol": PROTOCOL_VERSION, "capabilities": ["net.request"]})
        if method != "net.request":
            raise BrokerError("CAPABILITY_UNAVAILABLE", f"unsupported capability: {method!r}")
        params = request.get("params")
        if not isinstance(params, dict):
            raise BrokerError("INVALID_ARGUMENT", "params must be an object")
        return response(request_id, result=_request(params))

    def serve(self) -> int:
        seen_ids: set[str] = set()
        while True:
            request = read_frame(self.input_stream)
            if request is None:
                return 0
            request_id = request.get("id")
            key = json.dumps(request_id, sort_keys=True, ensure_ascii=False)
            if key in seen_ids:
                write_frame(self.output_stream, response(request_id, error=BrokerError("DUPLICATE_ID", "request id is already in flight")))
                continue
            seen_ids.add(key)
            try:
                self.log(f"request id={request_id!r} method={request.get('method')!r}")
                result = self.dispatch(request)
            except BrokerError as exc:
                result = response(request_id, error=exc)
            except Exception:
                self.log("internal error")
                result = response(request_id, error=BrokerError("INTERNAL_ERROR", "broker request failed"))
            write_frame(self.output_stream, result)
            seen_ids.remove(key)


def run_stdio(verbose: bool) -> int:
    return Broker(sys.stdin.buffer, sys.stdout.buffer, verbose).serve()


def run_runtime(args: argparse.Namespace) -> int:
    command = [args.wasmtime, "run", "--dir", f"{args.root}::/", args.runtime, *args.runtime_args]
    print(f"[wasi-broker] launching runtime {args.runtime}", file=sys.stderr, flush=True)
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def forward_stderr() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            sys.stderr.buffer.write(line)
            sys.stderr.buffer.flush()

    threading.Thread(target=forward_stderr, daemon=True).start()
    assert process.stdout is not None and process.stdin is not None
    try:
        while True:
            frame = read_frame(process.stdout)
            if frame is None:
                break
            write_frame(sys.stdout.buffer, frame)
    finally:
        if process.poll() is None:
            process.terminate()
        process.wait()
    return int(process.returncode or 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="mode", required=True)
    sub.add_parser("stdio", help="serve capability frames on stdin/stdout")
    runtime = sub.add_parser("runtime", help="launch a WASI runtime and proxy framed output")
    runtime.add_argument("--wasmtime", default="wasmtime")
    runtime.add_argument("--root", required=True)
    runtime.add_argument("runtime")
    runtime.add_argument("runtime_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if args.mode == "stdio":
        return run_stdio(args.verbose)
    return run_runtime(args)


if __name__ == "__main__":
    raise SystemExit(main())
