"""Small WASI fallback for the zlib API used by Rich.

The minimal WASI build does not ship the native zlib extension. Hermes uses
only adler32 for deterministic Rich SVG identifiers on this path.
"""

_BASE = 65521

try:
    from binascii import crc32  # type: ignore[attr-defined]
except ImportError:
    def crc32(data: bytes, value: int = 0) -> int:
        checksum = value & 0xFFFFFFFF
        for byte in data:
            checksum ^= byte
            for _ in range(8):
                checksum = (checksum >> 1) ^ (0xEDB88320 if checksum & 1 else 0)
        return checksum & 0xFFFFFFFF


def adler32(data: bytes, value: int = 1) -> int:
    checksum = value & 0xFFFFFFFF
    first = checksum & 0xFFFF
    second = (checksum >> 16) & 0xFFFF
    for byte in data:
        first = (first + byte) % _BASE
        second = (second + first) % _BASE
    return (second << 16) | first
