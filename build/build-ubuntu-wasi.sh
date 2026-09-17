#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BUILD_ROOT=${BUILD_ROOT:-/root/hermes-build}
CPYTHON_REF=${CPYTHON_REF:-v3.13.9}
WASI_SDK_PATH=${WASI_SDK_PATH:?WASI_SDK_PATH is required}
WASMTIME_BIN=${WASMTIME_BIN:-wasmtime}
SQLITE_VERSION=${SQLITE_VERSION:-3450300}

rm -rf "$BUILD_ROOT/cpython" "$BUILD_ROOT/loader-build" \
  "$BUILD_ROOT/hermes-artifact" "$BUILD_ROOT/loader-artifact" \
  "$ROOT/build/external"
mkdir -p "$BUILD_ROOT" "$BUILD_ROOT/downloads" "$BUILD_ROOT/sqlite"

printf '==> cloning CPython %s\n' "$CPYTHON_REF"
git clone --depth 1 --branch "$CPYTHON_REF" https://github.com/python/cpython.git "$BUILD_ROOT/cpython"

printf '==> downloading SQLite %s\n' "$SQLITE_VERSION"
sqlite_zip="$BUILD_ROOT/downloads/sqlite-amalgamation-${SQLITE_VERSION}.zip"
curl --fail --location --retry 5 --retry-all-errors \
  "https://www.sqlite.org/2024/sqlite-amalgamation-${SQLITE_VERSION}.zip" \
  -o "$sqlite_zip"
rm -rf "$BUILD_ROOT/sqlite/amalgamation"
unzip -q "$sqlite_zip" -d "$BUILD_ROOT/sqlite"
cp "$BUILD_ROOT/sqlite/sqlite-amalgamation-${SQLITE_VERSION}/sqlite3.c" "$BUILD_ROOT/sqlite/sqlite3.c"
cp "$BUILD_ROOT/sqlite/sqlite-amalgamation-${SQLITE_VERSION}/sqlite3.h" "$BUILD_ROOT/sqlite/sqlite3.h"

printf '==> building CPython with the standard WASI SDK\n'
export WASI_SDK_PATH
export WASMTIME_BIN
export HERMES_AGENT_COMMIT=${HERMES_AGENT_COMMIT:-2246c245f51e03eb6a151d19119009156e84659a}
export HERMES_WEBUI_COMMIT=${HERMES_WEBUI_COMMIT:-e36f77389191fe9d81cd3a7416772e2f7b022e19}
FORCE_REBUILD=1 bash "$ROOT/build/build-local-wasi.sh"

printf '==> resolving pure-Python Hermes dependencies\n'
python3 -m pip install --disable-pip-version-check --quiet uv
requirements="$BUILD_ROOT/hermes-requirements.txt"
uv export --project "$ROOT/build/external/hermes-agent" --locked \
  --no-dev --no-editable --no-hashes --format requirements.txt \
  --output-file "$requirements"
sed -i '/^\.$/d' "$requirements"
wheelhouse="$BUILD_ROOT/pure-wheelhouse"
site_packages="$BUILD_ROOT/loader-artifact/lib/python3.13/site-packages"
rm -rf "$wheelhouse"
mkdir -p "$wheelhouse" "$site_packages"
python3 -m pip download --disable-pip-version-check --only-binary=:all: \
  --dest "$wheelhouse" -r "$requirements"
python3 - "$wheelhouse" "$site_packages" <<'PY'
import sys
import zipfile
from pathlib import Path

wheelhouse, destination = map(Path, sys.argv[1:])
for wheel in wheelhouse.glob("*.whl"):
    with zipfile.ZipFile(wheel) as archive:
        wheel_meta = next(
            name for name in archive.namelist() if name.endswith(".dist-info/WHEEL")
        )
        tags = [line.split(":", 1)[1].strip() for line in archive.read(wheel_meta).decode().splitlines() if line.startswith("Tag:")]
        if not any(tag.endswith("-none-any") for tag in tags):
            continue
        archive.extractall(destination)
        print(f"embedded {wheel.name}")
PY

mkdir -p "$BUILD_ROOT/hermes-artifact"
cp "$BUILD_ROOT/loader-build/cross-build/wasm32-wasip1/python.wasm" \
  "$BUILD_ROOT/hermes-artifact/python.wasm"
rm -rf "$BUILD_ROOT/hermes-artifact/lib"
cp -a "$BUILD_ROOT/loader-artifact/lib" "$BUILD_ROOT/hermes-artifact/lib"

printf '==> packaging hermes.wasm and hermesrt.zip\n'
SOURCE_ARTIFACT="$BUILD_ROOT/hermes-artifact" \
CPYTHON_SOURCE="$BUILD_ROOT/loader-build" \
bash "$ROOT/build/package-hermes.sh"

python3 - <<'PY'
from pathlib import Path
import zipfile

wasm = Path("hermes.wasm")
assert wasm.read_bytes()[:4] == b"\0asm"
with zipfile.ZipFile("hermesrt.zip") as archive:
    assert archive.testzip() is None
    names = archive.namelist()
    assert "encodings/__init__.py" in names
    assert "hermes_cli/main.py" in names
    assert not any(name.startswith("lib/python3.13/") for name in names)
print(f"hermes.wasm={wasm.stat().st_size}")
print(f"hermesrt.zip={Path('hermesrt.zip').stat().st_size}")
print(f"runtime_entries={len(names)}")
PY
