#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TARGET_ROOT=${1:?target CPython build directory}
OUTPUT=${2:?output executable}
BUILD_ROOT=$(dirname "$OUTPUT")
HOST_PYTHON=${HOST_PYTHON:-$(dirname "$TARGET_ROOT")/host-python/bin/python3.13}
ARCHIVE="$BUILD_ROOT/hermesrt.zip"
OPENSSL_INSTALL=${OPENSSL_INSTALL:-$(dirname "$BUILD_ROOT")/openssl-install}
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

[ -f "$TARGET_ROOT/libpython3.13.a" ] || { echo "missing target libpython3.13.a" >&2; exit 2; }
[ -d "$TARGET_ROOT/Lib/encodings" ] || { echo "missing CPython standard library" >&2; exit 2; }
HERMES_SOURCE=${HERMES_SOURCE:-$ROOT/build/external/hermes-agent}
WEBUI_SOURCE=${WEBUI_SOURCE:-$ROOT/build/external/hermes-webui}
[ -f "$HERMES_SOURCE/hermes_cli/main.py" ] || bash "$ROOT/build/fetch-sources.sh"
[ -f "$WEBUI_SOURCE/api/config.py" ] || { echo "missing Hermes WebUI source: $WEBUI_SOURCE" >&2; exit 2; }

mkdir -p "$STAGE/hermes" "$STAGE/hermes-webui" "$STAGE/python/site-packages"
cp -a "$TARGET_ROOT/Lib/." "$STAGE/python/"
for package in acp_adapter agent cron gateway hermes_cli plugins providers tools tui_gateway hermes; do
  [ -d "$HERMES_SOURCE/$package" ] && cp -a "$HERMES_SOURCE/$package" "$STAGE/hermes/"
