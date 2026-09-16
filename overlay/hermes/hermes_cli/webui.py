"""Standalone WebUI launcher bundled into the WASI runtime."""

from __future__ import annotations

import os


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
    if args.host is not None:
        os.environ["HERMES_WEBUI_HOST"] = str(args.host)
    if args.port is not None:
        os.environ["HERMES_WEBUI_PORT"] = str(args.port)
    import server
    return server.main()
