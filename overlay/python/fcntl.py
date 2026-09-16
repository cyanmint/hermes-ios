"""Minimal fcntl compatibility for the single-process WASI runtime.

The a-Shell WASI target does not expose POSIX fcntl. Hermes uses flock for
optional local coordination; the runtime is isolated to one process, so the
lock operations are safe no-ops rather than making the whole session store
unavailable.
"""

LOCK_SH = 1
LOCK_EX = 2
LOCK_NB = 4
LOCK_UN = 8
F_RDLCK = 0
F_WRLCK = 1
F_UNLCK = 2
F_SETLK = 6
F_SETLKW = 7


def flock(_fd, _operation):
    return None
