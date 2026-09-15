#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SOURCE_ARTIFACT=${SOURCE_ARTIFACT:-/root/hermes-build/hermes-artifact}
OUTPUT=${OUTPUT:-"$ROOT/hermes.wasm"}
RUNTIME_ROOT=${RUNTIME_ROOT:-"$ROOT/hermes-runtime"}

SOURCE_WASM="$SOURCE_ARTIFACT/python.wasm"
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
mkdir -p "$RUNTIME_ROOT/lib/python3.13" "$RUNTIME_ROOT/python"
cp -a "$SOURCE_ARTIFACT/lib/." "$RUNTIME_ROOT/lib/"
cp -a "$SOURCE_ARTIFACT/python/." "$RUNTIME_ROOT/python/"
cp -a "$ROOT/overlay/hermes/." "$RUNTIME_ROOT/python/site-packages/"
rm -rf "$RUNTIME_ROOT/lib/python3.13/site-packages"
cp -a "$ROOT/overlay/python/." "$RUNTIME_ROOT/lib/python3.13/"

printf 'built %s (%s bytes)\n' "$OUTPUT" "$(stat -c %s "$OUTPUT")"
printf 'runtime support: %s\n' "$RUNTIME_ROOT"
file "$OUTPUT"
