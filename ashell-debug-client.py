#!/usr/bin/env python3
"""Client for ashell-debug-server.py; stdlib only."""
from __future__ import annotations

import argparse
import base64
import json
import socket
import sys
from pathlib import Path
from typing import Any

CHUNK = 48 * 1024


def send(sock: socket.socket, value: dict[str, Any]) -> None:
    sock.sendall((json.dumps(value, ensure_ascii=False, separators=(",", ":")) + "\n").encode())


def receive(stream) -> dict[str, Any]:
    line = stream.readline()
    if not line:
        raise ConnectionError("debug server closed the connection")
    return json.loads(line)


def connect(args):
    sock = socket.create_connection((args.host, args.port), args.timeout)
    stream = sock.makefile("rb")
    send(sock, {"op": "auth", "password": args.password})
    reply = receive(stream)
    if not reply.get("ok"):
        raise PermissionError("debug password rejected")
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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--password", required=True)
    parser.add_argument("--timeout", type=float, default=10)
    sub = parser.add_subparsers(dest="operation", required=True)
    run = sub.add_parser("exec")
    run.add_argument("--cwd", default=".")
    run.add_argument("--command-timeout", type=float, default=0)
    run.add_argument("argv", nargs=argparse.REMAINDER)
    put = sub.add_parser("upload")
    put.add_argument("local")
    put.add_argument("remote")
    get = sub.add_parser("download")
    get.add_argument("remote")
    get.add_argument("local")
    args = parser.parse_args()
    if args.operation == "exec":
        if args.argv and args.argv[0] == "--":
            args.argv.pop(0)
        if not args.argv:
            parser.error("exec requires a command argv")
        return command(args)
    if args.operation == "upload":
        return upload(args)
    return download(args)


if __name__ == "__main__":
    raise SystemExit(main())
