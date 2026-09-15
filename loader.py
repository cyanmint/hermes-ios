#!/usr/bin/env python3
"""a-Shell native loader and network broker for a WASI Hermes command.

The loader owns the native Python network stack. Hermes communicates with it
through length-prefixed JSON frames on the WASI child's stdin/stdout. Child
stderr is forwarded to the loader's stderr and never enters the RPC stream.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import socket
import ssl
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, BinaryIO

MAX_FRAME = 4 * 1024 * 1024
MAX_REQUEST_BODY = 2 * 1024 * 1024
MAX_RESPONSE_BODY = 8 * 1024 * 1024
MAX_SOCKET_BUFFER = 1024 * 1024


class LoaderError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def read_frame(stream: BinaryIO) -> dict[str, Any] | None:
    header = stream.read(4)
    if not header:
        return None
    if len(header) != 4:
        raise LoaderError("MALFORMED_FRAME", "truncated frame header")
    size = int.from_bytes(header, "big")
    if size > MAX_FRAME:
        raise LoaderError("FRAME_TOO_LARGE", "request frame is too large")
    payload = stream.read(size)
    if len(payload) != size:
        raise LoaderError("MALFORMED_FRAME", "truncated frame payload")
    try:
        value = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LoaderError("MALFORMED_FRAME", "request is not UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise LoaderError("MALFORMED_FRAME", "request must be a JSON object")
    return value


def write_frame(stream: BinaryIO, value: dict[str, Any]) -> None:
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(payload) > MAX_FRAME:
        raise LoaderError("FRAME_TOO_LARGE", "response frame is too large")
    stream.write(len(payload).to_bytes(4, "big"))
    stream.write(payload)
    stream.flush()


def _error(request_id: Any, code: str, message: str) -> dict[str, Any]:
    return {
        "type": "response",
        "id": request_id,
        "ok": False,
        "error": {"code": code, "message": message},
    }


def _request(request_id: Any, params: dict[str, Any]) -> dict[str, Any]:
    url = params.get("url")
    if not isinstance(url, str) or len(url) > 4096:
        return _error(request_id, "INVALID_ARGUMENT", "url is invalid")
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return _error(request_id, "NETWORK_POLICY", "only http/https URLs are allowed")
    if parsed.username or parsed.password or parsed.fragment:
        return _error(request_id, "NETWORK_POLICY", "URL credentials/fragments are not allowed")
    method = params.get("method", "GET")
    if not isinstance(method, str) or not method.isalpha() or len(method) > 16:
        return _error(request_id, "INVALID_ARGUMENT", "method is invalid")
    headers = params.get("headers", {})
    if not isinstance(headers, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in headers.items()):
        return _error(request_id, "INVALID_ARGUMENT", "headers must be a string map")
    body = params.get("body")
    if body is None:
        data = None
    elif isinstance(body, str):
        data = body.encode("utf-8")
    elif isinstance(body, dict) and body.get("encoding") == "base64" and isinstance(body.get("data"), str):
        try:
            data = base64.b64decode(body["data"], validate=True)
        except ValueError:
            return _error(request_id, "INVALID_ARGUMENT", "body is not valid base64")
    else:
        return _error(request_id, "INVALID_ARGUMENT", "body must be UTF-8 text or base64")
    if data is not None and len(data) > MAX_REQUEST_BODY:
        return _error(request_id, "REQUEST_TOO_LARGE", "request body is too large")
    try:
        timeout = float(params.get("timeout", 30))
    except (TypeError, ValueError):
        return _error(request_id, "INVALID_ARGUMENT", "timeout is invalid")
    if not 0 < timeout <= 300:
        return _error(request_id, "INVALID_ARGUMENT", "timeout must be 0 < timeout <= 300")

    request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read(MAX_RESPONSE_BODY + 1)
            if len(payload) > MAX_RESPONSE_BODY:
                return _error(request_id, "RESPONSE_TOO_LARGE", "response body is too large")
            return {
                "type": "response",
                "id": request_id,
                "ok": True,
                "result": {
                    "status": int(response.status),
                    "headers": {str(k): str(v) for k, v in response.headers.items()},
                    "body": {"encoding": "base64", "data": base64.b64encode(payload).decode("ascii")},
                },
            }
    except urllib.error.HTTPError as exc:
        payload = exc.read(MAX_RESPONSE_BODY)
        return {
            "type": "response",
            "id": request_id,
            "ok": True,
            "result": {
                "status": int(exc.code),
                "headers": {str(k): str(v) for k, v in exc.headers.items()},
                "body": {"encoding": "base64", "data": base64.b64encode(payload).decode("ascii")},
            },
        }
    except (urllib.error.URLError, TimeoutError, OSError):
        return _error(request_id, "NETWORK_ERROR", "native a-Shell network request failed")


def _socket_dispatch(sockets: dict[int, socket.socket], params: dict[str, Any]) -> dict[str, Any]:
    method = params.get("method")
    sid = params.get("id")
    if method == "socket.hostname":
        return {"name": socket.gethostname()}
    if method == "socket.resolve":
        addresses = socket.getaddrinfo(params.get("host"), params.get("port"), params.get("family", 0), params.get("type", 0), params.get("proto", 0), params.get("flags", 0))
        return {"addresses": [list(item) for item in addresses]}
    if method == "socket.open":
        sock = socket.socket(params.get("family", socket.AF_INET), params.get("type", socket.SOCK_STREAM), params.get("proto", 0))
        sid = max(sockets, default=0) + 1
        sockets[sid] = sock
        return {"id": sid}
    if not isinstance(sid, int) or sid not in sockets:
        raise LoaderError("SOCKET_ERROR", "unknown socket id")
    sock = sockets[sid]
    if method == "socket.connect":
        sock.settimeout(params.get("timeout")); sock.connect((params["host"], params["port"])); return {}
    if method in {"socket.send", "socket.sendall"}:
        data = base64.b64decode(params["data"]["data"], validate=True)
        if len(data) > MAX_SOCKET_BUFFER: raise LoaderError("REQUEST_TOO_LARGE", "socket write is too large")
        if method == "socket.sendall": sock.sendall(data); return {"sent": len(data)}
        return {"sent": sock.send(data, params.get("flags", 0))}
    if method == "socket.recv":
        size = int(params.get("size", 0));
        if not 0 < size <= MAX_SOCKET_BUFFER: raise LoaderError("INVALID_ARGUMENT", "invalid recv size")
        sock.settimeout(params.get("timeout")); return {"data": base64.b64encode(sock.recv(size, params.get("flags", 0))).decode("ascii")}
    if method == "socket.timeout": sock.settimeout(params.get("timeout")); return {}
    if method == "socket.getopt": return {"value": sock.getsockopt(params["level"], params["option"])}
    if method == "socket.setopt": sock.setsockopt(params["level"], params["option"], params["value"]); return {}
    if method == "socket.name": return {"address": list(sock.getsockname())}
    if method == "socket.peer": return {"address": list(sock.getpeername())}
    if method == "socket.shutdown": sock.shutdown(params["how"]); return {}
    if method == "socket.start_tls":
        context = ssl.create_default_context() if params.get("check_hostname") else ssl._create_unverified_context()
        context.check_hostname = bool(params.get("check_hostname")); context.verify_mode = params.get("verify_mode", ssl.CERT_NONE)
        sockets[sid] = context.wrap_socket(sock, server_hostname=params.get("server_hostname"), do_handshake_on_connect=True)
        return {}
    if method == "socket.close": sock.close(); sockets.pop(sid, None); return {}
    raise LoaderError("CAPABILITY_UNAVAILABLE", "unsupported socket capability")


def _dispatch(frame: dict[str, Any], sockets: dict[int, socket.socket]) -> dict[str, Any] | None:
    request_id = frame.get("id")
    if frame.get("type") == "event" and frame.get("event") == "io.write":
        raw = base64.b64decode(frame.get("data", {}).get("data", ""), validate=True)
        target = sys.stdout.buffer if frame.get("stream") == "stdout" else sys.stderr.buffer
        target.write(raw); target.flush(); return None
    if frame.get("type") != "request": return _error(request_id, "MALFORMED_REQUEST", "type must be request")
    if frame.get("method") == "handshake":
        return {"type": "response", "id": request_id, "ok": True, "result": {"protocol": 2, "capabilities": ["io.write", "socket", "ssl", "net.request"]}}
    if frame.get("method", "").startswith("socket."):
        try: return {"type": "response", "id": request_id, "ok": True, "result": _socket_dispatch(sockets, {"method": frame["method"], **(frame.get("params") or {})})}
        except LoaderError as exc: return _error(request_id, exc.code, exc.message)
        except (OSError, ValueError, KeyError) as exc: return _error(request_id, "SOCKET_ERROR", str(exc))
    if frame.get("method") == "crypto.random" and isinstance(frame.get("params"), dict):
        size = int(frame["params"].get("size", 0))
        if not 0 <= size <= 1024 * 1024:
            return _error(request_id, "INVALID_ARGUMENT", "invalid random size")
        return {"type": "response", "id": request_id, "ok": True, "result": {"data": base64.b64encode(os.urandom(size)).decode("ascii")}}
    if frame.get("method") == "net.request" and isinstance(frame.get("params"), dict): return _request(request_id, frame["params"])
    return _error(request_id, "CAPABILITY_UNAVAILABLE", "unsupported capability")


def serve_child(process: subprocess.Popen[bytes]) -> int:
    assert process.stdin is not None and process.stdout is not None
    sockets: dict[int, socket.socket] = {}
    while True:
        frame = read_frame(process.stdout)
        if frame is None:
            break
        response = _dispatch(frame, sockets)
        if response is not None: write_frame(process.stdin, response)
    for sock in sockets.values(): sock.close()
    return process.wait()


def forward_stderr(stream: BinaryIO) -> None:
    while data := stream.read(4096):
        sys.stderr.buffer.write(data)
        sys.stderr.buffer.flush()


def _find_artifact() -> Path:
    """Find the bundled Hermes runtime without requiring a CLI option."""
    candidates = [
        Path(__file__).resolve().with_name("hermes"),
        Path.cwd() / "hermes",
    ]
    configured = os.environ.get("HERMES_ARTIFACT")
    if configured:
        candidates.insert(0, Path(configured).expanduser())
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    searched = ", ".join(str(path) for path in candidates)
    raise LoaderError("ARTIFACT_NOT_FOUND", f"Hermes runtime not found; searched: {searched}")


def _find_wasm_command() -> str:
    """Find a-Shell's wasm launcher, with Wasmtime as a desktop fallback."""
    configured = os.environ.get("HERMES_WASM_COMMAND") or os.environ.get("WASM_COMMAND")
    if configured:
        return str(Path(configured).expanduser())
    for name in ("wasm", "wasmtime"):
        found = shutil.which(name)
        if found:
            return found
    # a-Shell does not put its bundled launcher on PATH.  The application UUID
    # changes between installs, so discover it rather than hard-coding one.
    bundle_candidates = sorted(Path("/private/var/containers/Bundle/Application").glob("*/a-Shell.app/bin/wasm"))
    if bundle_candidates:
        return str(bundle_candidates[0])
    raise LoaderError(
        "WASM_COMMAND_NOT_FOUND",
        "could not find a-Shell wasm launcher or Wasmtime (set HERMES_WASM_COMMAND only for debugging)",
    )


