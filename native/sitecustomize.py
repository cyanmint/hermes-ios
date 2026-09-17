"""Initialize the pure-Python runtime for the static iOS build."""
from __future__ import annotations

import sys


def _install_hash_fallbacks() -> None:
    """Keep cache fingerprints working when optional BLAKE2 is unavailable."""
    try:
        import hashlib
    except Exception:
        return
    if hasattr(hashlib, "blake2b") and hasattr(hashlib, "blake2s"):
        return

    class _FallbackHash:
        def __init__(self, data=b"", digest_size=32):
            self._hash = hashlib.sha256(data)
            self._digest_size = digest_size

        def update(self, data):
            self._hash.update(data)

        def digest(self):
            return self._hash.copy().digest()[:self._digest_size]

        def hexdigest(self):
            return self.digest().hex()

        def copy(self):
            result = type(self)(digest_size=self._digest_size)
            result._hash = self._hash.copy()
            return result

    hashlib.blake2b = lambda data=b"", digest_size=64, **_: _FallbackHash(data, digest_size)
    hashlib.blake2s = lambda data=b"", digest_size=32, **_: _FallbackHash(data, digest_size)


_install_hash_fallbacks()
