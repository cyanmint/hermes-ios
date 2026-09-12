#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN=${HERMES_WEBUI_PYTHON:-python3}

# Keep the upstream Hermes CLI's arguments and command dispatch unchanged. The
# wrapper only supplies the self-contained a-Shell environment.
unset HERMES_HOME
export HERMES_HOME=.
export HERMES_WEBUI_AGENT_DIR="$ROOT_DIR/hermes-agent"
export HERMES_WEBUI_ASHELL_MODE=1
export PYTHONPATH="hermes-agent${PYTHONPATH:+:$PYTHONPATH}"

cd "$ROOT_DIR"
mkdir -p "$HERMES_HOME"

if [ ! -f "$ROOT_DIR/hermes-agent/hermes_cli/main.py" ]; then
  printf '%s\n' "Hermes CLI source is missing: $ROOT_DIR/hermes-agent" >&2
  exit 1
fi

# Pass every option and subcommand verbatim to the upstream implementation.
exec "$PYTHON_BIN" -m hermes_cli.main "$@"
