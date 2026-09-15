#!/usr/bin/env python3
"""Small stdlib-only debug server for a-Shell.

Protocol: newline-delimited JSON control frames. Binary file content is base64
encoded in bounded chunks. The server never invokes a shell; exec receives an
argv list and uses asyncio.create_subprocess_exec.

The listener is fixed to 127.0.0.1 and has no application-level authentication.
Use a USB port forwarder such as ``iproxy`` to expose it to the development
host; do not bind this server to a LAN address.
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import signal
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

MAX_LINE = 1024 * 1024
CHUNK = 48 * 1024
LOOPBACK_HOST = "127.0.0.1"


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
    def __init__(self, root: Path, max_bytes: int, verbose: bool = False) -> None:
        self.root = root.resolve()
        self.max_bytes = max_bytes
        self.verbose = verbose
        self.active_processes: set[asyncio.subprocess.Process] = set()

    def log(self, message: str) -> None:
        if self.verbose:
            print(f"[ashell-debug] {message}", file=sys.stderr, flush=True)

    async def terminate_process(self, process: asyncio.subprocess.Process, reason: str) -> int:
        """Terminate an exec child and reap it, even when it ignores SIGTERM."""
        if process.returncode is None:
            self.log(f"terminating pid={process.pid} reason={reason}")
            process.terminate()
        try:
            return await asyncio.wait_for(process.wait(), 2)
        except asyncio.TimeoutError:
            self.log(f"killing pid={process.pid} after terminate timeout")
            process.kill()
            return await process.wait()

    async def terminate_all(self, reason: str) -> None:
        processes = tuple(self.active_processes)
        if processes:
            await asyncio.gather(*(self.terminate_process(process, reason) for process in processes))

    def resolve_path(self, raw: str) -> Path:
        candidate = (self.root / raw).resolve() if not os.path.isabs(raw) else Path(raw).resolve()
        try:
            candidate.relative_to(self.root)
        except ValueError as exc:
            raise ValueError("path is outside the debug root") from exc
        return candidate

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        self.log(f"accepted peer={peer!r}")
        try:
            while True:
                request = await read_json(reader)
                self.log(f"request peer={peer!r} op={request.get('op')!r}")
                await self.dispatch(request, reader, writer)
                await writer.drain()
        except EOFError:
            self.log(f"eof peer={peer!r}")
        except Exception as exc:
            self.log(f"error peer={peer!r} type={type(exc).__name__}: {exc}")
            try:
                send_json(writer, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
                await writer.drain()
            except (ConnectionError, RuntimeError):
                pass
        finally:
            writer.close()
            await writer.wait_closed()
            self.log(f"closed peer={peer!r}")

    async def dispatch(self, request: dict[str, Any], reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        op = request.get("op")
        if op == "exec":
            await self.exec_command(request, writer)
        elif op == "upload":
            await self.upload(request, reader, writer)
        elif op == "update":
            await self.update(request, reader, writer)
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
        self.log(f"exec argv0={argv[0]!r} argc={len(argv)} cwd={cwd} timeout={timeout}")
        process = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self.active_processes.add(process)
        self.log(f"exec started pid={process.pid}")
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
            self.log(f"exec timeout pid={process.pid}")
            returncode = await self.terminate_process(process, "command timeout")
            send_json(writer, {"ok": False, "event": "exit", "returncode": returncode, "timeout": True})
            return
        except (ConnectionError, BrokenPipeError, asyncio.CancelledError):
            await self.terminate_process(process, "client disconnected")
            raise
        finally:
            self.active_processes.discard(process)
        self.log(f"exec exit pid={process.pid} returncode={returncode}")
        send_json(writer, {"ok": returncode == 0, "event": "exit", "returncode": returncode})

    async def upload(self, request: dict[str, Any], reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        path = self.resolve_path(str(request.get("path", "")))
        size = int(request.get("size", -1))
        if size < 0 or size > self.max_bytes:
            raise ValueError("invalid upload size")
        self.log(f"upload path={path} size={size}")
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
        self.log(f"upload complete path={path}")

    async def update(self, request: dict[str, Any], reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Replace a debugger script in-process, without launching Python."""
        path = self.resolve_path(str(request.get("path", "")))
        size = int(request.get("size", -1))
        if size < 1 or size > min(self.max_bytes, 4 * 1024 * 1024):
            raise ValueError("invalid debugger update size")
        if path.suffix != ".py":
            raise ValueError("debugger update target must be a Python script")
        self.log(f"update path={path} size={size}")

        mode = stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o700
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=path.parent,
                prefix=f".{path.name}.",
                suffix=".tmp",
                delete=False,
            ) as output:
                temporary = Path(output.name)
                remaining = size
                while remaining:
                    frame = await read_json(reader)
                    if frame.get("op") != "chunk":
                        raise ValueError("expected debugger update chunk")
                    data = base64.b64decode(str(frame.get("data", "")), validate=True)
                    if not data or len(data) > CHUNK or len(data) > remaining:
                        raise ValueError("invalid debugger update chunk")
                    output.write(data)
                    remaining -= len(data)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, mode)
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        send_json(writer, {"ok": True, "event": "update", "path": str(path.relative_to(self.root)), "size": size})
        self.log(f"update complete path={path}")

    async def download(self, request: dict[str, Any], writer: asyncio.StreamWriter) -> None:
        path = self.resolve_path(str(request.get("path", "")))
        if not path.is_file():
            raise FileNotFoundError(path)
        size = path.stat().st_size
        if size > self.max_bytes:
            raise ValueError("file exceeds transfer limit")
        self.log(f"download path={path} size={size}")
        send_json(writer, {"ok": True, "event": "download", "path": str(path.relative_to(self.root)), "size": size})
        with path.open("rb") as source:
            while data := source.read(CHUNK):
                send_json(writer, {"op": "chunk", "data": base64.b64encode(data).decode("ascii")})
        send_json(writer, {"event": "download_end"})
        self.log(f"download complete path={path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--max-bytes", type=int, default=256 * 1024 * 1024)
    parser.add_argument(
        "-v",
        "--verbose",
        dest="verbose",
        action="store_true",
        default=True,
        help="log connections and operations (default)",
    )
    parser.add_argument("--quiet", dest="verbose", action="store_false", help="only print startup messages")
    args = parser.parse_args()
    server = DebugServer(args.root, args.max_bytes, args.verbose)

    async def run() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(signum, stop.set)
            except (NotImplementedError, RuntimeError):
                pass
        listener = await asyncio.start_server(
            server.handle,
            LOOPBACK_HOST,
            args.port,
            limit=MAX_LINE + 1,
        )
        addresses = ", ".join(str(sock.getsockname()) for sock in listener.sockets or [])
        print(f"ASHELL_DEBUG_LISTEN {addresses}", file=sys.stderr, flush=True)
        print("ASHELL_DEBUG_TRANSPORT usb-loopback", file=sys.stderr, flush=True)
        server.log(f"root={server.root} max_bytes={args.max_bytes}")
        try:
            async with listener:
                await stop.wait()
        finally:
            await server.terminate_all("debug server stopped")
            server.log("stopped")

    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
