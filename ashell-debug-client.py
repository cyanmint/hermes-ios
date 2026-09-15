#!/usr/bin/env python3
"""USB-forwarded client for ashell-debug-server.py; stdlib only.

The server is intentionally reachable only through the host-side loopback
port. Forward the device port over USB (for example with ``iproxy``) and use
that forwarded local port here.
"""
from __future__ import annotations

import argparse
import base64
import json
import socket
import sys
from pathlib import Path
from typing import Any

# Keep JSON request lines below asyncio's default 64 KiB stream limit on
# already-running older debug servers.
CHUNK = 32 * 1024
LOOPBACK_HOST = "127.0.0.1"


def send(sock: socket.socket, value: dict[str, Any]) -> None:
    sock.sendall((json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode())


def receive(stream) -> dict[str, Any]:
    line = stream.readline()
    if not line:
        raise ConnectionError("debug server closed the connection")
    return json.loads(line)


def connect(args):
    sock = socket.create_connection((LOOPBACK_HOST, args.port), args.timeout)
    stream = sock.makefile("rb")
    return sock, stream


def command(args) -> int:
    sock, stream = connect(args)
    try:
        send(sock, {"op": "exec", "argv": args.argv, "cwd": args.cwd, "timeout": args.command_timeout})
        code = 1
        while True:
            frame = receive(stream)
            event = frame.get("event")
            if event in {"stdout", "stderr"}:
                data = base64.b64decode(frame["data"])
                target = sys.stdout.buffer if event == "stdout" else sys.stderr.buffer
                target.write(data)
                target.flush()
            elif event == "exit":
                code = int(frame.get("returncode", 1))
                break
            elif frame.get("error"):
                print(frame["error"], file=sys.stderr)
                return 1
        return code
    finally:
        sock.close()


def upload(args) -> int:
    source = Path(args.local)
    size = source.stat().st_size
    sock, stream = connect(args)
    try:
        send(sock, {"op": "upload", "path": args.remote, "size": size})
        with source.open("rb") as input_file:
            while data := input_file.read(CHUNK):
                send(sock, {"op": "chunk", "data": base64.b64encode(data).decode("ascii")})
        reply = receive(stream)
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error", "upload failed"))
        print(json.dumps(reply, ensure_ascii=False))
        return 0
    finally:
        sock.close()


def update(args) -> int:
    """Atomically replace a debugger script without starting Python."""
    source = Path(args.local)
    size = source.stat().st_size
    sock, stream = connect(args)
    try:
        send(sock, {"op": "update", "path": args.remote, "size": size})
        with source.open("rb") as input_file:
            while data := input_file.read(CHUNK):
                send(sock, {"op": "chunk", "data": base64.b64encode(data).decode("ascii")})
        reply = receive(stream)
        if not reply.get("ok"):
            raise RuntimeError(reply.get("error", "debugger update failed"))
        print(json.dumps(reply, ensure_ascii=False))
        return 0
    finally:
        sock.close()


def download(args) -> int:
    sock, stream = connect(args)
    try:
        send(sock, {"op": "download", "path": args.remote})
        header = receive(stream)
        if not header.get("ok"):
            raise RuntimeError(header.get("error", "download failed"))
        remaining = int(header["size"])
        with Path(args.local).open("wb") as output:
            while remaining:
                frame = receive(stream)
                if frame.get("op") != "chunk":
                    raise RuntimeError("invalid download stream")
                data = base64.b64decode(frame["data"], validate=True)
                if not data or len(data) > remaining:
                    raise RuntimeError("invalid download chunk")
                output.write(data)
                remaining -= len(data)
        end = receive(stream)
        if end.get("event") != "download_end":
            raise RuntimeError("download did not finish")
        print(f"downloaded {header['size']} bytes to {args.local}")
        return 0
    finally:
        sock.close()


def restart(args) -> int:
    sock, stream = connect(args)
    try:
        send(sock, {"op": "restart"})
        reply = receive(stream)
        if not reply.get("ok") or reply.get("event") != "restart":
            print(reply.get("error", "restart failed"), file=sys.stderr)
            return 1
        print("debug server restarting")
        return 0
    finally:
        sock.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--timeout", type=float, default=10)
    sub = parser.add_subparsers(dest="operation", required=True)
    run = sub.add_parser("exec")
    run.add_argument("--cwd", default=".")
    run.add_argument("--command-timeout", type=float, default=0)
    run.add_argument("argv", nargs=argparse.REMAINDER)
    put = sub.add_parser("upload")
    put.add_argument("local")
    put.add_argument("remote")
    update_parser = sub.add_parser(
        "update",
        help="atomically replace a remote debugger script without spawning Python",
    )
    update_parser.add_argument("local")
    update_parser.add_argument("remote", nargs="?", default="ashell-debug-server.py")
    get = sub.add_parser("download")
    get.add_argument("remote")
    get.add_argument("local")
    sub.add_parser("restart", help="restart the a-Shell debug server in place")
    args = parser.parse_args()
    if args.operation == "exec":
        if args.argv and args.argv[0] == "--":
            args.argv.pop(0)
        if not args.argv:
            parser.error("exec requires a command argv")
        return command(args)
    if args.operation == "upload":
        return upload(args)
    if args.operation == "update":
        return update(args)
    if args.operation == "restart":
        return restart(args)
    return download(args)


if __name__ == "__main__":
    raise SystemExit(main())
