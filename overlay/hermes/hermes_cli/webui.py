"""Standalone WebUI launcher bundled into the WASI runtime."""

from __future__ import annotations

import os
import faulthandler
import sys
import time
import traceback


def _startup_log(stage: str) -> None:
    print(f"[webui-startup {time.monotonic():.3f}] {stage}", file=sys.stderr, flush=True)


def build_webui_parser(subparsers, *, cmd_webui):
    parser = subparsers.add_parser(
        "webui",
        help="Start the bundled Hermes WebUI",
        description="Start the bundled Hermes WebUI server without npm or a checkout",
    )
    parser.add_argument("--host", default=None, help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None, help="Port (default: 8787)")
    parser.set_defaults(func=cmd_webui)


def cmd_webui(args):
    try:
        faulthandler.enable(file=sys.stderr, all_threads=True)
    except (AttributeError, OSError, RuntimeError, ValueError) as exc:
        # a-Shell's framed stderr intentionally has no fileno().  Keep the
        # structured startup logs working instead of failing before import.
        _startup_log(f"faulthandler unavailable: {type(exc).__name__}: {exc}")
    _startup_log("cmd_webui entered")
    if args.host is not None:
        os.environ["HERMES_WEBUI_HOST"] = str(args.host)
        _startup_log(f"host override set: {args.host}")
    if args.port is not None:
        os.environ["HERMES_WEBUI_PORT"] = str(args.port)
        _startup_log(f"port override set: {args.port}")
    _startup_log("importing server")
    try:
        import server
    except BaseException:
        _startup_log("server import failed")
        traceback.print_exc(file=sys.stderr)
        raise
    _startup_log("server imported")
    try:
        _startup_log("entering server.main")
        result = server.main()
        _startup_log(f"server.main returned: {result!r}")
        return result
    except BaseException:
        _startup_log("server.main failed")
        traceback.print_exc(file=sys.stderr)
        raise
