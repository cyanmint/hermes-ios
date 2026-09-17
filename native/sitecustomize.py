"""Native iOS runtime bootstrap for extensions stored in hermesrt.zip."""
from __future__ import annotations

import os
import sys
import tempfile
import zipfile


def _extract_native_extensions() -> None:
    archive = next((p for p in sys.path if p.endswith(".zip") and os.path.isfile(p)), None)
    if archive is None:
        return
    destination = os.path.join(tempfile.gettempdir(), "hermes-native-" + str(os.getpid()))
    os.makedirs(destination, exist_ok=True)
    with zipfile.ZipFile(archive) as source:
        for info in source.infolist():
            name = info.filename
            if not name.endswith((".so", ".dylib")):
                continue
            target = os.path.join(destination, os.path.basename(name))
            if not os.path.exists(target):
                with open(target, "wb") as output:
                    output.write(source.read(info))
    if destination not in sys.path:
        sys.path.insert(0, destination)


_extract_native_extensions()
