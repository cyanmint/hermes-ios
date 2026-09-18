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
HOST_PYTHON=${HOST_PYTHON:-$BUILD_ROOT/host-python/bin/python3.13}
OPENSSL_REF=${OPENSSL_REF:-openssl-3.3.2}
OPENSSL_ROOT=${OPENSSL_ROOT:-$BUILD_ROOT/openssl}
OPENSSL_INSTALL=${OPENSSL_INSTALL:-$BUILD_ROOT/openssl-install}
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
    (cd "$HOST_ROOT" && make -j"${JOBS:-16}")
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

if [ ! -d "$OPENSSL_ROOT/.git" ]; then
  git clone --depth=1 --branch "$OPENSSL_REF" https://github.com/openssl/openssl.git "$OPENSSL_ROOT"
fi
if [ ! -f "$OPENSSL_INSTALL/lib/libssl.a" ] || [ ! -f "$OPENSSL_INSTALL/lib/libcrypto.a" ]; then
  (
    cd "$OPENSSL_ROOT"
    make clean >/dev/null 2>&1 || true
    CC="$TOOLBIN/arm64-apple-ios-clang" \
      CFLAGS="-I$SDK_ROOT/usr/include -isysroot $SDK_ROOT -miphoneos-version-min=$DEPLOYMENT_TARGET" \
      ./Configure iphoneos-cross no-shared no-apps no-tests \
        --prefix="$OPENSSL_INSTALL" -static
    sed -i "s#/SDKs/#$SDK_ROOT#g" Makefile
    make -j16 build_libs
    make install_sw
  )
fi

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
(cd "$TARGET_ROOT" && \
  PATH="$TOOLBIN:/usr/bin:/bin" CC=arm64-apple-ios-clang \
    CPPFLAGS="-DOPENSSL_THREADS -I$OPENSSL_INSTALL/include" \
    LDFLAGS="-L$OPENSSL_INSTALL/lib" \
    LIBS="$TARGET_ROOT/ios_compat.o -lssl -lcrypto" \
    py_cv_module__lzma=n/a py_cv_module__bz2=n/a py_cv_module__dbm=n/a \
    py_cv_module__gdbm=n/a py_cv_module_readline=n/a py_cv_module__curses=n/a \
    py_cv_module__curses_panel=n/a py_cv_module__blake2=n/a py_cv_module__ctypes=n/a \
    py_cv_module__decimal=n/a \
    py_cv_module__elementtree=n/a py_cv_module__uuid=n/a \
    ./configure --host=arm64-apple-ios${DEPLOYMENT_TARGET} \
    --build=x86_64-pc-linux-gnu --with-build-python="$HOST_PYTHON" \
    --without-ensurepip --disable-test-modules --disable-ipv6 --with-lto=no \
    --enable-framework)
python3 - "$TARGET_ROOT/Modules/Setup.stdlib" "$TARGET_ROOT/Modules/Setup.local" "$TARGET_ROOT/Makefile" <<'PY'
import pathlib, sys
source, target, makefile = sys.argv[1:]
lines = pathlib.Path(source).read_text().splitlines()
for i, line in enumerate(lines):
    if line.strip() == "*shared*": lines[i] = "*static*"
    if line.startswith("_decimal "): lines[i] += " -IModules/_decimal/libmpdec Modules/_decimal/libmpdec/libmpdec.a"
pathlib.Path(target).write_text("\n".join(lines) + "\n")
objects = []
for line in lines:
    line = line.strip()
    if not line or line.startswith("#") or line.startswith("*"):
        continue
    for token in line.split()[1:]:
        if token.endswith(".c"):
            source_path = token[2:] if token.startswith("$(srcdir)/") else token
            objects.append("Modules/" + source_path[:-2] + ".o")
objects = sorted(set(objects))
with open(makefile, "a", encoding="utf-8", newline="\n") as f:
    f.write("\nMODOBJS += " + " ".join(objects) + "\n")
    f.write("MODULE_OBJS += " + " ".join(objects) + "\n")
    f.write("LIBRARY_OBJS += $(MODULE_OBJS)\n")
    f.write("libpython3.13.a: " + " ".join(objects) + "\n")
    expat = pathlib.Path(makefile).parent / "Modules/expat/libexpat.a"
    f.write("SHLIBS += -lz " + str(pathlib.Path(makefile).parent / "Modules/_hacl/libHacl_Hash_SHA2.a") + " -Wl,-force_load," + str(expat) + "\n")
    hacl = pathlib.Path(makefile).parent / "Modules/_hacl/libHacl_Hash_SHA2.a"
    f.write("PY_CORE_LDFLAGS += -lz -Wl,-force_load," + str(hacl) + " -Wl,-force_load," + str(expat) + "\n")
PY

(cd "$TARGET_ROOT" && PATH="$TOOLBIN:/usr/bin:/bin" make -j"${JOBS:-16}" Modules/_hacl/libHacl_Hash_SHA2.a Modules/expat/libexpat.a)
(cd "$TARGET_ROOT" && PATH="$TOOLBIN:/usr/bin:/bin" make -o Makefile -j"${JOBS:-16}" libpython3.13.a || {
  rc=$?
  [ "$rc" -eq 2 ] || exit "$rc"
  printf '\nSHLIBS += -lz -lsqlite3 %s/Modules/_hacl/libHacl_Hash_SHA2.a -Wl,-force_load,%s/Modules/expat/libexpat.a\nPY_CORE_LDFLAGS += -lz -lsqlite3 %s/Modules/_hacl/libHacl_Hash_SHA2.a\n' "$TARGET_ROOT" "$TARGET_ROOT" "$TARGET_ROOT" >> "$TARGET_ROOT/Makefile"
  PATH="$TOOLBIN:/usr/bin:/bin" make -o Makefile -j"${JOBS:-16}" libpython3.13.a
})

mkdir -p "$BUILD_ROOT/artifact"
CC=arm64-apple-ios-clang PATH="$TOOLBIN:/usr/bin:/bin" \
  "$ROOT/build/package-native-ios.sh" \
  "$TARGET_ROOT" "$BUILD_ROOT/artifact/hermes"
cp "$BUILD_ROOT/artifact/hermes" "$ROOT/hermes"
cp "$BUILD_ROOT/artifact/hermesrt.zip" "$ROOT/hermesrt.zip"
file "$ROOT/hermes" "$ROOT/hermesrt.zip"
