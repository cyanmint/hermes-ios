#!/usr/bin/env bash
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
SOURCE_ROOT=${SOURCE_ROOT:-"$ROOT/build/sources"}
if [ ! -d "$SOURCE_ROOT/hermes-agent/.git" ] || [ ! -d "$SOURCE_ROOT/hermes-webui/.git" ]; then
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
python -c 'from pathlib import Path; p=Path("configure"); s=p.read_text(); s=s.replace("--max-memory=10485760", "--max-memory=268435456").replace("--initial-memory=20971520", "--initial-memory=67108864"); p.write_text(s)'
cp /root/hermes-build/sqlite/sqlite3.c /root/hermes-build/sqlite/sqlite3.h Modules/_sqlite/
printf '%s\n' \
  '*disabled*' \
  '_socket socketmodule.c' \
  '_ssl _ssl.c' \
  '_sqlite3 _sqlite/blob.c _sqlite/connection.c _sqlite/cursor.c _sqlite/microprotocols.c _sqlite/module.c _sqlite/prepare_protocol.c _sqlite/row.c _sqlite/statement.c _sqlite/util.c _sqlite/sqlite3.c' \
  > Modules/Setup.local
export WASI_SDK_PATH=/root/hermes-build/wasi-sdk-25.0-x86_64-linux
export PATH=/root/hermes-build/wasmtime-v48:$WASI_SDK_PATH/bin:$PATH
command -v wasmtime
mkdir -p /root/hermes-build/sqlite/lib
clang --target=wasm32-wasi --sysroot="$WASI_SDK_PATH/share/wasi-sysroot" \
  -DSQLITE_THREADSAFE=0 -DSQLITE_OMIT_LOAD_EXTENSION \
  -c /root/hermes-build/sqlite/sqlite3.c \
  -o /root/hermes-build/sqlite/sqlite3.o
llvm-ar rcs /root/hermes-build/sqlite/lib/libsqlite3.a /root/hermes-build/sqlite/sqlite3.o
python Tools/wasm/wasi.py configure-build-python --clean --quiet -- \
  --config-cache --without-ensurepip \
  py_cv_module__socket=n/a py_cv_module__ssl=n/a \
  LIBSQLITE3_LIBS=-lsqlite3
python Tools/wasm/wasi.py make-build-python --quiet
python Tools/wasm/wasi.py configure-host --quiet -- \
  --config-cache --without-ensurepip \
  py_cv_module__socket=n/a py_cv_module__ssl=n/a \
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
python Tools/wasm/wasi.py make-host --quiet

# Stage project-owned WASI Python files separately from the upstream CPython tree.
PYTHON_OVERLAY_DEST=${PYTHON_OVERLAY_DEST:-/root/hermes-build/loader-artifact/lib/python3.13}
mkdir -p "$PYTHON_OVERLAY_DEST"
cp -a "$ROOT/overlay/python/." "$PYTHON_OVERLAY_DEST/"
