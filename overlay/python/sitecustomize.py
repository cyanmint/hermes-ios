"""Initialize the pure-Python runtime for the static iOS build."""
from __future__ import annotations

import re
import sys
import zipfile


def _install_zip_metadata_fallbacks() -> None:
    """Make importlib.metadata find dist-info nested under the runtime ZIP."""
    try:
        from importlib import metadata
    except Exception:
        return
    original_version = metadata.version
    versions = {}
    for entry in sys.path:
        archive = entry.split(".zip", 1)[0] + ".zip" if ".zip/" in entry else None
        if not archive:
            continue
        try:
            with zipfile.ZipFile(archive) as bundle:
                for name in bundle.namelist():
                    if not name.endswith(".dist-info/METADATA"):
                        continue
                    text = bundle.read(name).decode("utf-8", "replace")
                    match = re.search(r"^Name: (.+)$", text, re.MULTILINE)
                    version = re.search(r"^Version: (.+)$", text, re.MULTILINE)
                    if match and version:
                        key = re.sub(r"[-_.]+", "-", match.group(1).strip().lower())
                        versions[key] = version.group(1).strip()
        except (OSError, zipfile.BadZipFile):
            continue

    def version(name):
        try:
            return original_version(name)
        except metadata.PackageNotFoundError:
            key = re.sub(r"[-_.]+", "-", name.strip().lower())
            if key in versions:
                return versions[key]
            raise

    metadata.version = version


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


_install_zip_metadata_fallbacks()
_install_hash_fallbacks()
