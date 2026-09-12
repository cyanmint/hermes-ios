#!/usr/bin/env python3
"""Build the single-file a-Shell Hermes zipapp.

The archive contains the Hermes Agent source tree and only dependency wheels
whose WHEEL metadata advertises a ``*-none-any`` tag. Platform/native wheels
are intentionally omitted: a-Shell's Python supplies them when available.
"""

from __future__ import annotations

import shutil
import subprocess
import tarfile
import sys
import tomllib
import zipapp
from pathlib import Path
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parent
AGENT = ROOT / "hermes-agent"
BUILD = ROOT / ".build-hermes"
WHEELS = BUILD / "wheels"
STAGE = BUILD / "stage"
OUTPUT = ROOT / "hermes"

EXCLUDED_SOURCE = {
    ".git", ".github", "apps", "tests", "tests-js", "website", "docs", "evals",
    "node_modules", "native", "nix", "ui-tui", "web", "package-lock.json", "package.json",
    "pnpm-lock.yaml", "cli.py", "hermes",
}
EXCLUDED_NESTED = {".git", "__pycache__", "node_modules"}

ENTRYPOINT = '''#!/usr/bin/env python3
"""Single-file Hermes Agent entry point."""


def main():
    from hermes_cli.main import main as upstream_main
    return upstream_main()


if __name__ == "__main__":
    main()
'''


def direct_dependencies() -> list[str]:
    project = tomllib.loads((AGENT / "pyproject.toml").read_text(encoding="utf-8"))
    deps: list[str] = []
    for raw in project["project"]["dependencies"]:
        if ";" in raw:
            requirement, marker = raw.split(";", 1)
            if any(platform in marker for platform in ("win32", "darwin", "linux")):
                continue
            raw = requirement.strip()
        deps.append(raw)
    # ptyprocess is pure Python and is used by the POSIX/a-Shell terminal path.
    deps.append("ptyprocess>=0.7.0,<1")
    return deps


def download_wheels() -> None:
    WHEELS.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "pip", "download", "--only-binary=:all:", "--dest", str(WHEELS), *direct_dependencies()],
        check=True,
    )
    # PyYAML publishes a platform wheel because it has an optional C loader,
    # but its ``yaml`` package also contains a pure-Python loader.  Download
    # the sdist separately so the bundle can use that fallback on a-Shell.
    subprocess.run(
        [sys.executable, "-m", "pip", "download", "--no-binary", "PyYAML", "--no-deps", "--dest", str(WHEELS), "PyYAML==6.0.3"],
        check=True,
    )


def is_pure_python_wheel(path: Path) -> bool:
    with ZipFile(path) as archive:
        wheel_files = [name for name in archive.namelist() if name.endswith(".dist-info/WHEEL")]
        if not wheel_files:
            return False
        text = archive.read(wheel_files[0]).decode("utf-8", "replace")
    return any(
        line.startswith("Tag:") and line.rsplit(":", 1)[-1].strip().endswith("-none-any")
        for line in text.splitlines()
    )


def copy_agent_source() -> None:
    STAGE.mkdir(parents=True, exist_ok=True)
    for source in AGENT.iterdir():
        if source.name in EXCLUDED_SOURCE:
            continue
        destination = STAGE / source.name
        if source.is_dir():
            shutil.copytree(
                source,
                destination,
                ignore=lambda _directory, names: [name for name in names if name in EXCLUDED_NESTED],
            )
        else:
            shutil.copy2(source, destination)
    (STAGE / "cli.py").write_text(ENTRYPOINT, encoding="utf-8", newline="\n")


def embed_pure_dependencies() -> list[str]:
    embedded: list[str] = []
    for wheel in sorted(WHEELS.glob("*.whl")):
        if not is_pure_python_wheel(wheel):
            continue
        with ZipFile(wheel) as archive:
            archive.extractall(STAGE)
        embedded.append(wheel.name)
    for source in sorted(WHEELS.glob("PyYAML-*.tar.gz")):
        with tarfile.open(source, "r:gz") as archive:
            for member in archive.getmembers():
                if not member.isfile() or "/yaml/" not in member.name:
                    continue
                relative = member.name.split("/yaml/", 1)[-1]
                if not relative or relative.startswith("../"):
                    continue
                payload = archive.extractfile(member)
                if payload is None:
                    continue
                destination = STAGE / "yaml" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(payload.read())
        embedded.append(source.name + " (pure yaml package)")
    policy = STAGE / "BUNDLE_NATIVE_POLICY.txt"
    policy.write_text(
        "Hermes single-file a-Shell bundle\n\n"
        "Pure-Python wheels are embedded. Native wheels are intentionally omitted; "
        "the runtime uses modules already available in a-Shell when present. "
        "Provider/features requiring missing native extensions remain unavailable.\n\n"
        "Embedded pure wheels:\n" + "\n".join(embedded) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    return embedded


def main() -> None:
    if not AGENT.is_dir():
        raise SystemExit(f"missing Hermes Agent source: {AGENT}")
    if BUILD.exists():
        shutil.rmtree(BUILD)
    if OUTPUT.exists():
        OUTPUT.unlink()
    download_wheels()
    copy_agent_source()
    embedded = embed_pure_dependencies()
    zipapp.create_archive(STAGE, OUTPUT, interpreter="python3", main="cli:main")
    OUTPUT.chmod(0o755)
    print(f"created {OUTPUT}")
    print(f"embedded pure-Python wheels: {len(embedded)}")


if __name__ == "__main__":
    main()
