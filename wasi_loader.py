"""WASI-side framed transport used by the a-Shell loader.

The WASM process never opens a host socket and never writes application output
as unframed bytes.  ``_socket`` and ``sitecustomize`` use this module to send
requests and output events over stdout; responses arrive on stdin.
"""
from __future__ import annotations

import base64
import json
import sys
from typing import Any

_next_id = 1
_rpc_stdin = sys.stdin.buffer
_rpc_stdout = sys.stdout.buffer


def _read_exact(size: int) -> bytes:
    data = _rpc_stdin.read(size)
    if len(data) != size:
        raise RuntimeError("loader closed the RPC channel")
    return data


def call(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    global _next_id
    request_id = _next_id
    _next_id += 1
    payload = json.dumps(
        {"type": "request", "id": request_id, "method": method, "params": params or {}},
        separators=(",", ":"),
    ).encode("utf-8")
    _rpc_stdout.write(len(payload).to_bytes(4, "big") + payload)
    _rpc_stdout.flush()
    size = int.from_bytes(_read_exact(4), "big")
    response = json.loads(_read_exact(size).decode("utf-8"))
    if response.get("id") != request_id:
        raise RuntimeError("loader returned an unexpected request id")
    if not response.get("ok"):
        error = response.get("error", {})
        raise OSError(error.get("code", "LOADER_ERROR"), error.get("message", "loader request failed"))
    return response.get("result", {})


def emit(stream: str, data: bytes) -> None:
    payload = json.dumps(
        {"type": "event", "event": "io.write", "stream": stream,
         "data": {"encoding": "base64", "data": base64.b64encode(data).decode("ascii")}},
        separators=(",", ":"),
    ).encode("utf-8")
    _rpc_stdout.write(len(payload).to_bytes(4, "big") + payload)
    _rpc_stdout.flush()


def install_stdio() -> None:
    class _Stream:
        def __init__(self, name: str) -> None:
            self.name = name
            self.buffer = self

        def write(self, value: Any) -> int:
            raw = value if isinstance(value, bytes) else str(value).encode("utf-8")
            if raw:
                emit(self.name, raw)
            return len(value)

        def flush(self) -> None:
            return None

        def isatty(self) -> bool:
            return False

        @property
        def encoding(self) -> str:
            return "utf-8"

    sys.stdout = _Stream("stdout")  # type: ignore[assignment]
    sys.stderr = _Stream("stderr")  # type: ignore[assignment]
