#!/usr/bin/env python3
"""Small stdlib-only debug server for a-Shell.

Protocol: newline-delimited JSON control frames. Binary file content is base64
encoded in bounded chunks. The server never invokes a shell; exec receives an
argv list and uses asyncio.create_subprocess_exec.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import hmac
import json
import os
import signal
import sys
from pathlib import Path
from typing import Any

MAX_LINE = 1024 * 1024
CHUNK = 48 * 1024


def send_json(writer: asyncio.StreamWriter, value: dict[str, Any]) -> None:
    payload = (json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode()
    if len(payload) > MAX_LINE:
        raise ValueError("response frame is too large")
    writer.write(payload)


async def read_json(reader: asyncio.StreamReader) -> dict[str, Any]:
    line = await reader.readline()
    if not line:
        raise EOFError
    if len(line) > MAX_LINE:
        raise ValueError("request frame is too large")
    value = json.loads(line)
    if not isinstance(value, dict):
        raise ValueError("request must be a JSON object")
    return value


class DebugServer:
    def __init__(self, root: Path, password: str, max_bytes: int) -> None:
        self.root = root.resolve()
        self.password_digest = hashlib.sha256(password.encode()).digest()
        self.max_bytes = max_bytes

    def resolve_path(self, raw: str) -> Path:
        candidate = (self.root / raw).resolve() if not os.path.isabs(raw) else Path(raw).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("path is outside the debug root") from exc
        return candidate

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        authenticated = False
        try:
            request = await read_json(reader)
            if request.get("op") != "auth" or not isinstance(request.get("password"), str):
                raise ValueError("first request must authenticate with a password")
            supplied = hashlib.sha256(request["password"].encode()).digest()
            authenticated = hmac.compare_digest(supplied, self.password_digest)
            send_json(writer, {"ok": authenticated, "event": "auth"})
            await writer.drain()
            if not authenticated:
                return
            while True:
                request = await read_json(reader)
                await self.dispatch(request, reader, writer)
                await writer.drain()
        except EOFError:
            pass
        except Exception as exc:
            try:
                send_json(writer, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
                await writer.drain()
            except (ConnectionError, RuntimeError):
                pass
        finally:
            writer.close()
            await writer.wait_closed()
            print(f"closed {peer}", file=sys.stderr)

    async def dispatch(self, request: dict[str, Any], reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        op = request.get("op")
        if op == "exec":
            await self.exec_command(request, writer)
        elif op == "upload":
            await self.upload(request, reader, writer)
        elif op == "download":
            await self.download(request, writer)
        elif op == "ping":
            send_json(writer, {"ok": True, "event": "pong"})
        else:
            raise ValueError(f"unknown operation: {op!r}")

    async def exec_command(self, request: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        argv = request.get("argv")
        if not isinstance(argv, list) or not argv or not all(isinstance(item, str) and item for item in argv):
            raise ValueError("exec requires a non-empty argv list")
        cwd = self.resolve_path(str(request.get("cwd", ".")))
        if not cwd.is_dir():
            raise ValueError("cwd is not a directory inside the debug root")
        timeout = float(request.get("timeout", 0) or 0)
        if timeout < 0 or timeout > 3600:
            raise ValueError("timeout must be between 0 and 3600 seconds")
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        send_json(writer, {"ok": True, "event": "started", "pid": process.pid})

        async def forward(stream: asyncio.StreamReader, name: str) -> None:
            while True:
                data = await stream.read(CHUNK)
                if not data:
                    return
                send_json(writer, {"event": name, "data": base64.b64encode(data).decode("ascii")})
                await writer.drain()

        async def wait_process() -> int:
            await asyncio.gather(forward(process.stdout, "stdout"), forward(process.stderr, "stderr"))
            return await process.wait()

        try:
            returncode = await asyncio.wait_for(wait_process(), timeout or None)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
            send_json(writer, {"ok": False, "event": "exit", "returncode": -signal.SIGKILL, "timeout": True})
            return
        send_json(writer, {"ok": returncode == 0, "event": "exit", "returncode": returncode})

    async def upload(self, request: dict[str, Any], reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        path = self.resolve_path(str(request.get("path", "")))
        size = int(request.get("size", -1))
        if size < 0 or size > self.max_bytes:
            raise ValueError("invalid upload size")
        path.parent.mkdir(parents=True, exist_ok=True)
        remaining = size
        with path.open("wb") as output:
            while remaining:
                frame = await read_json(reader)
                if frame.get("op") != "chunk":
                    raise ValueError("expected upload chunk")
                data = base64.b64decode(str(frame.get("data", "")), validate=True)
                if not data or len(data) > CHUNK or len(data) > remaining:
                    raise ValueError("invalid upload chunk")
                output.write(data)
                remaining -= len(data)
        send_json(writer, {"ok": True, "event": "upload", "path": str(path.relative_to(self.root)), "size": size})

    async def download(self, request: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        path = self.resolve_path(str(request.get("path", "")))
        if not path.is_file():
            raise FileNotFoundError(path)
        size = path.stat().st_size
        if size > self.max_bytes:
            raise ValueError("file exceeds transfer limit")
        send_json(writer, {"ok": True, "event": "download", "path": str(path.relative_to(self.root)), "size": size})
        with path.open("rb") as source:
            while data := source.read(CHUNK):
                send_json(writer, {"op": "chunk", "data": base64.b64encode(data).decode("ascii")})
        send_json(writer, {"event": "download_end"})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--password", default=os.environ.get("ASHELL_DEBUG_PASSWORD"), help="debug password")
    parser.add_argument("--max-bytes", type=int, default=256 * 1024 * 1024)
    args = parser.parse_args()
    password = args.password
    if not password:
        parser.error("--password or ASHELL_DEBUG_PASSWORD is required")
    if args.host not in {"127.0.0.1", "::1", "localhost"} and not args.password:
        parser.error("--password must be supplied explicitly when listening beyond localhost")
    server = DebugServer(args.root, password, args.max_bytes)

    async def run() -> None:
        listener = await asyncio.start_server(server.handle, args.host, args.port)
        addresses = ", ".join(str(sock.getsockname()) for sock in listener.sockets or [])
        print(f"ASHELL_DEBUG_LISTEN {addresses}", file=sys.stderr, flush=True)
        print("ASHELL_DEBUG_AUTH password", file=sys.stderr, flush=True)
        async with listener:
            await listener.serve_forever()

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
