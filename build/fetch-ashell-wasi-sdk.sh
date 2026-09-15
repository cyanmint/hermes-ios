#!/usr/bin/env bash
set -euo pipefail

REPOSITORY=${WASI_SDK_REPOSITORY:-https://github.com/holzschu/wasi-sdk.git}
REF=${WASI_SDK_REF:-wasi-sdk-aShell-22}
SOURCE=${WASI_SDK_SOURCE:-/root/hermes-build/wasi-sdk-ashell-22-src}
PREFIX=${WASI_SDK_PATH:-/root/hermes-build/wasi-sdk-ashell-22}
EXPECTED_COMMIT=3d2154daab00d4479d855a661355566b2e393702

if [ ! -x "$PREFIX/bin/clang" ]; then
  if [ ! -d "$SOURCE/.git" ]; then
    rm -rf "$SOURCE"
    git clone --filter=blob:none --recurse-submodules --shallow-submodules \
      --depth 1 --branch "$REF" "$REPOSITORY" "$SOURCE"
  fi
  actual=$(git -C "$SOURCE" rev-parse HEAD)
  case "$actual" in
    "$EXPECTED_COMMIT"|3d2154daab00d4479d855a661355566b2e393702*) ;;
    *) printf 'unexpected a-Shell WASI SDK commit: %s\n' "$actual" >&2; exit 2 ;;
  esac
  env PREFIX="$PREFIX" make -C "$SOURCE"
  staged="$SOURCE/build/install$PREFIX"
  [ -x "$staged/bin/clang" ] || {
    printf 'missing staged a-Shell SDK clang: %s\n' "$staged/bin/clang" >&2
    exit 3
  }
  rm -rf "$PREFIX"
  mkdir -p "$(dirname "$PREFIX")"
  cp -a "$staged/." "$PREFIX/"
fi

actual=$(git -C "$SOURCE" rev-parse HEAD)
case "$actual" in
  "$EXPECTED_COMMIT"|3d2154daab00d4479d855a661355566b2e393702*) ;;
  *) printf 'unexpected a-Shell WASI SDK commit: %s\n' "$actual" >&2; exit 2 ;;
esac
[ -x "$PREFIX/bin/clang" ] || { printf 'missing a-Shell SDK clang: %s\n' "$PREFIX/bin/clang" >&2; exit 3; }
printf '%s\n' "$PREFIX"
