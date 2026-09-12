#!/bin/sh
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN=${HERMES_WEBUI_PYTHON:-python3}

# Keep the CLI in the same self-contained profile as the WebUI. Override these
# variables before invoking the script when a different profile is desired.
export HERMES_HOME=${HERMES_A_SHELL_HOME:-.}
export HERMES_WEBUI_AGENT_DIR=${HERMES_WEBUI_AGENT_DIR:-"$ROOT_DIR/hermes-agent"}
export PYTHONPATH="$ROOT_DIR/hermes-agent${PYTHONPATH:+:$PYTHONPATH}"

cd "$ROOT_DIR"
mkdir -p "$HERMES_HOME"

if [ ! -f "$ROOT_DIR/a-shell-cli.py" ]; then
  printf '%s\n' "a-Shell CLI source is missing: $ROOT_DIR/a-shell-cli.py" >&2
  exit 1
fi

# The full CLI imports optional Rich/provider modules while constructing every
# subcommand. The a-Shell-safe facade keeps config/provider setup usable with
# the standard a-Shell dependency set and shares the same HERMES_HOME.
exec "$PYTHON_BIN" a-shell-cli.py "$@"
