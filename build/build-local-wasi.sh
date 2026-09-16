#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
unset PYTHONHOME PYTHONPATH
SOURCE_ROOT=${SOURCE_ROOT:-"$ROOT/build/external"}
if [ "${SKIP_SOURCE_FETCH:-0}" = 1 ]; then
  mkdir -p "$SOURCE_ROOT/hermes-agent" "$SOURCE_ROOT/hermes-webui"
elif [ ! -d "$SOURCE_ROOT/hermes-agent/.git" ] || [ ! -d "$SOURCE_ROOT/hermes-webui/.git" ]; then
  "$ROOT/build/fetch-sources.sh" "$SOURCE_ROOT"
fi
cp -a "$ROOT/overlay/hermes/." "$SOURCE_ROOT/hermes-agent/"
cp -a "$ROOT/overlay/webui/." "$SOURCE_ROOT/hermes-webui/"
rm -rf /root/hermes-build/loader-build
mkdir -p /root/hermes-build/loader-build
cd /root/hermes-build/cpython
git archive HEAD | tar -x -C /root/hermes-build/loader-build
cp -a .git /root/hermes-build/loader-build/.git
cd /root/hermes-build/loader-build
mkdir -p Programs
cp "$ROOT/overlay/cpython/Programs/python.c" Programs/python.c
python -c 'from pathlib import Path; p=Path("configure"); s=p.read_text(); s=s.replace("--max-memory=10485760", "--max-memory=268435456").replace("--initial-memory=20971520", "--initial-memory=67108864"); p.write_text(s)'
cp /root/hermes-build/sqlite/sqlite3.c /root/hermes-build/sqlite/sqlite3.h Modules/_sqlite/
printf '%s\n' \
  '*disabled*' \
  '_socket socketmodule.c' \
  '_ssl _ssl.c' \
  '_sqlite3 _sqlite/blob.c _sqlite/connection.c _sqlite/cursor.c _sqlite/microprotocols.c _sqlite/module.c _sqlite/prepare_protocol.c _sqlite/row.c _sqlite/statement.c _sqlite/util.c _sqlite/sqlite3.c' \
  > Modules/Setup.local
