#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$ROOT_DIR"
PYTHON_BIN=${HERMES_WEBUI_PYTHON:-python3}
export HERMES_WEBUI_PYTHON="$PYTHON_BIN"
export HERMES_WEBUI_AGENT_DIR="$ROOT_DIR/hermes-agent"
# a-Shell may predefine HERMES_HOME as a non-writable $HOME/.hermes. Do not
# preserve that inherited value; this self-contained package owns its profile.
# Use HERMES_A_SHELL_HOME when an alternate writable profile is intentional.
export HERMES_HOME=${HERMES_A_SHELL_HOME:-.}
export HERMES_WEBUI_STATE_DIR=${HERMES_A_SHELL_STATE_DIR:-"$HERMES_HOME/webui"}
# Keep the literal relative path. On a-Shell, `.` is accepted by the sandbox
# while the equivalent resolved absolute path can fail os.access().
export HERMES_WEBUI_DEFAULT_WORKSPACE=${HERMES_A_SHELL_WORKSPACE:-.}
export HERMES_WEBUI_HOST=${HERMES_WEBUI_HOST:-127.0.0.1}
export HERMES_WEBUI_PORT=${HERMES_WEBUI_PORT:-8787}
export PYTHONPATH="hermes-webui:hermes-agent${PYTHONPATH:+:$PYTHONPATH}"

if ! mkdir -p "$HERMES_HOME" "$HERMES_WEBUI_DEFAULT_WORKSPACE" 2>/dev/null; then
  HERMES_WEBUI_DEFAULT_WORKSPACE=.
  if ! mkdir -p "$HERMES_WEBUI_DEFAULT_WORKSPACE" 2>/dev/null; then
    HERMES_WEBUI_DEFAULT_WORKSPACE="$HOME/tmp/hermes-workspace"
    if ! mkdir -p "$HERMES_WEBUI_DEFAULT_WORKSPACE" 2>/dev/null; then
      printf '%s\n' "Could not create a writable workspace." >&2
      printf '%s\n' "Try: mkdir -p \"$PWD/workspace\"" >&2
      exit 1
    fi
  fi
fi
export HERMES_WEBUI_DEFAULT_WORKSPACE

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
