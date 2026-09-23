#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BUILD_ROOT=${BUILD_ROOT:-/root/hermes-build/native-ios}
CPYTHON_REF=${CPYTHON_REF:-v3.13.9}
HOST_PYTHON=${HOST_PYTHON:-$BUILD_ROOT/host-python/bin/python3.13}
HOST_ROOT=$BUILD_ROOT/host-cpython
mkdir -p "$BUILD_ROOT"

if [ ! -x "$HOST_PYTHON" ]; then
  if [ ! -d "$HOST_ROOT/.git" ]; then
    git clone --filter=blob:none --depth=1 --branch "$CPYTHON_REF" https://github.com/python/cpython.git "$HOST_ROOT"
  fi
  if [ ! -f "$HOST_ROOT/Makefile" ]; then
    (cd "$HOST_ROOT" && ./configure --prefix="$BUILD_ROOT/host-python" --without-ensurepip --disable-test-modules)
  fi
  (cd "$HOST_ROOT" && make -j"${JOBS:-16}" && make install)
fi
"$HOST_PYTHON" --version
bash "$ROOT/build/package-hermesrt.sh" "${1:-$ROOT/hermesrt.zip}"
