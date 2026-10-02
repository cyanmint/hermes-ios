#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/input"
printf '%s\n' '#!/bin/sh' 'printf "runtime_root=%s\\n" "$HERMES_RUNTIME_ROOT"' 'for arg in "$@"; do printf "arg=<%s>\\n" "$arg"; done' > "$TMP/input/hermes"
chmod 755 "$TMP/input/hermes"
printf 'test-runtime-zip' > "$TMP/input/hermesrt.zip"
bash "$ROOT/build/package-native-ios-deb.sh" \
    "$TMP/input/hermes" "$TMP/input/hermesrt.zip" "$TMP/hermes-ios.deb" 2.0.0+42
bash "$ROOT/build/generate-flat-apt-repo.sh" "$TMP/hermes-ios.deb" "$TMP/repo"

python3 - "$TMP/repo" <<'PY'
from __future__ import annotations

import bz2
import gzip
import hashlib
import lzma
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
packages = (root / "Packages").read_bytes()
for name, decoder in (
    ("Packages.gz", gzip.decompress),
    ("Packages.bz2", bz2.decompress),
    ("Packages.xz", lzma.decompress),
):
    assert decoder((root / name).read_bytes()) == packages, name

text = packages.decode()
fields = {}
for line in text.splitlines():
    if not line:
        break
    if line[:1].isspace():
        continue
    key, value = line.split(":", 1)
    fields[key] = value.strip()
deb = (root / "hermes-ios.deb").read_bytes()
assert fields["Package"] == "com.cyanmint.hermes-ios", fields
assert fields["Version"] == "2.0.0+42", fields
assert fields["Architecture"] == "iphoneos-arm64", fields
assert fields["Filename"] == "hermes-ios.deb", fields
assert fields["Size"] == str(len(deb)), fields
assert fields["MD5sum"] == hashlib.md5(deb).hexdigest(), fields
assert fields["SHA1"] == hashlib.sha1(deb).hexdigest(), fields
assert fields["SHA256"] == hashlib.sha256(deb).hexdigest(), fields

release = (root / "Release").read_text()
assert "Architectures: iphoneos-arm64\n" in release
assert "Components: .\n" in release
for name in ("Packages", "Packages.gz", "Packages.bz2", "Packages.xz"):
    data = (root / name).read_bytes()
    expected = f" {hashlib.sha256(data).hexdigest()} {len(data):16d} {name}"
    assert expected in release, name
print("Flat APT repository test passed: package stanza, checksums, compression, Release hashes")
PY

mkdir -p "$TMP/apt-state/lists/partial"
printf 'deb [trusted=yes arch=iphoneos-arm64] file:%s/ ./\n' "$TMP/repo" > "$TMP/sources.list"
apt-get \
    -o "Dir::Etc::sourcelist=$TMP/sources.list" \
    -o Dir::Etc::sourceparts=- \
    -o "Dir::State::lists=$TMP/apt-state/lists" \
    -o APT::Architecture=iphoneos-arm64 \
    -o APT::Architectures=iphoneos-arm64 \
    update
apt-cache \
    -o "Dir::Etc::sourcelist=$TMP/sources.list" \
    -o Dir::Etc::sourceparts=- \
    -o "Dir::State::lists=$TMP/apt-state/lists" \
    -o APT::Architecture=iphoneos-arm64 \
    -o APT::Architectures=iphoneos-arm64 \
    show com.cyanmint.hermes-ios | grep -F 'Version: 2.0.0+42'
printf 'APT flat-source integration test passed\n'
