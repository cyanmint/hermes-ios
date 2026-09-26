#!/usr/bin/env python3
"""Verify static CPython module registration and archive membership."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REQUIRED = {
    "_random": "Modules/_randommodule.o",
    "_struct": "Modules/_struct.o",
    "math": "Modules/mathmodule.o",
    "select": "Modules/selectmodule.o",
    "_socket": "Modules/socketmodule.o",
    "_ssl": "Modules/_ssl.o",
    "_hashlib": "Modules/_hashopenssl.o",
    "_sqlite3": "Modules/_sqlite/module.o",
    "pyexpat": "Modules/pyexpat.o",
    "unicodedata": "Modules/unicodedata.o",
    "_contextvars": "Modules/_contextvarsmodule.o",
    "binascii": "Modules/binascii.o",
}


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {Path(sys.argv[0]).name} TARGET_ROOT", file=sys.stderr)
        return 2

    root = Path(sys.argv[1])
    config_path = root / "Modules/config.c"
    object_list_path = root / "native-module-objects.txt"
    archive_path = root / "libpython3.13.a"
    missing: list[str] = []

    if not config_path.is_file():
        missing.append("Modules/config.c")
        config = ""
    else:
        config = config_path.read_text(encoding="utf-8", errors="replace")

    if not object_list_path.is_file():
        missing.append("native-module-objects.txt")
        objects: set[str] = set()
    else:
        objects = set(object_list_path.read_text(encoding="utf-8").splitlines())

    for module, obj in REQUIRED.items():
        registration = f'{{"{module}", PyInit_{module}}}'
        if registration not in config:
            missing.append(f"builtin registration: {module}")
        if obj not in objects:
            missing.append(f"module object list: {obj}")

    if not archive_path.is_file():
        missing.append("libpython3.13.a")
    else:
        result = subprocess.run(
            ["llvm-ar", "t", str(archive_path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode:
            print(result.stderr, file=sys.stderr, end="")
            return result.returncode
        archive_members = set(result.stdout.splitlines())
        for module, obj in REQUIRED.items():
            member = Path(obj).name
            if member not in archive_members:
                missing.append(f"archive member: {member} ({module})")

    if missing:
        print("static native module verification failed:", file=sys.stderr)
        for item in missing:
            print(f"- {item}", file=sys.stderr)
        return 1

    print(f"static native module verification passed ({len(REQUIRED)} modules)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
