"""Enable fork-free subprocess creation for Hermes on jailbroken iOS."""
from __future__ import annotations

import contextvars
import errno
import os
import subprocess
from typing import Any


_spawn_isolated = contextvars.ContextVar("hermes_ios_spawn_isolated", default=False)
_original_posix_spawn = None


def _fork_exec_unavailable(*_args: Any, **_kwargs: Any) -> int:
    raise OSError(errno.ENOTSUP, "Hermes iOS supports only posix_spawn-compatible subprocesses")


def _open_fd_numbers() -> set[int]:
    try:
        candidates = (int(name) for name in os.listdir("/proc/self/fd") if name.isdecimal())
    except OSError:
        try:
            limit = os.sysconf("SC_OPEN_MAX")
        except (OSError, ValueError):
            limit = 256
        candidates = iter(range(3, int(limit)))

    opened = set()
    for fd in candidates:
        if fd <= 2:
            continue
        try:
            if os.get_inheritable(fd):
                opened.add(fd)
        except OSError as error:
            if error.errno != errno.EBADF:
                raise
    return opened


def _file_actions_with_close_fds(actions) -> list[tuple[int, ...]]:
    result = [tuple(action) for action in actions]
    close_action = os.POSIX_SPAWN_CLOSE
    already_closed = {
        action[1] for action in result
        if len(action) >= 2 and action[0] == close_action
    }
    result.extend(
        (close_action, fd)
        for fd in sorted(_open_fd_numbers() - already_closed)
    )
    return result


def install_ios_posix_spawn_support(
    platform_name: str = os.sys.platform,
    jailbreak_root: str = "/var/jb",
):
    """Enable posix_spawn for supported Popen calls on Procursus-rooted iOS."""
    global _original_posix_spawn
    bash_path = os.path.join(jailbreak_root, "usr/bin/bash")
    if platform_name != "ios" or not hasattr(os, "posix_spawn") or not os.path.isfile(bash_path):
        return None

    if getattr(subprocess, "_hermes_ios_posix_spawn_popen", None) is not None:
        return subprocess._hermes_ios_posix_spawn_popen

    if not all(hasattr(os, name) for name in (
        "POSIX_SPAWN_CLOSE", "waitpid", "waitstatus_to_exitcode",
        "WIFSTOPPED", "WSTOPSIG", "WNOHANG",
    )):
        return None

    _original_posix_spawn = os.posix_spawn

    def posix_spawn(path, argv, env, *args, **kwargs):
        if _spawn_isolated.get():
            kwargs.setdefault("setsid", True)
            kwargs["file_actions"] = _file_actions_with_close_fds(
                kwargs.get("file_actions", ())
            )
        return _original_posix_spawn(path, argv, env, *args, **kwargs)

    os.posix_spawn = posix_spawn
    subprocess._can_fork_exec = True
    subprocess._USE_POSIX_SPAWN = True
    subprocess._fork_exec = _fork_exec_unavailable
    del_safe = getattr(subprocess, "_del_safe", None)
    if del_safe is not None:
        del_safe.waitpid = os.waitpid
        del_safe.waitstatus_to_exitcode = os.waitstatus_to_exitcode
        del_safe.WIFSTOPPED = os.WIFSTOPPED
        del_safe.WSTOPSIG = os.WSTOPSIG
        del_safe.WNOHANG = os.WNOHANG

    class IOSPosixSpawnPopen(subprocess.Popen):
        def _posix_spawn(self, *args, **kwargs):
            token = _spawn_isolated.set(True)
            try:
                return super()._posix_spawn(*args, **kwargs)
            finally:
                _spawn_isolated.reset(token)

    IOSPosixSpawnPopen.__name__ = "IOSPosixSpawnPopen"
    subprocess._hermes_ios_posix_spawn_popen = IOSPosixSpawnPopen
    return IOSPosixSpawnPopen
