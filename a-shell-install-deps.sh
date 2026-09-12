#!/bin/sh
set -eu

PYTHON_BIN=${HERMES_WEBUI_PYTHON:-python3}

printf '%s\n' "Installing the first Hermes WebUI dependencies into a-Shell Python..."
"$PYTHON_BIN" -m pip install \
  fastapi \
  uvicorn \
  openai \
  python-dotenv \
  httpx \
  pydantic \
  pyyaml \
  cryptography

printf '%s\n' "Dependency installation finished. Run:"
printf '%s\n' "  python3 a-shell-check.py"
