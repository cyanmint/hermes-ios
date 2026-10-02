#!/usr/bin/env bash
set -euo pipefail
umask 022

DEB=${1:?usage: generate-flat-apt-repo.sh HERMES_DEB OUTPUT_DIRECTORY}
OUTPUT_DIR=${2:?usage: generate-flat-apt-repo.sh HERMES_DEB OUTPUT_DIRECTORY}

[[ -s "$DEB" ]] || { echo "Debian package is missing or empty: $DEB" >&2; exit 2; }
command -v dpkg-deb >/dev/null 2>&1 || { echo "dpkg-deb is required to inspect the package" >&2; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required to generate APT metadata" >&2; exit 2; }

mkdir -p "$OUTPUT_DIR"
install -m 644 "$DEB" "$OUTPUT_DIR/hermes-ios.deb"
python3 - "$OUTPUT_DIR" <<'PY'
from __future__ import annotations

import bz2
import gzip
import hashlib
import lzma
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

root = Path(sys.argv[1])
deb = root / "hermes-ios.deb"
control = subprocess.run(
    ["dpkg-deb", "--field", str(deb)], check=True, capture_output=True
).stdout.decode("utf-8")
fields = {}
current = None
for line in control.splitlines():
    if line[:1].isspace() and current is not None:
        fields[current] += "\n" + line
    elif ":" in line:
        current, value = line.split(":", 1)
        fields[current] = value.lstrip()
    elif line:
        raise SystemExit(f"malformed Debian control line: {line!r}")

if fields.get("Package") != "com.cyanmint.hermes-ios":
    raise SystemExit(f"unexpected Debian package name: {fields.get('Package')!r}")
if fields.get("Architecture") != "iphoneos-arm64":
    raise SystemExit(f"unexpected Debian package architecture: {fields.get('Architecture')!r}")

payload = deb.read_bytes()
fields.update({
    "Filename": "hermes-ios.deb",
    "Size": str(len(payload)),
    "MD5sum": hashlib.md5(payload).hexdigest(),
    "SHA1": hashlib.sha1(payload).hexdigest(),
    "SHA256": hashlib.sha256(payload).hexdigest(),
})
priority = ("Package", "Version", "Architecture", "Maintainer", "Depends", "Filename", "Size", "MD5sum", "SHA1", "SHA256")
order = [name for name in priority if name in fields]
order.extend(name for name in fields if name not in priority)
packages = "\n".join(f"{name}: {fields[name]}" for name in order).encode("utf-8") + b"\n\n"
(root / "Packages").write_bytes(packages)
(root / "Packages.gz").write_bytes(gzip.compress(packages, compresslevel=9, mtime=0))
(root / "Packages.bz2").write_bytes(bz2.compress(packages, compresslevel=9))
(root / "Packages.xz").write_bytes(lzma.compress(packages, format=lzma.FORMAT_XZ, preset=9))

indexes = ("Packages", "Packages.gz", "Packages.bz2", "Packages.xz")
release = [
    "Origin: cyanmint",
    "Label: Hermes iOS",
    "Suite: v2-native",
    "Codename: v2-native",
    "Architectures: iphoneos-arm64",
    "Components: .",
    "Description: Native Hermes Agent for jailbroken iOS devices",
    "Date: " + datetime.now(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S UTC"),
    "SHA256:",
]
for name in indexes:
    data = (root / name).read_bytes()
    release.append(f" {hashlib.sha256(data).hexdigest()} {len(data):16d} {name}")
(root / "Release").write_text("\n".join(release) + "\n", encoding="utf-8", newline="\n")

for name in ("Packages", "Packages.gz", "Packages.bz2", "Packages.xz", "Release", "hermes-ios.deb"):
    path = root / name
    if not path.is_file() or path.stat().st_size == 0:
        raise SystemExit(f"failed to generate APT repository asset: {path}")
print("Generated flat APT repository:", ", ".join(name for name in ("Packages", "Packages.gz", "Packages.bz2", "Packages.xz", "Release", "hermes-ios.deb")))
PY
