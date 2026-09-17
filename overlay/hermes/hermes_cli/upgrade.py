from __future__ import annotations

import io
import os
import runpy
import shutil
import sys
import tempfile
import zipfile
from urllib.parse import urlparse
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
    try:
        from dulwich import porcelain
        from dulwich.repo import Repo
    except ImportError as exc:
        raise RuntimeError("upgrade requires bundled pure-Python dulwich") from exc
    repo = Repo(str(path))
    porcelain.reset(repo, "hard")
    remote_url = repo.get_config().get((b"remote", b"origin"), b"url")
    if not remote_url:
        raise RuntimeError(f"git clone has no origin URL: {path.name}")
    fetched = porcelain.fetch(repo, remote_url.decode("utf-8"))
    refs = fetched.refs
    candidates = (
        b"refs/remotes/origin/main",
        b"refs/remotes/origin/master",
        b"refs/heads/main",
        b"refs/heads/master",
    )
    new_head = next((refs[name] for name in candidates if name in refs), None)
    if new_head is None:
        remote_heads = sorted(name for name in refs if name.startswith(b"refs/heads/"))
        if len(remote_heads) != 1:
            raise RuntimeError(f"could not identify origin default branch: {remote_heads!r}")
        new_head = refs[remote_heads[0]]
    target_ref = b"refs/heads/upgrade-target"
    repo.refs[target_ref] = new_head
    if isinstance(new_head, str):
        head_hex = new_head
    elif len(new_head) == 40:
        head_hex = new_head.decode("ascii")
    else:
        head_hex = new_head.hex()
    try:
        porcelain.reset(repo, "hard", treeish="refs/heads/upgrade-target")
    except KeyError:
        _github_archive_fallback(path, remote_url.decode("utf-8"), "main")
    del repo.refs[target_ref]


def _github_archive_fallback(path: Path, remote_url: str, branch: str) -> None:
    parsed = urlparse(remote_url.removesuffix(".git"))
    if parsed.netloc != "github.com":
        raise RuntimeError("shallow Git object is unavailable and remote is not GitHub")
    owner_repo = parsed.path.strip("/")
    url = f"https://codeload.github.com/{owner_repo}/zip/refs/heads/{branch}"
    try:
        import httpx
        response = httpx.get(url, timeout=90, follow_redirects=True)
        response.raise_for_status()
        archive = zipfile.ZipFile(io.BytesIO(response.content))
    except Exception as exc:
        raise RuntimeError(f"GitHub source fallback failed: {exc}") from exc
    with tempfile.TemporaryDirectory(prefix="hermes-source-") as extracted:
        extracted_root = Path(extracted)
        archive.extractall(extracted_root)
        roots = [p for p in extracted_root.iterdir() if p.is_dir()]
        if len(roots) != 1:
            raise RuntimeError("GitHub source archive has an unexpected root")
        git_dir = path / ".git"
        for child in path.iterdir():
            if child.name == ".git":
                continue
            if child.is_dir():
                shutil.rmtree(child)
            else:
                child.unlink()
        _copytree_contents(roots[0], path)
        if not git_dir.is_dir():
            raise RuntimeError("source clone lost its .git directory")


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
        except (OSError, RuntimeError) as exc:
            print(f"upgrade failed: {exc}", file=sys.stderr)
            return 1
    print("Hermes runtime upgraded; Python runtime was preserved.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
