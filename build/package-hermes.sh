#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SOURCE_ARTIFACT=${SOURCE_ARTIFACT:-/root/hermes-build/hermes-artifact}
OUTPUT=${OUTPUT:-"$ROOT/hermes.wasm"}
RUNTIME_ROOT=${RUNTIME_ROOT:-"$ROOT/hermes-runtime"}

SOURCE_WASM="$SOURCE_ARTIFACT/python.wasm"
CPYTHON_SOURCE=${CPYTHON_SOURCE:-/root/hermes-build/loader-build}

copy_tree() {
  local source=$1
  local destination=$2
  mkdir -p "$destination"
  (cd "$source" && tar -cf - .) | (cd "$destination" && tar -xf -)
}

STAGE_ROOT=$(mktemp -d /tmp/hermes-runtime.XXXXXX)
STAGE_TAR=/tmp/hermes-runtime.${BASHPID}.tar
cleanup() {
  rm -rf "$STAGE_ROOT" "$STAGE_TAR"
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
rm -rf "$RUNTIME_ROOT"
mkdir -p "$STAGE_ROOT/lib/python3.13" "$STAGE_ROOT/python"
copy_tree "$SOURCE_ARTIFACT/lib" "$STAGE_ROOT/lib"
if [ -d "$SOURCE_ARTIFACT/python/Lib" ]; then
  copy_tree "$SOURCE_ARTIFACT/python" "$STAGE_ROOT/python"
else
  copy_tree "$SOURCE_ARTIFACT/python" "$STAGE_ROOT/python"
  [ -d "$CPYTHON_SOURCE/Lib" ] || {
    printf 'missing CPython standard library: %s\n' "$CPYTHON_SOURCE/Lib" >&2
    exit 3
  }
  copy_tree "$CPYTHON_SOURCE/Lib" "$STAGE_ROOT/python/Lib"
fi
copy_tree "$ROOT/overlay/hermes" "$STAGE_ROOT/python/site-packages"
rm -rf "$STAGE_ROOT/lib/python3.13/site-packages"
copy_tree "$ROOT/overlay/python" "$STAGE_ROOT/lib/python3.13"
# The direct CPython entrypoint also searches the Python/Lib tree before
# environment setup, so keep sitecustomize and wasi_loader in both supported
# layouts.
copy_tree "$ROOT/overlay/python" "$STAGE_ROOT/python/Lib"

# Avoid millions of WSL-to-Windows metadata writes.  Build the complete
# archive in the WSL filesystem, copy one file across the boundary, then use
# the native Windows tar implementation to extract it on the Windows volume.
tar -cf "$STAGE_TAR" -C "$STAGE_ROOT" .
RUNTIME_ARCHIVE="$RUNTIME_ROOT.tar"
cp "$STAGE_TAR" "$RUNTIME_ARCHIVE"
mkdir -p "$RUNTIME_ROOT"
WINDOWS_TAR=/mnt/w/Windows/System32/tar.exe
[ -x "$WINDOWS_TAR" ] || WINDOWS_TAR=/mnt/w/windows/system32/tar.exe
"$WINDOWS_TAR" -xf "$(wslpath -w "$RUNTIME_ARCHIVE")" \
  -C "$(wslpath -w "$RUNTIME_ROOT")"
rm -f "$RUNTIME_ARCHIVE"

printf 'built %s (%s bytes)\n' "$OUTPUT" "$(stat -c %s "$OUTPUT")"
printf 'runtime support: %s\n' "$RUNTIME_ROOT"
file "$OUTPUT"