done
cp -a "$HERMES_SOURCE"/*.py "$STAGE/hermes/" 2>/dev/null || true
cp -a "$ROOT/overlay/hermes/." "$STAGE/hermes/"
"$HOST_PYTHON" "$ROOT/overlay/patches/patch-ios-stability.py" "$STAGE/hermes"
"$HOST_PYTHON" "$ROOT/overlay/patches/patch-agent-sdk-compat.py" "$STAGE/hermes/agent/agent_init.py"
cp "$ROOT/overlay/hermes/agent/legacy_responses.py" "$STAGE/hermes/agent/legacy_responses.py"
VENDOR_ROOT=${HERMES_VENDOR:-$(dirname "$BUILD_ROOT")/vendor}
if [ "${HERMES_REFRESH_VENDOR:-1}" = "1" ]; then
  command -v uv >/dev/null 2>&1 || { echo "uv is required to vendor pure-Python dependencies" >&2; exit 2; }
  HOST_PYTHON=${HOST_PYTHON:-$(dirname "$TARGET_ROOT")/host-python/bin/python3.13}
  rm -rf "$VENDOR_ROOT"
  mkdir -p "$VENDOR_ROOT"
  uv pip install --target "$VENDOR_ROOT" --python "$HOST_PYTHON" \
    openai==1.3.8 pydantic==1.10.15 'httpx[socks]==0.28.1'
  uv pip install --target "$VENDOR_ROOT" --python "$HOST_PYTHON" --no-deps \
    certifi==2026.5.20 python-dotenv==1.2.2 fire==0.7.1 rich==14.3.3 \
    tenacity==9.1.4 pyyaml==6.0.3 ruamel.yaml==0.18.17 requests==2.33.0 \
    jinja2==3.1.6 prompt_toolkit==3.0.52 wcwidth==0.2.13 croniter==6.0.0 \
    snowballstemmer==3.1.1 packaging==26.0 Markdown==3.10.2 PyJWT==2.13.0 \
    urllib3==2.7.0 websockets==15.0.1 pathspec==1.1.1 python-multipart==0.0.20 \
    markdown-it-py==4.0.0 mdurl==0.1.2 pygments==2.19.2 charset-normalizer==3.4.4 \
    typing-extensions==4.15.0 tqdm==4.67.1 sniffio==1.3.1 socksio==1.0.0 \
    markupsafe==3.0.2 six==1.17.0 pytz==2025.2 python-dateutil==2.9.0.post0 \
    dulwich==0.22.8
  find "$VENDOR_ROOT" -type f -name '*.so' -delete
  if [ ! -f "$VENDOR_ROOT/openai/lib/__init__.py" ]; then
    : > "$VENDOR_ROOT/openai/lib/__init__.py"
  fi
fi
if [ -d "$VENDOR_ROOT" ]; then
  cp -a "$VENDOR_ROOT/." "$STAGE/python/site-packages/"
  if find "$STAGE/python/site-packages" -type f -name '*.so' -print -quit | grep -q .; then
    echo "native extensions must be statically linked; found .so in vendor" >&2
    exit 2
  fi
fi
cp -a "$WEBUI_SOURCE/api" "$STAGE/hermes-webui/"
cp -a "$WEBUI_SOURCE/static" "$STAGE/hermes-webui/" 2>/dev/null || true
for module in bootstrap.py server.py mcp_server.py; do
  [ -f "$WEBUI_SOURCE/$module" ] && cp "$WEBUI_SOURCE/$module" "$STAGE/hermes-webui/"
done
"$HOST_PYTHON" "$ROOT/overlay/patches/patch-webui-zip.py" "$STAGE/hermes-webui/api/config.py"
[ -d "$STAGE/hermes/plugins/browser" ] && : > "$STAGE/hermes/plugins/browser/__init__.py"
cp "$ROOT/overlay/python/sitecustomize.py" "$STAGE/python/sitecustomize.py"
cp -a "$ROOT/overlay" "$STAGE/overlay"
# Native CPython modules are required to be statically linked into libpython.

python3 - "$STAGE" "$ARCHIVE" <<'PY'
import os, sys, zipfile
root, output = sys.argv[1:]
with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as z:
    seen = set()
    for directory, _, names in os.walk(root):
        for name in sorted(names):
            source = os.path.join(directory, name)
            relative = os.path.relpath(source, root).replace(os.sep, '/')
            target = relative
            target = relative
            if '.git' in target.split('/'):
                continue
            if target in seen:
                raise SystemExit(f'duplicate runtime path: {target}')
            seen.add(target)
            z.write(source, target, compress_type=zipfile.ZIP_STORED)
PY

CC=${CC:-arm64-apple-ios-clang}
"$CC" -I"$TARGET_ROOT" -I"$TARGET_ROOT/Include" -I"$TARGET_ROOT" \
  -c "$ROOT/overlay/cpython/Programs/hermes_main.c" -o "$BUILD_ROOT/hermes_main.o"
"$CC" -mios-version-min="${IPHONEOS_DEPLOYMENT_TARGET:-13.0}" \
-Wl,-headerpad_max_install_names -Wl,-x -Wl,-no_function_starts -Wl,-no_data_in_code_info -Wl,-all_load "$TARGET_ROOT/libpython3.13.a" -Wl,-force_load,"$TARGET_ROOT/Modules/_hacl/libHacl_Hash_SHA2.a" -Wl,-force_load,"$TARGET_ROOT/Modules/expat/libexpat.a" "$BUILD_ROOT/hermes_main.o" \
  -Wl,-rpath,@loader_path -framework CoreFoundation -ldl -lpthread -lm -lz -lsqlite3 \
  -L"$OPENSSL_INSTALL/lib" -lssl -lcrypto "$TARGET_ROOT/ios_compat.o" \
  -o "$OUTPUT"
chmod 755 "$OUTPUT"
python3 - "$ARCHIVE" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    assert z.testzip() is None
    names = set(z.namelist())
    assert 'python/encodings/__init__.py' in names
    assert 'hermes/hermes_cli/main.py' in names
    assert not any(n.endswith(('.so', '.dylib', '.pyd', '.wasm')) for n in names)
PY
cp "$ARCHIVE" "$ROOT/hermesrt.zip"
