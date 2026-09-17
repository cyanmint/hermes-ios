"""Extract native iOS extensions before importing the Agent runtime."""
from __future__ import annotations

import os
import sys


def _u16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 2], "little")


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset:offset + 4], "little")


def _extract_native_extensions() -> None:
    archive = next(
        (path for path in sys.path if path.endswith(".zip") and os.path.isfile(path)),
        None,
    )
    if archive is None:
        return

    with open(archive, "rb") as stream:
        data = stream.read()

    destination = os.path.join(
        os.path.dirname(archive), ".hermes-native-" + str(os.getpid())
    )
    os.makedirs(destination, exist_ok=True)
    cursor = 0
    signature = b"PK\x03\x04"
    while True:
        cursor = data.find(signature, cursor)
        if cursor < 0:
            break
        method = _u16(data, cursor + 8)
        compressed_size = _u32(data, cursor + 18)
        filename_size = _u16(data, cursor + 26)
        extra_size = _u16(data, cursor + 28)
        name_start = cursor + 30
        name_end = name_start + filename_size
        name = data[name_start:name_end].decode("utf-8")
        payload_start = name_end + extra_size
        payload_end = payload_start + compressed_size
        if method == 0 and name.endswith(".so"):
            target = os.path.join(destination, os.path.basename(name))
            os.makedirs(os.path.dirname(target), exist_ok=True)
            if not os.path.exists(target):
                with open(target, "wb") as output:
                    output.write(data[payload_start:payload_end])
            signer = "/var/jb/usr/bin/ldid"
            if os.path.isfile(signer):
                result = os.spawnv(os.P_WAIT, signer, [signer, "-S", target])
                if result != 0:
                    raise OSError(result, "ldid failed", target)
        cursor = payload_end

    if destination not in sys.path:
        sys.path.insert(0, destination)


_extract_native_extensions()
