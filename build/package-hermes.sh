#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SOURCE_ARTIFACT=${SOURCE_ARTIFACT:-/root/hermes-build/hermes-artifact}
OUTPUT=${OUTPUT:-"$ROOT/hermes.wasm"}
RUNTIME_ARCHIVE=${RUNTIME_ARCHIVE:-"$ROOT/hermes-runtime.zip"}

SOURCE_WASM="$SOURCE_ARTIFACT/python.wasm"
CPYTHON_SOURCE=${CPYTHON_SOURCE:-/root/hermes-build/loader-build}
HERMES_SOURCE=${HERMES_SOURCE:-"$ROOT/build/external/hermes-agent"}
WEBUI_SOURCE=${WEBUI_SOURCE:-"$ROOT/build/external/hermes-webui"}

copy_tree() {
  local source=$1
  local destination=$2
  mkdir -p "$destination"
  (cd "$source" && tar -cf - .) | (cd "$destination" && tar -xf -)
}

STAGE_ROOT=$(mktemp -d /tmp/hermes-runtime.XXXXXX)
STAGE_ZIP=/tmp/hermes-runtime.${BASHPID}.zip
cleanup() {
  rm -rf "$STAGE_ROOT" "$STAGE_ZIP"
}
trap cleanup EXIT
[ -f "$SOURCE_WASM" ] || {
  printf 'missing WASM source artifact: %s\n' "$SOURCE_WASM" >&2
  exit 2
}
[ "$(od -An -tx1 -N4 "$SOURCE_WASM" | tr -d ' \n')" = "0061736d" ] || {
  printf 'source artifact is not a raw WASM module: %s\n' "$SOURCE_WASM" >&2
  exit 2
}
[ -f "$WEBUI_SOURCE/server.py" ] && [ -d "$WEBUI_SOURCE/api" ] && [ -d "$WEBUI_SOURCE/static" ] || {
  printf 'missing Hermes WebUI source: %s\n' "$WEBUI_SOURCE" >&2
  exit 5
}

# Delivery 1 is the WASM module itself. It must never be replaced by a shell
# launcher; loader.py is the only host-side process wrapper.
cp "$SOURCE_WASM" "$OUTPUT"
chmod +x "$OUTPUT"
[ "$(od -An -tx1 -N4 "$OUTPUT" | tr -d ' \n')" = "0061736d" ] || exit 2

# CPython still needs its standard library and project overlays at runtime.
# Keep those support files separate from the two named delivery entrypoints.
rm -rf "$ROOT/hermes-runtime" "$RUNTIME_ARCHIVE"
mkdir -p "$STAGE_ROOT/lib/python3.13/site-packages"
copy_tree "$SOURCE_ARTIFACT/lib" "$STAGE_ROOT/lib"
[ -d "$CPYTHON_SOURCE/Lib" ] || {
  printf 'missing CPython standard library: %s\n' "$CPYTHON_SOURCE/Lib" >&2
  exit 3
}
copy_tree "$CPYTHON_SOURCE/Lib" "$STAGE_ROOT/lib/python3.13"
# Keep the complete site-packages tree from the built runtime.  It contains
# Hermes' bundled dependencies; deleting it here leaves only the small set of
# source files copied below and produces a deceptively valid but unusable ZIP.
mkdir -p "$STAGE_ROOT/lib/python3.13/site-packages"
if [ -d "$HERMES_SOURCE/hermes_cli" ]; then
  for module in "$HERMES_SOURCE"/*.py; do
    [ -f "$module" ] || continue
    cp "$module" "$STAGE_ROOT/lib/python3.13/site-packages/"
  done
  # The built artifact may contain an older copy of these packages.  Overlay
  # every Hermes-owned runtime package from the pinned source checkout so
  # imports cannot mix versions (for example agent.proxy_bypass and
  # hermes_cli.main from different revisions).
  for package in acp_adapter agent cron gateway hermes_cli plugins providers tools tui_gateway hermes; do
    if [ -d "$HERMES_SOURCE/$package" ]; then
      copy_tree "$HERMES_SOURCE/$package" "$STAGE_ROOT/lib/python3.13/site-packages/$package"
    fi
  done
else
  printf 'missing Hermes Agent source: %s\n' "$HERMES_SOURCE/hermes_cli" >&2
  exit 4
fi
copy_tree "$ROOT/overlay/hermes" "$STAGE_ROOT/lib/python3.13/site-packages"
copy_tree "$WEBUI_SOURCE/api" "$STAGE_ROOT/lib/python3.13/api"
copy_tree "$WEBUI_SOURCE/static" "$STAGE_ROOT/lib/python3.13/static"
for module in bootstrap.py server.py mcp_server.py; do
  [ -f "$WEBUI_SOURCE/$module" ] && cp "$WEBUI_SOURCE/$module" "$STAGE_ROOT/lib/python3.13/$module"
done
copy_tree "$ROOT/overlay/python" "$STAGE_ROOT/lib/python3.13"

# Build the complete zip in WSL and copy one file across the Windows
# filesystem boundary.  The archive is also CPython's import path.
# ZIP_STORED is intentional: the WASI build omits the zlib extension, while
# CPython must import encodings from this archive before Python code starts.
python3 -c 'import os, sys, zipfile; root, output = sys.argv[1:]; z = zipfile.ZipFile(output, "w", zipfile.ZIP_STORED); [(z.write(os.path.join(directory, name), os.path.relpath(os.path.join(directory, name), root), compress_type=zipfile.ZIP_STORED)) for directory, _, names in os.walk(root) for name in names]; z.close()' "$STAGE_ROOT" "$STAGE_ZIP"
cp "$STAGE_ZIP" "$RUNTIME_ARCHIVE"

printf 'built %s (%s bytes)\n' "$OUTPUT" "$(stat -c %s "$OUTPUT")"
printf 'runtime archive: %s\n' "$RUNTIME_ARCHIVE"
file "$OUTPUT"
