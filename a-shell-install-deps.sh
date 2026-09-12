#!/bin/sh
set -eu

PYTHON_BIN=${HERMES_WEBUI_PYTHON:-python3}

printf '%s\n' "Installing pure-Python dependencies needed by the first Hermes WebUI path..."
"$PYTHON_BIN" -m pip install \
  python-dotenv \
  pyyaml

printf '%s\n' "FastAPI and uvicorn are intentionally not installed:"
printf '%s\n' "the independent Hermes WebUI uses its own standard-library HTTP server."
printf '%s\n' "Dependency installation finished. Run:"
printf '%s\n' "  python3 a-shell-check.py"
