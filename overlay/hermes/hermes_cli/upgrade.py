from __future__ import annotations

import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path


_PACKAGES = (
    "acp_adapter",
    "agent",
    "cron",
    "gateway",
    "hermes_cli",
    "plugins",
    "providers",
    "tools",
    "tui_gateway",
    "hermes",
)


def _copytree_contents(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for item in source.iterdir():
        destination = target / item.name
        if item.is_dir():
            shutil.copytree(item, destination, dirs_exist_ok=True)
        else:
            shutil.copy2(item, destination)


def _git_pull(path: Path) -> None:
    reset = subprocess.run(
        ["git", "-C", str(path), "reset", "--hard", "HEAD"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if reset.returncode:
        raise RuntimeError(f"git restore failed for {path.name}:\n{reset.stdout}")
    clean = subprocess.run(
        ["git", "-C", str(path), "clean", "-fd"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if clean.returncode:
        raise RuntimeError(f"git clean failed for {path.name}:\n{clean.stdout}")
    result = subprocess.run(
        ["git", "-C", str(path), "pull", "--ff-only"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if result.returncode:
        raise RuntimeError(f"git pull failed for {path.name}:\n{result.stdout}")
    print(result.stdout.rstrip())


def _apply_overlay(root: Path) -> None:
    overlay = root / "overlay"
    runtime = root / "hermes"
    if not overlay.is_dir():
        raise RuntimeError("runtime ZIP does not contain overlay/")
    _copytree_contents(overlay / "hermes", runtime)

    patches = overlay / "patches"
    for name, argument in (
        ("patch-ios-stability.py", str(runtime)),
        ("patch-agent-sdk-compat.py", str(runtime / "agent" / "agent_init.py")),
        ("patch-webui-zip.py", str(root / "hermes-webui" / "api" / "config.py")),
    ):
        patch = patches / name
        if not patch.is_file():
            raise RuntimeError(f"runtime ZIP is missing overlay patch: {name}")
        saved = sys.argv
        try:
            sys.argv = [str(patch), argument]
            runpy.run_path(str(patch), run_name="__hermes_upgrade_patch__")
        finally:
            sys.argv = saved


def _build_runtime(root: Path) -> None:
    agent = root / "hermes"
    webui = root / "hermes-webui"
    if not (agent / ".git").exists() or not (webui / ".git").exists():
        raise RuntimeError("runtime ZIP sources are not shallow git clones")
    browser = agent / "plugins" / "browser"
    if browser.is_dir():
        (browser / "__init__.py").touch()
    _apply_overlay(root)


def _write_archive(archive: Path, root: Path, destination: Path) -> None:
    with zipfile.ZipFile(archive) as old:
        python_entries = {
            info.filename: old.read(info.filename)
            for info in old.infolist()
            if info.filename.startswith("python/")
        }
    temporary = destination.with_suffix(".upgrade.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as output:
            for name, data in sorted(python_entries.items()):
                output.writestr(name, data)
            for directory in ("hermes", "hermes-webui", "overlay"):
                base = root / directory
                for path in sorted(base.rglob("*")):
                    if path.is_file():
                        output.write(path, path.relative_to(root).as_posix())
        with zipfile.ZipFile(temporary) as check:
            if check.testzip() is not None:
                raise RuntimeError("upgraded runtime ZIP failed integrity check")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    archive = Path("hermesrt.zip").resolve()
    if not archive.is_file():
        print(f"upgrade: missing {archive}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="hermes-upgrade-") as work:
        root = Path(work)
        with zipfile.ZipFile(archive) as bundle:
            bundle.extractall(root)
        try:
            _git_pull(root / "hermes")
            _git_pull(root / "hermes-webui")
            _build_runtime(root)
            _write_archive(archive, root, archive)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            print(f"upgrade failed: {exc}", file=sys.stderr)
            return 1
    print("Hermes runtime upgraded; Python runtime was preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
