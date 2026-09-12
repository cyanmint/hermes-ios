#!/usr/bin/env python3
"""Check the imports needed by the first a-Shell WebUI package."""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AGENT = ROOT / "hermes-agent"
WEBUI = ROOT / "hermes-webui"
os.environ.setdefault("HERMES_WEBUI_AGENT_DIR", str(AGENT))
os.environ.setdefault("HERMES_HOME", str(Path.home() / ".hermes"))
os.environ.setdefault("HERMES_WEBUI_STATE_DIR", str(Path.home() / ".hermes" / "webui"))
sys.path[:0] = [str(WEBUI), str(AGENT)]

modules = ["yaml", "cryptography", "httpx"]
failed = False
for name in modules:
    try:
        module = importlib.import_module(name)
        version = getattr(module, "__version__", "")
        print(f"OK   {name} {version}".rstrip())
    except Exception as exc:
        failed = True
        print(f"MISS {name}: {type(exc).__name__}: {exc}")

for name in ["dotenv", "openai", "pydantic"]:
    try:
        module = importlib.import_module(name)
        version = getattr(module, "__version__", "")
        print(f"OPTIONAL {name} {version}".rstrip())
    except Exception as exc:
        print(f"OPTIONAL-MISS {name}: {type(exc).__name__}: {exc}")

try:
    from api.config import _AGENT_DIR
    print(f"OK   webui agent path: {_AGENT_DIR}")
except Exception as exc:
    failed = True
    print(f"MISS webui config: {type(exc).__name__}: {exc}")

try:
    from run_agent import AIAgent
    print(f"OK   AIAgent: {AIAgent.__module__}.{AIAgent.__name__}")
except Exception as exc:
    failed = True
    print(f"MISS AIAgent: {type(exc).__name__}: {exc}")

try:
    import server  # noqa: F401
    print("OK   WebUI server import")
except Exception as exc:
    failed = True
    print(f"MISS WebUI server: {type(exc).__name__}: {exc}")

raise SystemExit(1 if failed else 0)
