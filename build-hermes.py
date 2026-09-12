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
WEBUI = ROOT / "hermes-webui"
BUILD = ROOT / ".build-hermes"
WHEELS = BUILD / "wheels"
STAGE = BUILD / "stage"
OUTPUT = ROOT / "hermes"

EXCLUDED_SOURCE = {
    ".git", ".github", "apps", "tests", "tests-js", "website", "docs", "evals",
    "node_modules", "native", "nix", "ui-tui", "web", "package-lock.json", "package.json",
    "pnpm-lock.yaml", "hermes",
}
EXCLUDED_NESTED = {".git", "__pycache__", "node_modules"}
WEBUI_EXCLUDED = {
    ".git", ".github", "docs", "tests", "scripts", "node_modules", "__pycache__",
    "package.json", "pyproject.toml", "requirements-dev.txt", "uv.lock", "flake.lock",
    "Dockerfile", "docker-compose.yml", "docker-compose.two-container.yml",
    "docker-compose.three-container.yml", "ctl.sh", "bootstrap.py", "mcp_server.py",
}

ENTRYPOINT = '''

def entrypoint():
    """Dispatch the bundled WebUI or the full upstream CLI."""
    import os
    import shutil
    import sys
    import zipfile
    from pathlib import Path

    os.environ["HOME"] = "."
    os.environ["HERMES_HOME"] = "./.hermes"
    os.environ.setdefault("HERMES_WEBUI_ASHELL_MODE", "1")

    Path("./.hermes").mkdir(parents=True, exist_ok=True)

    if len(sys.argv) > 1 and sys.argv[1] == "webui":
        archive = Path(sys.argv[0]).resolve()
        runtime = Path("./.hermes/.webui-runtime")
        marker = runtime / ".complete"
        if not marker.is_file():
            temporary = runtime.with_name(runtime.name + ".tmp")
            shutil.rmtree(temporary, ignore_errors=True)
            temporary.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(archive) as source:
                for prefix in ("_webui_bundle/", "_agent_bundle/"):
                    for name in source.namelist():
                        if not name.startswith(prefix) or name.endswith("/"):
                            continue
                        base = temporary / "_agent_bundle" if prefix == "_agent_bundle/" else temporary
                        target = base / name.split("/", 1)[1]
                        target.parent.mkdir(parents=True, exist_ok=True)
                        with source.open(name) as src, target.open("wb") as dst:
                            shutil.copyfileobj(src, dst)
            (temporary / ".complete").write_text("ok\\n", encoding="utf-8")
            shutil.rmtree(runtime, ignore_errors=True)
            temporary.replace(runtime)
        webui_root = runtime
        agent_root = runtime / "_agent_bundle"
        sys.path.insert(0, str(webui_root))
        os.environ.setdefault("HERMES_WEBUI_AGENT_DIR", str(agent_root))
        os.environ["HERMES_WEBUI_STATE_DIR"] = "./.hermes/webui"
        os.environ["HERMES_WEBUI_DEFAULT_WORKSPACE"] = "./workspace"
        sys.argv = [sys.argv[0], *sys.argv[2:]]
        from server import main as webui_main
        return webui_main()

    from hermes_cli.main import main as upstream_main
    return upstream_main()
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
    entry = STAGE / "cli.py"
    entry.write_text(entry.read_text(encoding="utf-8") + ENTRYPOINT, encoding="utf-8", newline="\n")


def copy_webui_source() -> None:
    """Embed the standard-library WebUI and its static assets for `hermes webui`."""
    if not WEBUI.is_dir():
        raise SystemExit(f"missing Hermes WebUI source: {WEBUI}")
    for container, excluded in (("_webui_bundle", WEBUI_EXCLUDED), ("_agent_bundle", EXCLUDED_SOURCE)):
        source_root = WEBUI if container == "_webui_bundle" else AGENT
        destination_root = STAGE / container
        destination_root.mkdir(parents=True, exist_ok=True)
        for source in source_root.iterdir():
            if source.name in excluded:
                continue
            destination = destination_root / source.name
            if source.is_dir():
                shutil.copytree(
                    source,
                    destination,
                    ignore=lambda _directory, names: [name for name in names if name in EXCLUDED_NESTED],
                )
            else:
                shutil.copy2(source, destination)


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
    copy_webui_source()
    embedded = embed_pure_dependencies()
    zipapp.create_archive(STAGE, OUTPUT, interpreter="python3", main="cli:entrypoint")
    OUTPUT.chmod(0o755)
    shutil.rmtree(BUILD)
    print(f"created {OUTPUT}")
    print(f"embedded pure-Python wheels: {len(embedded)}")


if __name__ == "__main__":
    main()
