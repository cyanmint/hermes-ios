#!/usr/bin/env bash
set -euo pipefail
umask 022

HERMES=${1:?usage: package-native-ios-deb.sh HERMES HERMESRT_ZIP OUTPUT VERSION}
RUNTIME=${2:?usage: package-native-ios-deb.sh HERMES HERMESRT_ZIP OUTPUT VERSION}
OUTPUT=${3:?usage: package-native-ios-deb.sh HERMES HERMESRT_ZIP OUTPUT VERSION}
VERSION=${4:?usage: package-native-ios-deb.sh HERMES HERMESRT_ZIP OUTPUT VERSION}

[[ -x "$HERMES" ]] || { echo "Hermes executable is missing or not executable: $HERMES" >&2; exit 2; }
[[ -s "$RUNTIME" ]] || { echo "Hermes runtime archive is missing or empty: $RUNTIME" >&2; exit 2; }
[[ "$VERSION" =~ ^[0-9][A-Za-z0-9.+:~_-]*$ ]] || { echo "invalid Debian package version: $VERSION" >&2; exit 2; }
command -v dpkg-deb >/dev/null 2>&1 || { echo "dpkg-deb is required to build the iOS package" >&2; exit 2; }

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
PACKAGE_ROOT="$STAGE/package"
mkdir -p "$PACKAGE_ROOT/DEBIAN" "$PACKAGE_ROOT/var/jb/usr/bin" "$PACKAGE_ROOT/var/jb/usr/libexec/hermes-ios"

install -m 755 "$HERMES" "$PACKAGE_ROOT/var/jb/usr/libexec/hermes-ios/hermes"
install -m 644 "$RUNTIME" "$PACKAGE_ROOT/var/jb/usr/libexec/hermes-ios/hermesrt.zip"

cat > "$PACKAGE_ROOT/var/jb/usr/bin/hermes" <<'WRAPPER'
#!/bin/sh
set -eu
export HERMES_RUNTIME_ROOT="${HERMES_RUNTIME_ROOT:-/var/jb/usr/libexec/hermes-ios}"
exec "$HERMES_RUNTIME_ROOT/hermes" "$@"
WRAPPER
chmod 755 "$PACKAGE_ROOT/var/jb/usr/bin/hermes"

cat > "$PACKAGE_ROOT/DEBIAN/control" <<CONTROL
Package: com.cyanmint.hermes-ios
Version: $VERSION
Architecture: iphoneos-arm64
Maintainer: cyanmint <cyanmint@cyanmint.net>
Depends: firmware (>= 13.0)
Homepage: https://github.com/cyanmint/hermes-ios
Description: Native Hermes Agent runtime for jailbroken arm64 iOS devices
 Installs the signed Hermes executable and its Python runtime under /var/jb.
CONTROL
chmod 644 "$PACKAGE_ROOT/DEBIAN/control"

mkdir -p "$(dirname "$OUTPUT")"
dpkg-deb --build --root-owner-group "$PACKAGE_ROOT" "$OUTPUT"
printf 'Built %s\n' "$OUTPUT"
