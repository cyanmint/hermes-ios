#!/usr/bin/env bash
set -euo pipefail

# Build-time sources. These are deliberately not Git submodules.
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
DEST=${1:-"$ROOT/build/external"}
AGENT_COMMIT=${HERMES_AGENT_COMMIT:-2246c245f51e03eb6a151d19119009156e84659a}
WEBUI_COMMIT=${HERMES_WEBUI_COMMIT:-e36f77389191fe9d81cd3a7416772e2f7b022e19}

mkdir -p "$DEST"
fetch() {
  local name=$1 url=$2 commit=$3
  local dir="$DEST/$name"
  if [ ! -d "$dir/.git" ]; then
    rm -rf "$dir"
    git clone --no-checkout "$url" "$dir"
  fi
  git -C "$dir" fetch --depth=1 origin "$commit"
  git -C "$dir" checkout --detach "$commit"
}

fetch hermes-agent https://github.com/NousResearch/hermes-agent.git "$AGENT_COMMIT"
fetch hermes-webui https://github.com/nesquena/hermes-webui.git "$WEBUI_COMMIT"
printf 'hermes-agent=%s\nhermes-webui=%s\n' "$AGENT_COMMIT" "$WEBUI_COMMIT" > "$DEST/SOURCES"
