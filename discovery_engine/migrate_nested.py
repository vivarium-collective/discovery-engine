"""Migrate a workspace from flat studies/ to nested investigations/<inv>/studies/.

Maps each flat ``studies/<slug>/`` to its owning investigation (investigation.yaml
``studies[]`` ∪ study.yaml ``investigation:`` back-ref), moves with ``git mv`` to
preserve history, and rewrites ``workspace.yaml`` ``layout:``. Studies whose owning investigation is absent here (lives on another branch)
or unknown are reported as orphans and left in place. Idempotent — re-running after a migration
finds nothing left to move.

CLI:
    python -m discovery_engine.migrate_nested --workspace <ws> [--dry-run]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from discovery_engine.migrate_fs import git_mv as _git_mv

import yaml

from .workspace_paths import WorkspacePaths


def _owner_map(wp: WorkspacePaths) -> dict[str, str]:
    """study slug -> investigation slug, from investigation.yaml studies[] plus
    study.yaml investigation: back-refs. The membership list wins on conflict."""
    out: dict[str, str] = {}
    inv_root = wp.dir("investigations")
    if inv_root.is_dir():
        for invf in sorted(inv_root.glob("*/investigation.yaml")):
            inv = yaml.safe_load(invf.read_text(encoding="utf-8")) or {}
            islug = inv.get("name") or invf.parent.name
            for s in (inv.get("studies") or []):
                slug = s if isinstance(s, str) else (s or {}).get("study")
                if slug:
                    out.setdefault(slug, islug)
    flat = wp.dir("studies")
    if flat.is_dir():
        for sy in sorted(flat.glob("*/study.yaml")):
            data = yaml.safe_load(sy.read_text(encoding="utf-8")) or {}
            inv = data.get("investigation")
            if inv:
                out.setdefault(sy.parent.name, inv)
    return out


def plan_migration(workspace: Path) -> dict:
    """Compute the flat->nested moves + orphans without touching the filesystem."""
    wp = WorkspacePaths.load(workspace)
    owners = _owner_map(wp)
    inv_root = wp.dir("investigations")
    # Only nest a study if its owning investigation actually exists here
    # (investigation.yaml present). Studies owned by an investigation that lives
    # on another branch are SOFT-ORPHANS — left in place with a reason, never
    # mis-moved under an empty investigation dir.
    present = set()
    if inv_root.is_dir():
        present = {f.parent.name for f in inv_root.glob("*/investigation.yaml")}
    flat = wp.dir("studies")
    moves: list[dict] = []
    orphans: list[dict] = []
    if flat.is_dir():
        for s in sorted(p for p in flat.iterdir() if p.is_dir()):
            if not (s / "study.yaml").is_file():
                continue
            inv = owners.get(s.name)
            if inv and inv in present:
                dest = inv_root / inv / "studies" / s.name
                moves.append({"slug": s.name, "src": str(s), "dest": str(dest),
                              "investigation": inv})
            elif inv:
                orphans.append({"slug": s.name, "src": str(s),
                                "reason": f"owning investigation {inv!r} has no "
                                          f"investigation.yaml in this checkout"})
            else:
                orphans.append({"slug": s.name, "src": str(s),
                                "reason": "no owning investigation"})
    return {"workspace": str(workspace), "moves": moves, "orphans": orphans}




def _rewrite_layout(ws: Path) -> None:
    f = ws / "workspace.yaml"
    data = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    layout = data.get("layout") or {}
    layout.pop("studies", None)  # nested-implied; drop the flat key
    layout["investigations"] = layout.get("investigations", "investigations")
    data["layout"] = layout
    f.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def migrate(workspace: Path, *, dry_run: bool = False) -> dict:
    workspace = Path(workspace)
    plan = plan_migration(workspace)
    if dry_run:
        return plan
    for m in plan["moves"]:
        _git_mv(Path(m["src"]), Path(m["dest"]), workspace)
    if plan["moves"]:
        _rewrite_layout(workspace)
    return plan


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workspace", default=".")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    res = migrate(Path(args.workspace), dry_run=args.dry_run)
    print(f"moves: {len(res['moves'])} | orphans: {len(res['orphans'])}")
    for m in res["moves"]:
        print(f"  {m['slug']} -> investigations/{m['investigation']}/studies/{m['slug']}")
    for o in res["orphans"]:
        print(f"  ORPHAN (left in place): {o['slug']} — {o.get('reason','')}")
    if args.dry_run:
        print("(dry-run)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
