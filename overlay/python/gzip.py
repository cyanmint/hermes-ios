"""WASI gzip compatibility shim.

The a-Shell CPython build intentionally has no zlib extension.  Hermes WebUI
uses gzip only as an optional static-response optimization, so returning no
compressed representation makes the caller send the original bytes instead of
failing during the stdlib gzip import.
"""


def compress(data, *args, **kwargs):
    del data, args, kwargs
    return None


def decompress(data, *args, **kwargs):
    raise NotImplementedError("gzip decompression is unavailable in the WASI runtime")
