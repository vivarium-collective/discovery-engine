"""discovery_engine is a standalone library: it must never import the
vivarium_workbench / vivarium_dashboard packages (the dependency direction is
one-way, workbench -> discovery_engine). Compute that needs the workbench
belongs in the workbench, reached via its HTTP API or a CLI verb."""
from __future__ import annotations

import ast
import pathlib

FORBIDDEN = ("vivarium_workbench", "vivarium_dashboard")


def _is_forbidden(name: str) -> bool:
    return any(name == f or name.startswith(f + ".") for f in FORBIDDEN)


def _offenders() -> dict[str, list[int]]:
    root = pathlib.Path(__file__).resolve().parents[1] / "discovery_engine"
    out: dict[str, list[int]] = {}
    for f in root.rglob("*.py"):
        try:
            tree = ast.parse(f.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        lines = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and _is_forbidden(node.module):
                lines.append(node.lineno)
            elif isinstance(node, ast.Import) and any(_is_forbidden(a.name) for a in node.names):
                lines.append(node.lineno)
        if lines:
            out[str(f.relative_to(root.parent))] = sorted(lines)
    return out


def test_discovery_engine_never_imports_the_workbench():
    offenders = _offenders()
    assert not offenders, "discovery_engine imports the workbench:\n" + "\n".join(
        f"  - {f}: lines {ls}" for f, ls in offenders.items()
    )
