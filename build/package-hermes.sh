#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
SOURCE_ARTIFACT=${SOURCE_ARTIFACT:-/root/hermes-build/hermes-artifact}
OUTPUT=${OUTPUT:-"$ROOT/hermes.wasm"}
RUNTIME_ARCHIVE=${RUNTIME_ARCHIVE:-"$ROOT/hermesrt.zip"}

SOURCE_WASM="$SOURCE_ARTIFACT/python.wasm"
CPYTHON_SOURCE=${CPYTHON_SOURCE:-/root/hermes-build/loader-build}
HERMES_SOURCE=${HERMES_SOURCE:-"$ROOT/build/external/hermes-agent"}
WEBUI_SOURCE=${WEBUI_SOURCE:-"$ROOT/build/external/hermes-webui"}

copy_tree() {
  local source=$1
  local destination=$2
  mkdir -p "$destination"
  (cd "$source" && tar -cf - .) | (cd "$destination" && tar -xf -)
}

STAGE_ROOT=$(mktemp -d /tmp/hermes-runtime.XXXXXX)
STAGE_ZIP=/tmp/hermes-runtime.${BASHPID}.zip
cleanup() {
  rm -rf "$STAGE_ROOT" "$STAGE_ZIP"
}
trap cleanup EXIT
[ -f "$SOURCE_WASM" ] || {
  printf 'missing WASM source artifact: %s\n' "$SOURCE_WASM" >&2
  exit 2
}
[ "$(od -An -tx1 -N4 "$SOURCE_WASM" | tr -d ' \n')" = "0061736d" ] || {
  printf 'source artifact is not a raw WASM module: %s\n' "$SOURCE_WASM" >&2
  exit 2
}
[ -f "$WEBUI_SOURCE/server.py" ] && [ -d "$WEBUI_SOURCE/api" ] && [ -d "$WEBUI_SOURCE/static" ] || {
  printf 'missing Hermes WebUI source: %s\n' "$WEBUI_SOURCE" >&2
  exit 5
}

# Delivery 1 is the WASM module itself. It must never be replaced by a shell
# launcher; loader.py is the only host-side process wrapper.
cp "$SOURCE_WASM" "$OUTPUT"
chmod +x "$OUTPUT"
[ "$(od -An -tx1 -N4 "$OUTPUT" | tr -d ' \n')" = "0061736d" ] || exit 2

