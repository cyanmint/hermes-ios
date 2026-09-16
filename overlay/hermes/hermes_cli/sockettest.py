"""Small native socket-listener diagnostic for a-Shell/WASI."""
from __future__ import annotations

import argparse
import socket
import time
import traceback


def build_sockettest_parser(subparsers):
    parser = subparsers.add_parser(
        "sockettest",
        help="Verify listening on a loopback TCP port",
        description="Bind and hold 127.0.0.1:8787 long enough for a probe.",
    )
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--verbose", action="store_true", help="print every socket-test stage")
    parser.set_defaults(func=cmd_sockettest)
    return parser


def cmd_sockettest(args) -> int:
    if args.duration < 0 or args.duration > 300:
        raise ValueError("duration must be between 0 and 300 seconds")
    def log(stage: str, **values) -> None:
        if args.verbose:
            suffix = " ".join(f"{key}={value!r}" for key, value in values.items())
            print(f"SOCKETTEST_STAGE {stage}" + (f" {suffix}" if suffix else ""), flush=True)

    log("start", host=args.host, port=args.port, duration=args.duration)
    listener = None
    try:
        log("socket.create.begin", family=socket.AF_INET, type=socket.SOCK_STREAM)
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            log("socket.create.ok", fileno=listener.fileno())
            log("socket.setopt.begin")
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            log("socket.setopt.ok")
            log("socket.bind.begin", address=(args.host, args.port))
            listener.bind((args.host, args.port))
            log("socket.bind.ok", address=listener.getsockname())
            log("socket.listen.begin", backlog=1)
            listener.listen(1)
            log("socket.listen.ok")
            print(f"SOCKETTEST_LISTENING {args.host}:{args.port}", flush=True)
            log("sleep.begin", duration=args.duration)
            time.sleep(args.duration)
            log("sleep.ok")
    except OSError as exc:
        print(f"SOCKETTEST_ERROR {args.host}:{args.port}: {type(exc).__name__}: {exc}", flush=True)
        if args.verbose:
            traceback.print_exc()
        return 1
    except BaseException:
        print("SOCKETTEST_FATAL", flush=True)
        traceback.print_exc()
        return 1
    log("socket.close.ok")
    print(f"SOCKETTEST_CLOSED {args.host}:{args.port}", flush=True)
    return 0
