#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SOURCE_ARTIFACT=${SOURCE_ARTIFACT:-/root/hermes-build/hermes-artifact}
OUTPUT=${OUTPUT:-"$ROOT/hermes.wasm"}
RUNTIME_ARCHIVE=${RUNTIME_ARCHIVE:-"$ROOT/hermes-runtime.zip"}

SOURCE_WASM="$SOURCE_ARTIFACT/python.wasm"
CPYTHON_SOURCE=${CPYTHON_SOURCE:-/root/hermes-build/loader-build}

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
rm -rf "$STAGE_ROOT/lib/python3.13/site-packages"
copy_tree "$ROOT/overlay/hermes" "$STAGE_ROOT/lib/python3.13/site-packages"
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
