"""Workspace-root discovery helpers.

The canonical way for a workspace script to locate its own root:

    from discovery_engine.paths import workspace_root

    WS = workspace_root()              # auto-detects caller's file
    runs_db = WS / "studies" / "s1" / "runs.db"

`workspace_root()` walks up from the caller's source file (or the
explicit `start` path) until it finds a directory containing
`workspace.yaml`. This lets scripts in `studies/<slug>/`, `viz/`,
`reports/`, etc. resolve workspace-relative paths without hardcoding
absolute directory names.
"""

from __future__ import annotations

import sys
from pathlib import Path


WORKSPACE_MARKER = "workspace.yaml"


def _walk_for_marker(cur: Path) -> Path | None:
    """Walk up from directory `cur` to the nearest ancestor with the marker."""
    while True:
        if (cur / WORKSPACE_MARKER).is_file():
            return cur
        if cur.parent == cur:
            return None
        cur = cur.parent


def workspace_root(start: Path | str | None = None) -> Path:
    """Return the nearest ancestor directory containing workspace.yaml.

    If `start` is None, the caller's source file is used as the starting
    point (via sys._getframe). Pass an explicit Path for non-script
    callers (REPL, tests, etc.).

    Raises FileNotFoundError if no workspace.yaml is found at or above
    the starting directory.
    """
    if start is None:
        caller_file = sys._getframe(1).f_code.co_filename
        cur = Path(caller_file).resolve().parent
    else:
        s = Path(start).resolve()
        cur = s if s.is_dir() else s.parent

    found = _walk_for_marker(cur)
    if found is None:
        raise FileNotFoundError(
            f"No {WORKSPACE_MARKER} found at or above {start or caller_file}"
        )
    return found


def find_workspace_root(
    start: Path | str, *, missing_ok: bool = False
) -> Path | None:
    """Explicit-start workspace-root lookup for module (non-script) callers.

    The single source of truth behind the historical copies in
    ``study_findings``/``study_narrative``/``investigation_close``/``runner``.
    Unlike :func:`workspace_root`, ``start`` is required (no ``_getframe``
    magic) and a file path walks up from its parent directory.

    Raises ``FileNotFoundError`` when no ``workspace.yaml`` is found, unless
    ``missing_ok=True`` (then returns ``None`` — the runner's contract).
    """
    s = Path(start).resolve()
    cur = s if s.is_dir() else s.parent
    found = _walk_for_marker(cur)
    if found is None and not missing_ok:
        raise FileNotFoundError(
            f"No {WORKSPACE_MARKER} found at or above {start}"
        )
    return found


def workspace_dir(name: str, *, root: Path | str | None = None) -> Path:
    """Absolute path to a workspace directory by logical name, honoring the
    optional ``layout:`` map in ``workspace.yaml`` (flat defaults otherwise).

    `name` is one of: studies, investigations, composites, references, datasets,
    notes, experiments, reports, pbg, scripts, tests, docs, package.
    `root` defaults to the nearest workspace.yaml at/above CWD.
    """
    from .workspace_paths import WorkspacePaths
    if root is not None:
        ws = Path(root).resolve()
    else:
        ws = find_workspace_root(Path.cwd())
    return WorkspacePaths.load(ws).dir(name)


def _main(argv=None) -> int:
    """CLI: print the resolved absolute path of a workspace directory.

    Used by the /pbg-* skills so their shell file-ops stay correct under any
    `layout:` (flat or nested), e.g.:

        INV_DIR="$(python -m discovery_engine.paths investigations --workspace "$WS")"
        git add "$INV_DIR/<slug>/investigation.yaml"

    Or export every directory at once:

        eval "$(python -m discovery_engine.paths --env --workspace "$WS")"
        # -> $STUDIES_DIR, $INVESTIGATIONS_DIR, $REFERENCES_DIR, ...
    """
    import argparse
    from .workspace_paths import WorkspacePaths, LAYOUT_DEFAULTS

    ap = argparse.ArgumentParser(prog="discovery_engine.paths")
    ap.add_argument("name", nargs="?", help="logical dir (studies, investigations, ...)")
    ap.add_argument("--workspace", help="workspace root (default: walk up from CWD)")
    ap.add_argument("--env", action="store_true",
                    help="print 'export <NAME>_DIR=...' for every directory")
    ap.add_argument("--study", help="resolve a study dir by slug (nested-aware)")
    args = ap.parse_args(argv)

    root = Path(args.workspace).resolve() if args.workspace else find_workspace_root(Path.cwd())
    wp = WorkspacePaths.load(root)
    if args.study:
        print(wp.study_dir(args.study, must_exist=True))
        return 0
    if args.env:
        for key in list(LAYOUT_DEFAULTS) + ["package"]:
            print(f"export {key.upper()}_DIR={wp.dir(key)}")
        return 0
    if not args.name:
        ap.error("a directory name is required (or use --env)")
    print(wp.dir(args.name))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
