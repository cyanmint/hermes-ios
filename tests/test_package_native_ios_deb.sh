#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PACKAGE_SCRIPT="$ROOT/build/package-native-ios-deb.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

mkdir -p "$TMP/input"
printf '%s\n' '#!/bin/sh' 'printf "runtime_root=%s\\n" "$HERMES_RUNTIME_ROOT"' 'for arg in "$@"; do printf "arg=<%s>\\n" "$arg"; done' > "$TMP/input/hermes"
chmod 755 "$TMP/input/hermes"
printf 'test-runtime-zip' > "$TMP/input/hermesrt.zip"
bash "$PACKAGE_SCRIPT" "$TMP/input/hermes" "$TMP/input/hermesrt.zip" "$TMP/hermes-ios.deb" 2.0.0+42

[[ "$(dpkg-deb -f "$TMP/hermes-ios.deb" Package)" == com.cyanmint.hermes-ios ]]
[[ "$(dpkg-deb -f "$TMP/hermes-ios.deb" Version)" == 2.0.0+42 ]]
[[ "$(dpkg-deb -f "$TMP/hermes-ios.deb" Architecture)" == iphoneos-arm64 ]]
[[ "$(dpkg-deb -f "$TMP/hermes-ios.deb" Maintainer)" == 'cyanmint <cyanmint@cyanmint.net>' ]]
[[ "$(dpkg-deb -f "$TMP/hermes-ios.deb" Depends)" == 'firmware (>= 13.0)' ]]
dpkg-deb --extract "$TMP/hermes-ios.deb" "$TMP/extracted"

[[ -x "$TMP/extracted/var/jb/usr/bin/hermes" ]]
[[ -x "$TMP/extracted/var/jb/usr/libexec/hermes-ios/hermes" ]]
cmp "$TMP/input/hermes" "$TMP/extracted/var/jb/usr/libexec/hermes-ios/hermes"
cmp "$TMP/input/hermesrt.zip" "$TMP/extracted/var/jb/usr/libexec/hermes-ios/hermesrt.zip"
grep -Fq 'export HERMES_RUNTIME_ROOT="${HERMES_RUNTIME_ROOT:-/var/jb/usr/libexec/hermes-ios}"' "$TMP/extracted/var/jb/usr/bin/hermes"
grep -Fq 'exec "$HERMES_RUNTIME_ROOT/hermes" "$@"' "$TMP/extracted/var/jb/usr/bin/hermes"
wrapper_output=$(HERMES_RUNTIME_ROOT="$TMP/extracted/var/jb/usr/libexec/hermes-ios" \
    "$TMP/extracted/var/jb/usr/bin/hermes" --version probe)
expected_output=$(printf 'runtime_root=%s\narg=<--version>\narg=<probe>' "$TMP/extracted/var/jb/usr/libexec/hermes-ios")
[[ "$wrapper_output" == "$expected_output" ]]
printf 'Debian package test passed\n'
