#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT_DIR"
PYTHON_BIN=${HERMES_WEBUI_PYTHON:-python3}
export HERMES_WEBUI_PYTHON="$PYTHON_BIN"
export HERMES_WEBUI_AGENT_DIR="$ROOT_DIR/hermes-agent"
export HERMES_HOME=${HERMES_HOME:-"$HOME/.hermes"}
export HERMES_WEBUI_STATE_DIR=${HERMES_WEBUI_STATE_DIR:-"$HERMES_HOME/webui"}
export HERMES_WEBUI_DEFAULT_WORKSPACE=${HERMES_WEBUI_DEFAULT_WORKSPACE:-"$ROOT_DIR/workspace"}
export HERMES_WEBUI_HOST=${HERMES_WEBUI_HOST:-127.0.0.1}
export HERMES_WEBUI_PORT=${HERMES_WEBUI_PORT:-8787}
export PYTHONPATH="hermes-webui:hermes-agent${PYTHONPATH:+:$PYTHONPATH}"
mkdir -p "$HERMES_WEBUI_DEFAULT_WORKSPACE"

if [ ! -f "hermes-webui/server.py" ]; then
  printf '%s\n' "Hermes WebUI source is missing: $ROOT_DIR/hermes-webui" >&2
  exit 1
fi
if [ ! -f "hermes-agent/run_agent.py" ]; then
  printf '%s\n' "Hermes Agent source is missing: $ROOT_DIR/hermes-agent" >&2
  exit 1
fi

printf '%s\n' "Hermes WebUI: http://${HERMES_WEBUI_HOST}:${HERMES_WEBUI_PORT}"
printf '%s\n' "Open this URL in Safari on the iPad. Press Ctrl-C to stop."
exec "$PYTHON_BIN" hermes-webui/server.py
