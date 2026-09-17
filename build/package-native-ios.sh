#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TARGET_ROOT=${1:?target CPython build directory}
OUTPUT=${2:?output executable}
BUILD_ROOT=$(dirname "$OUTPUT")
ARCHIVE="$BUILD_ROOT/hermesrt.zip"
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT

[ -f "$TARGET_ROOT/libpython3.13.a" ] || { echo "missing target libpython3.13.a" >&2; exit 2; }
[ -d "$TARGET_ROOT/Lib/encodings" ] || { echo "missing CPython standard library" >&2; exit 2; }
[ -d "$ROOT/build/external/hermes-agent/hermes_cli" ] || "$ROOT/build/fetch-sources.sh"
HERMES_SOURCE=${HERMES_SOURCE:-$ROOT/build/external/hermes-agent}
WEBUI_SOURCE=${WEBUI_SOURCE:-$ROOT/build/external/hermes-webui}

mkdir -p "$STAGE/lib/python3.13/site-packages"
cp -a "$TARGET_ROOT/Lib/." "$STAGE/lib/python3.13/"
for package in acp_adapter agent cron gateway hermes_cli plugins providers tools tui_gateway hermes; do
  [ -d "$HERMES_SOURCE/$package" ] && cp -a "$HERMES_SOURCE/$package" "$STAGE/lib/python3.13/site-packages/"
done
cp -a "$HERMES_SOURCE"/*.py "$STAGE/lib/python3.13/site-packages/" 2>/dev/null || true
if [ -d "$WEBUI_SOURCE/api" ]; then
  cp -a "$WEBUI_SOURCE/api" "$STAGE/lib/python3.13/"
  cp -a "$WEBUI_SOURCE/static" "$STAGE/lib/python3.13/" 2>/dev/null || true
  for module in bootstrap.py server.py mcp_server.py; do
    [ -f "$WEBUI_SOURCE/$module" ] && cp "$WEBUI_SOURCE/$module" "$STAGE/lib/python3.13/"
  done
fi
cp "$ROOT/native/sitecustomize.py" "$STAGE/lib/python3.13/sitecustomize.py"
# CPython's iOS build emits native extensions as Mach-O shared modules.  They
# remain inside the single runtime archive; sitecustomize extracts them to a
# private temporary directory before Hermes imports the Agent.
find "$TARGET_ROOT/Modules" -maxdepth 1 -type f -name '*.iphoneos.so' \
  -exec cp {} "$STAGE/lib/python3.13/site-packages/" \; 2>/dev/null || true
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
            for prefix in ('lib/python3.13/site-packages/', 'lib/python3.13/'):
                if relative.startswith(prefix):
                    target = relative[len(prefix):]
                    break
            if target in seen:
                raise SystemExit(f'duplicate runtime path: {target}')
            seen.add(target)
            z.write(source, target, compress_type=zipfile.ZIP_STORED)
PY

CC=${CC:-arm64-apple-ios-clang}
"$CC" -I"$TARGET_ROOT" -I"$TARGET_ROOT/Include" -I"$TARGET_ROOT" \
  -c "$ROOT/native/hermes_main.c" -o "$BUILD_ROOT/hermes_main.o"
"$CC" -mios-version-min="${IPHONEOS_DEPLOYMENT_TARGET:-13.0}" \
  -Wl,-all_load "$TARGET_ROOT/libpython3.13.a" "$BUILD_ROOT/hermes_main.o" \
  -framework CoreFoundation -ldl -lpthread -lm "$TARGET_ROOT/ios_compat.o" \
  -o "$OUTPUT"
chmod 755 "$OUTPUT"
python3 - "$ARCHIVE" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as z:
    assert z.testzip() is None
    names = set(z.namelist())
    assert 'encodings/__init__.py' in names
    assert 'hermes_cli/main.py' in names
    assert not any(n.endswith(('.so', '.dylib', '.pyd')) for n in names)
PY
cp "$ARCHIVE" "$ROOT/hermesrt.zip"