export WASI_SDK_PATH=${WASI_SDK_PATH:-$("$ROOT/build/fetch-ashell-wasi-sdk.sh")}
SDK_CC="$WASI_SDK_PATH/bin/clang --target=wasm32-wasi"
SDK_AR="$WASI_SDK_PATH/bin/llvm-ar"
SDK_RANLIB="$WASI_SDK_PATH/bin/llvm-ranlib"
# CPython 3.13's WASI configure scripts still spell the target wasm32-wasi,
# while the a-Shell SDK exposes the finalized wasm32-wasip1 directory names.
# Add local compatibility aliases inside the selected SDK only.
SDK_SYSROOT="$WASI_SDK_PATH/share/wasi-sysroot"
if [ -d "$SDK_SYSROOT/lib/wasm32-wasip1" ]; then
  [ -L "$SDK_SYSROOT/lib/wasm32-wasi" ] && rm "$SDK_SYSROOT/lib/wasm32-wasi"
  [ -L "$SDK_SYSROOT/include/wasm32-wasi" ] && rm "$SDK_SYSROOT/include/wasm32-wasi"
  mkdir -p "$SDK_SYSROOT/lib/wasm32-wasi" "$SDK_SYSROOT/include/wasm32-wasi"
  for item in "$SDK_SYSROOT/lib/wasm32-wasip1"/*; do
    name=$(basename "$item")
    ln -sfn "../wasm32-wasip1/$name" "$SDK_SYSROOT/lib/wasm32-wasi/$name"
  done
  for item in "$SDK_SYSROOT/include/wasm32-wasip1"/*; do
    name=$(basename "$item")
    ln -sfn "../wasm32-wasip1/$name" "$SDK_SYSROOT/include/wasm32-wasi/$name"
  done
  [ -e "$SDK_SYSROOT/lib/wasm32-wasip1/crt1.o" ] || \
    ln -s crt1-command.o "$SDK_SYSROOT/lib/wasm32-wasip1/crt1.o"
fi
export PATH=/root/hermes-build/wasmtime-v48:$WASI_SDK_PATH/bin:$PATH
command -v wasmtime
ZLIB_ROOT=${ZLIB_ROOT:-/root/hermes-build/zlib-1.3.1}
if [ ! -f "$ZLIB_ROOT/zlib.h" ]; then
  mkdir -p /root/hermes-build/downloads
  curl -fsSL https://zlib.net/fossils/zlib-1.3.1.tar.gz | tar -xz -C /root/hermes-build
fi
mkdir -p /root/hermes-build/zlib-wasi
for source in adler32.c crc32.c deflate.c infback.c inffast.c inflate.c inftrees.c trees.c zutil.c; do
  clang --target=wasm32-wasip1 --sysroot="$WASI_SDK_PATH/share/wasi-sysroot" -O2 \
    -I"$ZLIB_ROOT" -c "$ZLIB_ROOT/$source" -o "/root/hermes-build/zlib-wasi/${source%.c}.o"
done
llvm-ar rcs /root/hermes-build/zlib-wasi/libz.a /root/hermes-build/zlib-wasi/*.o
mkdir -p /root/hermes-build/sqlite/lib
clang --target=wasm32-wasip1 --sysroot="$WASI_SDK_PATH/share/wasi-sysroot" \
  -DSQLITE_THREADSAFE=0 -DSQLITE_OMIT_LOAD_EXTENSION \
  -c /root/hermes-build/sqlite/sqlite3.c \
  -o /root/hermes-build/sqlite/sqlite3.o
llvm-ar rcs /root/hermes-build/sqlite/lib/libsqlite3.a /root/hermes-build/sqlite/sqlite3.o
unset CC AR RANLIB
python Tools/wasm/wasi.py configure-build-python --clean --quiet -- \
  --config-cache --without-ensurepip \
  py_cv_module__socket=n/a py_cv_module__ssl=n/a py_cv_module_zlib=n/a \
  ZLIB_CFLAGS= ZLIB_LIBS= \
  LIBSQLITE3_LIBS=-lsqlite3
python Tools/wasm/wasi.py make-build-python --quiet
printf '%s\n' \
  'zlib zlibmodule.c -I/root/hermes-build/zlib-1.3.1 /root/hermes-build/zlib-wasi/libz.a' \
  >> Modules/Setup.local
export CC="$SDK_CC"
export AR="$SDK_AR"
export RANLIB="$SDK_RANLIB"
export CFLAGS="${CFLAGS:-} --target=wasm32-wasi"
export LDFLAGS="${LDFLAGS:-} --target=wasm32-wasi"
python Tools/wasm/wasi.py configure-host --quiet -- \
  --config-cache --without-ensurepip \
  py_cv_module__socket=n/a py_cv_module__ssl=n/a \
  ZLIB_CFLAGS=-I/root/hermes-build/zlib-1.3.1 \
  ZLIB_LIBS=/root/hermes-build/zlib-wasi/libz.a \
  ac_cv_lib_sqlite3_sqlite3_bind_double=yes \
  ac_cv_lib_sqlite3_sqlite3_column_decltype=yes \
  ac_cv_lib_sqlite3_sqlite3_column_double=yes \
  ac_cv_lib_sqlite3_sqlite3_complete=yes \
  ac_cv_lib_sqlite3_sqlite3_load_extension=yes \
  ac_cv_lib_sqlite3_sqlite3_progress_handler=yes \
  ac_cv_lib_sqlite3_sqlite3_result_double=yes \
  ac_cv_lib_sqlite3_sqlite3_serialize=yes \
  ac_cv_lib_sqlite3_sqlite3_set_authorizer=yes \
  ac_cv_lib_sqlite3_sqlite3_trace=yes \
  ac_cv_lib_sqlite3_sqlite3_trace_v2=yes \
  ac_cv_lib_sqlite3_sqlite3_value_double=yes \
  LIBSQLITE3_CFLAGS=-I/root/hermes-build/sqlite \
  LIBSQLITE3_LIBS=/root/hermes-build/sqlite/lib/libsqlite3.a
if [[ "$WASI_SDK_PATH" == *ashell* ]]; then
  # The a-Shell SDK deliberately imports its host bridge during CPython's
  # --version self-test; ordinary Wasmtime cannot provide ashell_system.
  python Tools/wasm/wasi.py make-host --quiet || {
    [ -s /root/hermes-build/loader-build/cross-build/wasm32-wasip1/python.wasm ] || exit 1
    printf 'skipping Wasmtime self-test for a-Shell host module\n' >&2
  }
else
  python Tools/wasm/wasi.py make-host --quiet
fi

# Stage project-owned WASI Python files separately from the upstream CPython tree.
PYTHON_OVERLAY_DEST=${PYTHON_OVERLAY_DEST:-/root/hermes-build/loader-artifact/lib/python3.13}
mkdir -p "$PYTHON_OVERLAY_DEST"
cp -a "$ROOT/overlay/python/." "$PYTHON_OVERLAY_DEST/"
