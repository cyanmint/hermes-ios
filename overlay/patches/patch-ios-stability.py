#!/usr/bin/env python3
"""Apply iOS-only runtime safety patches to the packaged agent."""
from __future__ import annotations

import sys
from pathlib import Path


def patch_usage_pricing(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    anchor = '''    bundled_entry = _lookup_official_docs_pricing(route)\n    if bundled_entry:\n        return bundled_entry\n    if route.base_url:\n'''
    replacement = '''    bundled_entry = _lookup_official_docs_pricing(route)\n    if bundled_entry:\n        return bundled_entry\n    # iOS static builds must not perform the optional pricing metadata probe.\n    # That background requests/SSLContext path can abort the process in the\n    # statically linked OpenSSL runtime after an otherwise successful turn.\n    return None\n    if route.base_url:\n'''
    if "iOS static builds must not perform the optional pricing metadata probe." in text:
        return
    if anchor not in text:
        raise SystemExit(f"usage pricing patch anchor not found: {path}")
    path.write_text(text.replace(anchor, replacement, 1), encoding="utf-8", newline="\n")


def patch_process_title(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    anchor = "    import ctypes\n    import platform\n"
    replacement = "    try:\n        import ctypes\n    except ImportError:\n        return\n    import platform\n"
    if "except ImportError:\n        return\n    import platform" in text:
        return
    if anchor not in text:
        raise SystemExit(f"process title patch anchor not found: {path}")
    path.write_text(text.replace(anchor, replacement, 1), encoding="utf-8", newline="\n")


def patch_local_terminal(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "subprocess._hermes_ios_posix_spawn_popen" in text:
        return
    import_anchor = "import shutil\nimport signal\n"
    import_replacement = "import shutil\nimport shlex\nimport signal\n"
    if import_anchor not in text:
        raise SystemExit(f"local terminal import patch anchor not found: {path}")
    text = text.replace(import_anchor, import_replacement, 1)
    anchor = '''        args = [bash, *(["-l"] if login else []), "-c", cmd_string]
        self._recover_cwd()
        proc = subprocess.Popen(
'''
    replacement = '''        self._recover_cwd()
        use_ios_spawn = (
            sys.platform == "ios"
            and hasattr(subprocess, "_hermes_ios_posix_spawn_popen")
        )
        if use_ios_spawn:
            cmd_string = f"cd -- {shlex.quote(self.cwd)} && {cmd_string}"
        args = [bash, *(["-l"] if login else []), "-c", cmd_string]
        popen = (
            subprocess._hermes_ios_posix_spawn_popen
            if use_ios_spawn else subprocess.Popen
        )
        proc = popen(
'''
    if anchor not in text:
        raise SystemExit(f"local terminal spawn patch anchor not found: {path}")
    text = text.replace(anchor, replacement, 1)
    old_options = '''            start_new_session=True, cwd=self.cwd,
            **({"creationflags": windows_hide_flags()} if _IS_WINDOWS else {}))
'''
    new_options = '''            start_new_session=not use_ios_spawn,
            cwd=None if use_ios_spawn else self.cwd,
            close_fds=not use_ios_spawn,
            **({"creationflags": windows_hide_flags()} if _IS_WINDOWS else {}))
'''
    if old_options not in text:
        raise SystemExit(f"local terminal spawn options patch anchor not found: {path}")
    text = text.replace(old_options, new_options, 1)
    path.write_text(text, encoding="utf-8", newline="\n")


def main() -> int:
    if len(sys.argv) != 2:
        raise SystemExit("usage: patch-ios-stability.py <staging-hermes-root>")
    root = Path(sys.argv[1])
    patch_usage_pricing(root / "agent" / "usage_pricing.py")
    patch_process_title(root / "hermes_cli" / "main.py")
    patch_local_terminal(root / "tools" / "environments" / "local.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
