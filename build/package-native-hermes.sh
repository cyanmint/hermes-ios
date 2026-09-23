#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TARGET_ROOT=${1:?target CPython build directory}
OUTPUT=${2:-$ROOT/hermes}
BUILD_ROOT=$(dirname "$OUTPUT")
OPENSSL_INSTALL=${OPENSSL_INSTALL:-$(dirname "$BUILD_ROOT")/openssl-install}
CC=${CC:-arm64-apple-ios-clang}

[ -f "$TARGET_ROOT/libpython3.13.a" ] || { echo "missing target libpython3.13.a" >&2; exit 2; }
[ -f "$TARGET_ROOT/ios_compat.o" ] || { echo "missing target ios_compat.o" >&2; exit 2; }
"$CC" -I"$TARGET_ROOT" -I"$TARGET_ROOT/Include" -c "$ROOT/overlay/cpython/Programs/hermes_main.c" -o "$BUILD_ROOT/hermes_main.o"
"$CC" -mios-version-min="${IPHONEOS_DEPLOYMENT_TARGET:-13.0}" \
  -Wl,-headerpad_max_install_names -Wl,-x -Wl,-no_function_starts \
  -Wl,-no_data_in_code_info -Wl,-all_load "$TARGET_ROOT/libpython3.13.a" \
  -Wl,-force_load,"$TARGET_ROOT/Modules/_hacl/libHacl_Hash_SHA2.a" \
  -Wl,-force_load,"$TARGET_ROOT/Modules/expat/libexpat.a" "$BUILD_ROOT/hermes_main.o" \
  -Wl,-rpath,@loader_path -framework CoreFoundation -ldl -lpthread -lm -lz -lsqlite3 \
  -L"$OPENSSL_INSTALL/lib" -lssl -lcrypto "$TARGET_ROOT/ios_compat.o" -o "$OUTPUT"
chmod 755 "$OUTPUT"
file "$OUTPUT"