# CPython still needs its standard library and project overlays at runtime.
# Keep those support files separate from the two named delivery entrypoints.
rm -rf "$ROOT/hermes-runtime" "$RUNTIME_ARCHIVE"
mkdir -p "$STAGE_ROOT/lib/python3.13/site-packages"
copy_tree "$SOURCE_ARTIFACT/lib" "$STAGE_ROOT/lib"
[ -d "$CPYTHON_SOURCE/Lib" ] || {
  printf 'missing CPython standard library: %s\n' "$CPYTHON_SOURCE/Lib" >&2
  exit 3
}
copy_tree "$CPYTHON_SOURCE/Lib" "$STAGE_ROOT/lib/python3.13"
# Keep the complete site-packages tree from the built runtime.  It contains
# Hermes' bundled dependencies; deleting it here leaves only the small set of
# source files copied below and produces a deceptively valid but unusable ZIP.
mkdir -p "$STAGE_ROOT/lib/python3.13/site-packages"
if [ -d "$HERMES_SOURCE/hermes_cli" ]; then
  for module in "$HERMES_SOURCE"/*.py; do
    [ -f "$module" ] || continue
    cp "$module" "$STAGE_ROOT/lib/python3.13/site-packages/"
  done
  # The built artifact may contain an older copy of these packages.  Overlay
  # every Hermes-owned runtime package from the pinned source checkout so
  # imports cannot mix versions (for example agent.proxy_bypass and
  # hermes_cli.main from different revisions).
  for package in acp_adapter agent cron gateway hermes_cli plugins providers tools tui_gateway hermes; do
    if [ -d "$HERMES_SOURCE/$package" ]; then
      copy_tree "$HERMES_SOURCE/$package" "$STAGE_ROOT/lib/python3.13/site-packages/$package"
    fi
  done
else
  printf 'missing Hermes Agent source: %s\n' "$HERMES_SOURCE/hermes_cli" >&2
  exit 4
fi
copy_tree "$ROOT/overlay/hermes" "$STAGE_ROOT/lib/python3.13/site-packages"
# The WASM now provides CPython's built-in zlib extension.  Remove any stale
# source fallback from an older Hermes checkout so it cannot shadow the
# built-in module and hide the real compression implementation.
rm -f "$STAGE_ROOT/lib/python3.13/site-packages/zlib.py"
copy_tree "$WEBUI_SOURCE/api" "$STAGE_ROOT/lib/python3.13/api"
copy_tree "$WEBUI_SOURCE/static" "$STAGE_ROOT/lib/python3.13/static"
for module in bootstrap.py server.py mcp_server.py; do
  [ -f "$WEBUI_SOURCE/$module" ] && cp "$WEBUI_SOURCE/$module" "$STAGE_ROOT/lib/python3.13/$module"
done
copy_tree "$ROOT/overlay/python" "$STAGE_ROOT/lib/python3.13"
# ``ssl.py`` imports the top-level ``_ssl`` module.  Keep it synchronized with
# the maintained WASI facade instead of allowing a stale artifact copy to win.
cp "$ROOT/overlay/python/wasi_runtime/_ssl.py" "$STAGE_ROOT/lib/python3.13/_ssl.py"
# CPython's WASI importlib.metadata cannot discover nested dist-info entries
# inside the runtime ZIP.  prompt_toolkit imports its version at module import
# time, so replace that metadata lookup with the version from the staged
# dist-info record while preserving the upstream package source.
PROMPT_TOOLKIT_INIT="$STAGE_ROOT/lib/python3.13/site-packages/prompt_toolkit/__init__.py"
if [ -f "$PROMPT_TOOLKIT_INIT" ]; then
  python3 - "$STAGE_ROOT/lib/python3.13/site-packages" "$PROMPT_TOOLKIT_INIT" <<'PY'
import re
import sys
from pathlib import Path

site_packages = Path(sys.argv[1])
init_path = Path(sys.argv[2])
metadata_path = next(site_packages.glob("prompt_toolkit-*.dist-info/METADATA"), None)
if metadata_path is None:
    raise SystemExit("missing prompt_toolkit dist-info metadata")
metadata = metadata_path.read_text(encoding="utf-8")
match = re.search(r"^Version:\s*(\S+)\s*$", metadata, re.MULTILINE)
if match is None:
    raise SystemExit("missing prompt_toolkit version metadata")
version = match.group(1)
source = init_path.read_text(encoding="utf-8")
old = '__version__ = metadata.version("prompt_toolkit")'
new = f'__version__ = {version!r}'
if old not in source:
    raise SystemExit("unexpected prompt_toolkit __init__.py")
init_path.write_text(source.replace(old, new), encoding="utf-8", newline="\n")
PY
fi
# The a-Shell WASI process has a small thread budget.  Avoid optional Hermes
# background workers during CLI startup; the interactive command itself stays
# synchronous and does not need these maintenance threads.
CLI_PATH="$STAGE_ROOT/lib/python3.13/site-packages/cli.py"
[ -f "$CLI_PATH" ] || CLI_PATH="$STAGE_ROOT/lib/python3.13/cli.py"
python3 - "$STAGE_ROOT/lib/python3.13/site-packages/hermes_cli/main.py" "$CLI_PATH" "$STAGE_ROOT/lib/python3.13/site-packages/hermes_cli/cli_info_mixin.py" "$STAGE_ROOT/lib/python3.13/site-packages/prompt_toolkit/patch_stdout.py" "$STAGE_ROOT/lib/python3.13/site-packages/hermes_cli/cli_status_bar_mixin.py" "$STAGE_ROOT/lib/python3.13/server.py" <<'PY'
import sys
import re
from pathlib import Path

main_path, cli_path, info_path, patch_stdout_path, status_path, server_path = map(Path, sys.argv[1:])
main = main_path.read_text(encoding="utf-8")
old_main = "    if not _is_tui_chat_launch(args):\n"
new_main = "    if not _is_tui_chat_launch(args) and os.environ.get(\"HERMES_DISABLE_BACKGROUND_DISCOVERY\") != \"1\":\n"
if old_main in main:
    main = main.replace(old_main, new_main, 1)
main_path.write_text(main, encoding="utf-8", newline="\n")

cli = cli_path.read_text(encoding="utf-8")

old_banner = "                threading.Thread(\n                    target=_refresh_banner_snapshot, name=\"banner-snapshot-refresh\", daemon=True,\n                ).start()\n"
new_banner = "                if os.environ.get(\"HERMES_DISABLE_STARTUP_PREWARM\") != \"1\":\n                    threading.Thread(\n                        target=_refresh_banner_snapshot, name=\"banner-snapshot-refresh\", daemon=True,\n                    ).start()\n"
info = info_path.read_text(encoding="utf-8")
if old_banner in info:
    info = info.replace(old_banner, new_banner, 1)
info_path.write_text(info, encoding="utf-8", newline="\n")
old_cli = '    threading.Thread(target=auto_prune_from_config, name="checkpoint-auto-prune", daemon=True).start()\n'
new_cli = '    if os.environ.get("HERMES_DISABLE_BACKGROUND_CHECKPOINTS") != "1":\n        threading.Thread(target=auto_prune_from_config, name="checkpoint-auto-prune", daemon=True).start()\n'
if old_cli in cli:
    cli = cli.replace(old_cli, new_cli, 1)
for thread_name, label in [
    ("_tui_spinner_loop", "HERMES_DISABLE_TUI_SPINNER"),
    ("_tui_process_loop", "HERMES_DISABLE_TUI_THREADS"),
]:
    pattern = rf'(?m)^(?P<indent>[ ]*)threading\.Thread\(target=self\.{re.escape(thread_name)}, daemon=True\)\.start\(\s*\)$'
    replacement = rf'\g<indent>if os.environ.get("{label}") != "1":\n\g<indent>    threading.Thread(target=self.{thread_name}, daemon=True).start()'
    cli, count = re.subn(pattern, replacement, cli, count=1)
    pass
pattern = r'(?m)^(?P<indent>[ ]*)threading\.Thread\(target=self\._tui_wake_startup, daemon=True, name="wake-startup"\)\.start\(\s*\)$'
replacement = r'\g<indent>if os.environ.get("HERMES_DISABLE_TUI_THREADS") != "1":\n\g<indent>    threading.Thread(target=self._tui_wake_startup, daemon=True, name="wake-startup").start()'
cli, count = re.subn(pattern, replacement, cli, count=1)
pass
cli_path.write_text(cli, encoding="utf-8", newline="\n")
patch_stdout = patch_stdout_path.read_text(encoding="utf-8")
old_flush_start = "        thread.start()\n        return thread\n"
new_flush_start = "        try:\n            thread.start()\n        except RuntimeError:\n            class _NoopThread:\n                def join(self):\n                    return None\n            return _NoopThread()\n        return thread\n"
if old_flush_start in patch_stdout:
    patch_stdout = patch_stdout.replace(old_flush_start, new_flush_start, 1)
patch_stdout_path.write_text(patch_stdout, encoding="utf-8", newline="\n")
status = status_path.read_text(encoding="utf-8")
old_pet_start = "        self._pet_anim_thread.start()\n"
new_pet_start = "        try:\n            self._pet_anim_thread.start()\n        except RuntimeError:\n            self._pet_anim_thread = None\n"
if old_pet_start in status:
    status = status.replace(old_pet_start, new_pet_start, 1)
old_pet_join = "        if thread is not None:\n            thread.join(timeout=0.3)\n"
new_pet_join = "        if thread is not None:\n            try:\n                thread.join(timeout=0.3)\n            except RuntimeError:\n                pass\n"
if old_pet_join in status:
    status = status.replace(old_pet_join, new_pet_join, 1)
status_path.write_text(status, encoding="utf-8", newline="\n")
server = server_path.read_text(encoding="utf-8")
server = server.replace(
    "from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer",
    "from http.server import BaseHTTPRequestHandler, HTTPServer",
    1,
)
server = server.replace("class QuietHTTPServer(ThreadingHTTPServer):", "class QuietHTTPServer(HTTPServer):", 1)
server_path.write_text(server, encoding="utf-8", newline="\n")
# Source snapshots place the TUI entrypoint in different locations. Apply the
# thread-budget guards once more over the final staging tree so no layout
# variant can reintroduce an unconditional worker.
import re
for candidate in Path(sys.argv[1]).rglob("*.py"):
    text = candidate.read_text(encoding="utf-8")
    original = text
    text = re.sub(
        r'(?m)^(?P<i>[ ]*)threading\.Thread\(target=_prewarm_agent_runtime, name="agent-runtime-prewarm", daemon=True\)\.start\(\)\s*$',
        r'\g<i>if os.environ.get("HERMES_DISABLE_STARTUP_PREWARM") != "1":\n\g<i>    threading.Thread(target=_prewarm_agent_runtime, name="agent-runtime-prewarm", daemon=True).start()',
        text,
    )
    text = re.sub(
        r'(?m)^(?P<i>[ ]*)threading\.Thread\(target=self\._tui_(spinner_loop|process_loop), daemon=True\)\.start\(\s*\)$',
        r'\g<i>if os.environ.get("HERMES_DISABLE_TUI_THREADS") != "1":\n\g<i>    threading.Thread(target=self._tui_\1, daemon=True).start()',
        text,
    )
    text = re.sub(
        r'(?ms)^(?P<i>[ ]*)threading\.Thread\(\n(?P<body>.*?target=_refresh_banner_snapshot.*?\n\s*\)\.start\(\))',
        r'\g<i>if os.environ.get("HERMES_DISABLE_STARTUP_PREWARM") != "1":\n\g<i>    threading.Thread(\n\g<body>\n\g<i>    )',
        text,
        count=1,
    )
    if text != original:
        candidate.write_text(text, encoding="utf-8", newline="\n")
PY

# Build the complete zip in WSL and copy one file across the Windows
# filesystem boundary.  The archive is also CPython's import path.
# ZIP_STORED keeps startup independent of archive decompression; zlib is also
# linked into the WASM for Python's runtime compression APIs.
python3 - "$STAGE_ROOT" "$STAGE_ZIP" <<'PY'
import os
import sys
import zipfile

root, output = sys.argv[1:]
prefixes = ("lib/python3.13/site-packages/", "lib/python3.13/")
targets = {}
for directory, _, names in os.walk(root):
    for name in names:
        source = os.path.join(directory, name)
        relative = os.path.relpath(source, root).replace(os.sep, "/")
        target = relative
        for prefix in prefixes:
            if relative.startswith(prefix):
                target = relative[len(prefix):]
                break
        previous = targets.get(target)
        if previous is not None:
            raise SystemExit(f"runtime archive path collision: {target}: {previous} and {source}")
        targets[target] = source

with zipfile.ZipFile(output, "w", zipfile.ZIP_STORED) as archive:
    for target, source in sorted(targets.items()):
        archive.write(source, target, compress_type=zipfile.ZIP_STORED)
PY
# Apply the final source-independent guard to the archive entries actually
# imported at runtime; the dependency ZIP may contain an older Hermes copy.
python3 - "$STAGE_ZIP" <<'PY'
import sys, zipfile
path = sys.argv[1]
old = b'                threading.Thread(\n                    target=_refresh_banner_snapshot, name="banner-snapshot-refresh", daemon=True,\n                ).start()'
new = b'                if os.environ.get("HERMES_DISABLE_STARTUP_PREWARM") != "1":\n                    threading.Thread(\n                        target=_refresh_banner_snapshot, name="banner-snapshot-refresh", daemon=True,\n                    ).start()'
with zipfile.ZipFile(path, "r") as source:
    entries = [(info, source.read(info.filename)) for info in source.infolist()]
with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as output:
    for info, data in entries:
        if info.filename == "hermes_cli/cli_info_mixin.py":
            data = data.replace(old, new, 1)
        data = data.replace(b'if os.environ.get("HERMES_DISABLE_STARTUP_PREWARM") != "1":', b'if False:')
        data = data.replace(b'if os.environ.get("HERMES_DISABLE_TUI_THREADS") != "1":', b'if False:')
        data = data.replace(b'if os.environ.get("HERMES_DISABLE_TUI_SPINNER") != "1":', b'if False:')
        data = data.replace(b'if os.environ.get("HERMES_DEFER_AGENT_STARTUP") != "1":', b'if False:')
        if info.filename == "bootstrap.py":
            text = data.decode("utf-8")
            for newline in ("\r\r\n", "\r\n", "\n"):
                marker = "def discover_agent_dir() -> Path | None:" + newline
                embedded = marker + "    if REPO_ROOT.name == \"hermesrt.zip\":" + newline + "        return REPO_ROOT" + newline
                if marker in text:
                    text = text.replace(marker, embedded, 1)
                    break
            data = text.encode("utf-8")
        if info.filename == "api/config.py":
            text = data.decode("utf-8")
            for newline in ("\r\r\n", "\r\n", "\n"):
                marker = "def _discover_agent_dir() -> Path:" + newline
                embedded = marker + "    if REPO_ROOT.name == \"hermesrt.zip\":" + newline + "        return REPO_ROOT" + newline
                if marker in text:
                    text = text.replace(marker, embedded, 1)
                    break
            data = text.encode("utf-8")
        if info.filename == "server.py":
            text = data.decode("utf-8").replace("\r\n", "\n")
            start = text.find("    try:\n        from api.gateway_watcher import start_watcher")
            end = text.find("    try:\n        from api.plugins import load_plugins", start)
            if start >= 0 and end > start:
                text = text[:start] + "    # Optional background workers are disabled in constrained WASI hosts.\n\n" + text[end:]
            data = text.encode("utf-8")
        output.writestr(info.filename, data)
PY
cp "$STAGE_ZIP" "$RUNTIME_ARCHIVE"

printf 'built %s (%s bytes)\n' "$OUTPUT" "$(stat -c %s "$OUTPUT")"
printf 'runtime archive: %s\n' "$RUNTIME_ARCHIVE"
file "$OUTPUT"