def _build_command(artifact: Path, args: list[str]) -> list[str]:
    wasm_command = _find_wasm_command()
    if Path(wasm_command).name == "wasmtime":
        root = str(artifact.parent)
        home = os.environ.get("HOME") or "/"
        command = [
            wasm_command, "run",
            "--env", f"HOME={home}",
            "--dir", f"{root}::/",
        ]
    else:
        command = [wasm_command]
    # a-Shell's bundled launcher requires the WASM entry path to be relative
    # to its working directory; absolute sandbox paths make it terminate the
    # hosting Python process before producing stderr.
    return [*command, artifact.name, *args]


def main() -> int:
    # loader.py is intentionally transparent: every user argument belongs to
    # the bundled ./hermes runtime, not to this wrapper.
    args = sys.argv[1:]
    try:
        artifact = _find_artifact()
        command = _build_command(artifact, args)
    except LoaderError as exc:
        print(f"loader: {exc.message}", file=sys.stderr)
        return 2
    child = subprocess.Popen(
        command,
        cwd=str(artifact.parent),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if any(arg in {"--version", "-V"} for arg in args):
        stdout, stderr = child.communicate()
        sys.stdout.buffer.write(stdout)
        sys.stdout.buffer.flush()
        sys.stderr.buffer.write(stderr)
        sys.stderr.buffer.flush()
        return child.returncode
    assert child.stderr is not None
    threading.Thread(target=forward_stderr, args=(child.stderr,), daemon=True).start()
    return serve_child(child)


if __name__ == "__main__":
    raise SystemExit(main())
