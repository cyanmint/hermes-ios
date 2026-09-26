#!/usr/bin/env python3
"""Ensure iOS doctor overlays preserve the pinned Agent import contract."""

from __future__ import annotations

import ast
import sys
from pathlib import Path


def imported_doctor_state_names(doctor_path: Path) -> set[str]:
    tree = ast.parse(doctor_path.read_text(encoding="utf-8"), filename=str(doctor_path))
    return {
        alias.asname or alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module == "hermes_cli.doctor_state"
        for alias in node.names
    }


def defined_names(state_path: Path) -> set[str]:
    tree = ast.parse(state_path.read_text(encoding="utf-8"), filename=str(state_path))
    names = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    for node in tree.body:
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.Assign):
            names.update(target.id for target in node.targets if isinstance(target, ast.Name))
    return names


def main() -> int:
    if len(sys.argv) != 3:
        print(f"usage: {Path(sys.argv[0]).name} AGENT_HERMES_ROOT OVERLAY_DOCTOR_STATE", file=sys.stderr)
        return 2

    agent_root, overlay_state = map(Path, sys.argv[1:])
    doctor_path = agent_root / "hermes_cli" / "doctor.py"
    if not doctor_path.is_file() or not overlay_state.is_file():
        print("doctor overlay verification failed: input file missing", file=sys.stderr)
        return 2

    required = imported_doctor_state_names(doctor_path)
    available = defined_names(overlay_state)
    missing = sorted(required - available)
    if missing:
        print("doctor_state overlay is missing Agent imports:", file=sys.stderr)
        for name in missing:
            print(f"- {name}", file=sys.stderr)
        return 1

    print(f"doctor_state overlay verification passed ({len(required)} imports)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
