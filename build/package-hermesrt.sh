#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SOURCE_DATE_EPOCH=${SOURCE_DATE_EPOCH:-$(git -C "$ROOT" log -1 --format=%ct)}
export SOURCE_DATE_EPOCH
BUILD_ROOT=${BUILD_ROOT:-/root/hermes-build/native-ios}
HOST_PYTHON=${HOST_PYTHON:-$BUILD_ROOT/host-python/bin/python3.13}
PYTHON_LIB_ROOT=${PYTHON_LIB_ROOT:-$BUILD_ROOT/host-python/lib/python3.13}
ARCHIVE=${1:-$ROOT/hermesrt.zip}
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

[ -x "$HOST_PYTHON" ] || { echo "missing host Python: $HOST_PYTHON" >&2; exit 2; }
[ -d "$PYTHON_LIB_ROOT/encodings" ] || { echo "missing Python standard library: $PYTHON_LIB_ROOT" >&2; exit 2; }
HERMES_SOURCE=${HERMES_SOURCE:-$ROOT/build/external/hermes-agent}
WEBUI_SOURCE=${WEBUI_SOURCE:-$ROOT/build/external/hermes-webui}
[ -f "$HERMES_SOURCE/hermes_cli/main.py" ] || bash "$ROOT/build/fetch-sources.sh"
[ -f "$WEBUI_SOURCE/api/config.py" ] || { echo "missing Hermes WebUI source: $WEBUI_SOURCE" >&2; exit 2; }

mkdir -p "$STAGE/hermes" "$STAGE/hermes-webui" "$STAGE/python/site-packages"
cp -a "$PYTHON_LIB_ROOT/." "$STAGE/python/"
for package in acp_adapter agent cron gateway hermes_cli plugins providers tools tui_gateway hermes; do
  [ -d "$HERMES_SOURCE/$package" ] && cp -a "$HERMES_SOURCE/$package" "$STAGE/hermes/"
done
cp -a "$HERMES_SOURCE"/*.py "$STAGE/hermes/" 2>/dev/null || true
cp -a "$ROOT/overlay/hermes/." "$STAGE/hermes/"
"$HOST_PYTHON" "$ROOT/build/verify-hermes-overlay.py" "$STAGE/hermes" "$STAGE/hermes/hermes_cli/doctor_state.py"
"$HOST_PYTHON" "$ROOT/overlay/patches/patch-ios-stability.py" "$STAGE/hermes"
"$HOST_PYTHON" "$ROOT/overlay/patches/patch-agent-sdk-compat.py" "$STAGE/hermes/agent/agent_init.py"
cp "$ROOT/overlay/hermes/agent/legacy_responses.py" "$STAGE/hermes/agent/legacy_responses.py"

VENDOR_ROOT=${HERMES_VENDOR:-$BUILD_ROOT/vendor}
if [ "${HERMES_REFRESH_VENDOR:-1}" = "1" ]; then
  command -v uv >/dev/null 2>&1 || { echo "uv is required to vendor dependencies" >&2; exit 2; }
  rm -rf "$VENDOR_ROOT"; mkdir -p "$VENDOR_ROOT"
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
    markupsafe==3.0.2 six==1.17.0 pytz==2025.2 python-dateutil==2.9.0.post0 dulwich==0.22.8
  find "$VENDOR_ROOT" -type f -name '*.so' -delete
fi
cp -a "$VENDOR_ROOT/." "$STAGE/python/site-packages/"
cp -a "$WEBUI_SOURCE/api" "$STAGE/hermes-webui/"
cp -a "$WEBUI_SOURCE/static" "$STAGE/hermes-webui/" 2>/dev/null || true
for module in bootstrap.py server.py mcp_server.py; do [ -f "$WEBUI_SOURCE/$module" ] && cp "$WEBUI_SOURCE/$module" "$STAGE/hermes-webui/"; done
"$HOST_PYTHON" "$ROOT/overlay/patches/patch-webui-zip.py" "$STAGE/hermes-webui/api/config.py"
[ -d "$STAGE/hermes/plugins/browser" ] && : > "$STAGE/hermes/plugins/browser/__init__.py"
cp "$ROOT/overlay/python/sitecustomize.py" "$STAGE/python/sitecustomize.py"
cp -a "$ROOT/overlay" "$STAGE/overlay"
# Host CPython's lib-dynload varies by Linux distro and is not usable on iOS.
find "$STAGE" \( -type f -o -type l \) \( -name '*.so' -o -name '*.pyc' -o -name '*.pyo' \) -delete
find "$STAGE" -type d -name __pycache__ -prune -exec rm -rf {} +

python3 - "$STAGE" "$ARCHIVE" <<'PY'
import os, sys, time, zipfile
root, output = sys.argv[1:]
epoch = int(os.environ.get("SOURCE_DATE_EPOCH", "315532800"))
fixed_time = time.gmtime(max(epoch, 315532800))[:6]
with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_STORED) as z:
    seen = set()
    for directory, dirs, names in os.walk(root):
        dirs.sort()
        for name in sorted(names):
            source = os.path.join(directory, name)
            target = os.path.relpath(source, root).replace(os.sep, '/')
            if '.git' in target.split('/') or target in seen: continue
            seen.add(target)
            info = zipfile.ZipInfo(target, date_time=fixed_time)
            info.create_system = 3
            info.external_attr = (0o100644 << 16)
            z.writestr(info, open(source, 'rb').read())
with zipfile.ZipFile(output) as z:
    assert z.testzip() is None
    names = set(z.namelist())
    assert 'python/encodings/__init__.py' in names
    assert 'hermes/hermes_cli/main.py' in names
    assert not any(n.endswith(('.so', '.dylib', '.pyd', '.wasm', '.pyc', '.pyo')) for n in names)
PY
printf 'built runtime: %s\n' "$ARCHIVE"
