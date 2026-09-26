from __future__ import annotations

import errno
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if os.environ.get("HERMES_IOS_TEST_RUNTIME_ZIP"):
    sys.path.insert(0, os.environ["HERMES_IOS_TEST_RUNTIME_ZIP"] + "/python")
else:
    sys.path.insert(0, str(REPO_ROOT / "overlay" / "python"))
from ios_posix_spawn import _open_fd_numbers, install_ios_posix_spawn_support


@unittest.skipUnless(hasattr(os, "posix_spawn") and hasattr(os, "waitpid"), "requires POSIX spawn")
class IOSPosixSpawnTests(unittest.TestCase):
    def test_spawn_backend_runs_child_in_isolated_session_and_captures_output(self):
        original_spawn = os.posix_spawn
        names = ("_can_fork_exec", "_USE_POSIX_SPAWN", "_fork_exec", "_hermes_ios_posix_spawn_popen")
        original_subprocess = {
            name: (hasattr(subprocess, name), getattr(subprocess, name, None))
            for name in names
        }
        safe_attrs = ("waitpid", "waitstatus_to_exitcode", "WIFSTOPPED", "WSTOPSIG", "WNOHANG")
        original_safe_attrs = {name: getattr(subprocess._del_safe, name) for name in safe_attrs}
        try:
            subprocess._can_fork_exec = False
            spawn_popen = install_ios_posix_spawn_support("ios", jailbreak_root="/")
            self.assertIsNotNone(spawn_popen)
            self.assertTrue(subprocess._can_fork_exec)
            self.assertIs(subprocess._del_safe.waitpid, os.waitpid)
            proc = spawn_popen(
                ["/bin/sh", "-c", "sleep 0.1; printf IOS_POSIX_SPAWN_TEST"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                close_fds=False,
                start_new_session=False,
            )
            self.assertEqual(os.getpgid(proc.pid), proc.pid)
            output, _ = proc.communicate(timeout=10)
            self.assertEqual(output, "IOS_POSIX_SPAWN_TEST")
            self.assertEqual(proc.returncode, 0)
        finally:
            os.posix_spawn = original_spawn
            for name, (present, value) in original_subprocess.items():
                if present:
                    setattr(subprocess, name, value)
                elif hasattr(subprocess, name):
                    delattr(subprocess, name)
            for name, value in original_safe_attrs.items():
                setattr(subprocess._del_safe, name, value)

    def test_close_list_tracks_only_explicitly_inheritable_descriptors(self):
        with tempfile.TemporaryFile() as descriptor:
            fd = descriptor.fileno()
            os.set_inheritable(fd, False)
            self.assertNotIn(fd, _open_fd_numbers())
            os.set_inheritable(fd, True)
            self.assertIn(fd, _open_fd_numbers())

    def test_fork_only_options_fail_closed_without_calling_fork_exec(self):
        original_spawn = os.posix_spawn
        names = ("_can_fork_exec", "_USE_POSIX_SPAWN", "_fork_exec", "_hermes_ios_posix_spawn_popen")
        original_subprocess = {
            name: (hasattr(subprocess, name), getattr(subprocess, name, None))
            for name in names
        }
        safe = subprocess._del_safe
        safe_attrs = ("waitpid", "waitstatus_to_exitcode", "WIFSTOPPED", "WSTOPSIG", "WNOHANG")
        original_safe_attrs = {name: getattr(safe, name) for name in safe_attrs}
        try:
            spawn_popen = install_ios_posix_spawn_support("ios", jailbreak_root="/")
            with self.assertRaises(OSError) as raised:
                spawn_popen(
                    ["/bin/sh", "-c", "true"],
                    preexec_fn=lambda: None,
                    stdin=subprocess.DEVNULL,
                    close_fds=False,
                    start_new_session=False,
                )
            self.assertEqual(raised.exception.errno, errno.ENOTSUP)
        finally:
            os.posix_spawn = original_spawn
            for name, (present, value) in original_subprocess.items():
                if present:
                    setattr(subprocess, name, value)
                elif hasattr(subprocess, name):
                    delattr(subprocess, name)
            for name, value in original_safe_attrs.items():
                setattr(safe, name, value)

    def test_backend_is_not_installed_outside_ios_or_without_procursus_bash(self):
        self.assertIsNone(install_ios_posix_spawn_support("darwin"))
        self.assertIsNone(install_ios_posix_spawn_support("ios", jailbreak_root="/not-procursus"))

    @unittest.skipUnless(Path("/proc/self/fd").is_dir(), "requires Linux procfs")
    def test_spawn_closes_unrequested_inheritable_descriptors(self):
        original_spawn = os.posix_spawn
        names = ("_can_fork_exec", "_USE_POSIX_SPAWN", "_fork_exec", "_hermes_ios_posix_spawn_popen")
        original_subprocess = {
            name: (hasattr(subprocess, name), getattr(subprocess, name, None))
            for name in names
        }
        safe = getattr(subprocess, "_del_safe", None)
        safe_attrs = ("waitpid", "waitstatus_to_exitcode", "WIFSTOPPED", "WSTOPSIG", "WNOHANG")
        original_safe_attrs = {name: getattr(safe, name) for name in safe_attrs} if safe else {}
        try:
            spawn_popen = install_ios_posix_spawn_support("ios", jailbreak_root="/")
            with tempfile.TemporaryFile() as inherited:
                os.set_inheritable(inherited.fileno(), True)
                cmd = f"test ! -e /proc/self/fd/{inherited.fileno()} && printf FD_CLOSED"
                proc = spawn_popen(
                    ["/bin/sh", "-c", cmd],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    close_fds=False,
                    start_new_session=False,
                )
                output, _ = proc.communicate(timeout=10)
                self.assertEqual((output, proc.returncode), ("FD_CLOSED", 0))
        finally:
            os.posix_spawn = original_spawn
            for name, (present, value) in original_subprocess.items():
                if present:
                    setattr(subprocess, name, value)
                elif hasattr(subprocess, name):
                    delattr(subprocess, name)
            for name, value in original_safe_attrs.items():
                setattr(safe, name, value)


if __name__ == "__main__":
    unittest.main()
