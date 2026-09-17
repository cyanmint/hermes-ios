#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
BUILD_ROOT=${BUILD_ROOT:-/root/hermes-build/native-ios}
SDK_VERSION=${IOS_SDK_VERSION:-16.5}
DEPLOYMENT_TARGET=${IPHONEOS_DEPLOYMENT_TARGET:-13.0}
SDK_REPO=${IOS_SDK_REPOSITORY:-https://github.com/theos/sdks.git}
SDK_ROOT=${IOS_SDK_ROOT:-$BUILD_ROOT/sdks/iPhoneOS${SDK_VERSION}.sdk}
CPYTHON_REF=${CPYTHON_REF:-v3.13.9}
CPYTHON_ROOT=${CPYTHON_ROOT:-$BUILD_ROOT/cpython}
HOST_PYTHON=${HOST_PYTHON:-$BUILD_ROOT/host-python/python}
TARGET_ROOT=${TARGET_ROOT:-$BUILD_ROOT/target}
TOOLBIN=$BUILD_ROOT/bin

case "$(uname -s)" in Linux) ;; *) echo 'native iOS build must run in WSL/Linux' >&2; exit 2;; esac
mkdir -p "$BUILD_ROOT" "$TOOLBIN"

if [ ! -d "$SDK_ROOT" ]; then
  SDK_REPO_DIR=$BUILD_ROOT/sdks
  if [ ! -d "$SDK_REPO_DIR/.git" ]; then
    git clone --filter=blob:none --sparse --depth=1 "$SDK_REPO" "$SDK_REPO_DIR"
    git -C "$SDK_REPO_DIR" sparse-checkout set "iPhoneOS${SDK_VERSION}.sdk"
  fi
fi
[ -d "$SDK_ROOT/usr/include" ] || { echo "missing iOS SDK: $SDK_ROOT" >&2; exit 3; }

if [ ! -d "$CPYTHON_ROOT/.git" ]; then
  git clone --filter=blob:none --depth=1 --branch "$CPYTHON_REF" https://github.com/python/cpython.git "$CPYTHON_ROOT"
fi

if [ ! -x "$HOST_PYTHON" ]; then
  HOST_ROOT=$BUILD_ROOT/host-cpython
  if [ ! -d "$HOST_ROOT/.git" ]; then
    git clone --filter=blob:none --depth=1 --branch "$CPYTHON_REF" https://github.com/python/cpython.git "$HOST_ROOT"
    (cd "$HOST_ROOT" && ./configure --prefix="$BUILD_ROOT/host-python" --without-ensurepip --disable-test-modules)
    (cd "$HOST_ROOT" && make -j"${JOBS:-2}")
    (cd "$HOST_ROOT" && make install)
  fi
fi
"$HOST_PYTHON" --version

cat > "$TOOLBIN/arm64-apple-ios-clang" <<EOF
#!/bin/sh
exec clang --target=arm64-apple-ios${DEPLOYMENT_TARGET} -isysroot "$SDK_ROOT" "\$@" -fuse-ld=lld
EOF
cat > "$TOOLBIN/arm64-apple-ios-clang++" <<EOF
#!/bin/sh
exec clang++ --target=arm64-apple-ios${DEPLOYMENT_TARGET} -isysroot "$SDK_ROOT" "\$@" -fuse-ld=lld
EOF
cat > "$TOOLBIN/arm64-apple-ios-cpp" <<EOF
#!/bin/sh
exec clang -E --target=arm64-apple-ios${DEPLOYMENT_TARGET} -isysroot "$SDK_ROOT" "\$@"
EOF
cat > "$TOOLBIN/arm64-apple-ios-ar" <<'EOF'
#!/bin/sh
exec llvm-ar "$@"
EOF
chmod +x "$TOOLBIN"/*

TARGET_ROOT=$BUILD_ROOT/target-cpython
rm -rf "$TARGET_ROOT"
mkdir -p "$TARGET_ROOT"
git -C "$CPYTHON_ROOT" archive HEAD | tar -x -C "$TARGET_ROOT"
cat > "$TARGET_ROOT/ios_compat.c" <<'EOF'
#include <stdint.h>
int __isPlatformVersionAtLeast(uint32_t platform, uint32_t major, uint32_t minor, uint32_t subminor) {
    (void)platform; (void)major; (void)minor; (void)subminor;
    return 1;
}
EOF
clang --target=arm64-apple-ios${DEPLOYMENT_TARGET} -isysroot "$SDK_ROOT" \
  -c "$TARGET_ROOT/ios_compat.c" -o "$TARGET_ROOT/ios_compat.o"
PATH="$TOOLBIN:/usr/bin:/bin" CC=arm64-apple-ios-clang \
  LIBS="$TARGET_ROOT/ios_compat.o" \
  py_cv_module__lzma=n/a py_cv_module__bz2=n/a py_cv_module__dbm=n/a \
  py_cv_module__gdbm=n/a py_cv_module_readline=n/a py_cv_module__curses=n/a \
  py_cv_module__curses_panel=n/a py_cv_module__blake2=n/a py_cv_module__ctypes=n/a \
  py_cv_module__uuid=n/a \
  "$TARGET_ROOT/configure" --host=arm64-apple-ios${DEPLOYMENT_TARGET} \
  --build=x86_64-pc-linux-gnu --with-build-python="$HOST_PYTHON" \
  --without-ensurepip --disable-test-modules --disable-ipv6 --with-lto=no \
  --enable-framework="$TARGET_ROOT/framework"
(cd "$TARGET_ROOT" && PATH="$TOOLBIN:/usr/bin:/bin" make -j"${JOBS:-2}")

mkdir -p "$BUILD_ROOT/artifact"
CC=arm64-apple-ios-clang PATH="$TOOLBIN:/usr/bin:/bin" \
  "$ROOT/build/package-native-ios.sh" \
  "$TARGET_ROOT" "$BUILD_ROOT/artifact/hermes"
cp "$BUILD_ROOT/artifact/hermes" "$ROOT/hermes"
cp "$BUILD_ROOT/artifact/hermesrt.zip" "$ROOT/hermesrt.zip"
file "$ROOT/hermes" "$ROOT/hermesrt.zip"
