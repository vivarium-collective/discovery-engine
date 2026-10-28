"""Migrate repo-level inputs (datasets/ + references/papers.bib) to per-investigation
ownership where a single investigation uses them.

Inputs are becoming investigation-owned. This tool inspects existing repo-level
``datasets/`` and assigns each dataset to an investigation when it is used by
exactly ONE investigation's studies. Imported source packages and
multi-investigation / unused items STAY global (reported, not moved).

Heuristic: a repo-level dataset is "used by investigation X" if the dataset's
filename (full name or stem) appears anywhere in the text of any ``study.yaml``
belonging to X (a member of X's ``investigation.yaml.studies``).

  - exactly 1 investigation uses it  -> assigned to that investigation
  - 0 or >1 investigations use it     -> stays global

When applied, an assigned dataset is ``git mv``- d into
``investigations/<slug>/inputs/datasets/`` and appended to that investigation's
``investigation.yaml`` ``inputs.datasets``. Idempotent — already-migrated files
are skipped. Global items are left in place.

CLI:
    python -m discovery_engine.migrate_inputs --workspace <ws> [--apply]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from discovery_engine.migrate_fs import git_mv as _git_mv

import yaml

from .workspace_paths import WorkspacePaths


def _investigation_studies(wp: WorkspacePaths) -> dict[str, list[Path]]:
    """investigation slug -> list of member study.yaml paths that exist here."""
    out: dict[str, list[Path]] = {}
    inv_root = wp.dir("investigations")
    if not inv_root.is_dir():
        return out
    for invf in sorted(inv_root.glob("*/investigation.yaml")):
        inv = yaml.safe_load(invf.read_text(encoding="utf-8")) or {}
        islug = invf.parent.name
        study_yamls: list[Path] = []
        for s in (inv.get("studies") or []):
            slug = s if isinstance(s, str) else (s or {}).get("study")
            if not slug:
                continue
            sy = invf.parent / "studies" / slug / "study.yaml"
            if sy.is_file():
                study_yamls.append(sy)
        out[islug] = study_yamls
    return out


def _dataset_used_by(name: str, study_yamls: list[Path]) -> bool:
    """True if the dataset filename (name or stem) appears in any study.yaml text."""
    stem = Path(name).stem
    for sy in study_yamls:
        try:
            text = sy.read_text(encoding="utf-8")
        except Exception:
            continue
        if name in text or stem in text:
            return True
    return False


def plan_inputs_migration(ws_root: Path) -> dict:
    """Compute per-investigation dataset assignments without touching the filesystem.

    Returns ``{"assignments": {slug: [dataset_paths]}, "global": [dataset_paths],
    "ambiguous": [...]}`` where dataset paths are workspace-root-relative
    (``datasets/<name>``). A dataset goes to an investigation iff exactly one
    investigation's studies reference it; 0 or >1 -> global.
    """
    ws_root = Path(ws_root)
    wp = WorkspacePaths.load(ws_root)
    inv_studies = _investigation_studies(wp)

    datasets_dir = wp.dir("datasets")
    assignments: dict[str, list[str]] = {}
    global_items: list[str] = []
    ambiguous: list[dict] = []

    if datasets_dir.is_dir():
        for p in sorted(datasets_dir.iterdir()):
            if not p.is_file():
                continue
            rel = f"{wp.rel('datasets')}/{p.name}"
            users = [slug for slug, sys in inv_studies.items()
                     if _dataset_used_by(p.name, sys)]
            if len(users) == 1:
                assignments.setdefault(users[0], []).append(rel)
            else:
                global_items.append(rel)
                if len(users) > 1:
                    ambiguous.append({"dataset": rel, "investigations": users})

    return {"workspace": str(ws_root), "assignments": assignments,
            "global": global_items, "ambiguous": ambiguous}




def _append_input_dataset(inv_yaml: Path, name: str) -> None:
    """Append a migrated dataset to investigation.yaml inputs.datasets, creating
    the block if absent. Idempotent — skips if already listed."""
    data = yaml.safe_load(inv_yaml.read_text(encoding="utf-8")) or {}
    inputs = data.get("inputs")
    if not isinstance(inputs, dict):
        inputs = {}
    datasets = inputs.get("datasets")
    if not isinstance(datasets, list):
        datasets = []
    entry = f"inputs/datasets/{name}"
    existing = {d if isinstance(d, str) else (d or {}).get("path") for d in datasets}
    if entry not in existing and name not in existing:
        datasets.append(entry)
    inputs["datasets"] = datasets
    data["inputs"] = inputs
    inv_yaml.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


def migrate_inputs(ws_root: Path, *, apply: bool = False) -> dict:
    """Return the plan; when ``apply=True``, move assigned datasets into
    ``investigations/<slug>/inputs/datasets/`` and record them in
    investigation.yaml. Idempotent; global items are left in place."""
    ws_root = Path(ws_root)
    plan = plan_inputs_migration(ws_root)
    if not apply:
        return plan
    wp = WorkspacePaths.load(ws_root)
    inv_root = wp.dir("investigations")
    for slug, paths in plan["assignments"].items():
        inv_yaml = inv_root / slug / "investigation.yaml"
        for rel in paths:
            src = ws_root / rel
            name = Path(rel).name
            dest = inv_root / slug / "inputs" / "datasets" / name
            if dest.exists() and not src.exists():
                continue  # already migrated
            if src.exists():
                _git_mv(src, dest, ws_root)
            if inv_yaml.is_file():
                _append_input_dataset(inv_yaml, name)
    return plan


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", default=".")
    ap.add_argument("--apply", action="store_true",
                    help="perform the moves (default: print the plan only)")
    args = ap.parse_args(argv)
    res = migrate_inputs(Path(args.workspace), apply=args.apply)
    n_assigned = sum(len(v) for v in res["assignments"].values())
    print(f"assigned: {n_assigned} | global (left in place): {len(res['global'])}")
    for slug, paths in res["assignments"].items():
        for rel in paths:
            name = Path(rel).name
            print(f"  {rel} -> investigations/{slug}/inputs/datasets/{name}")
    for rel in res["global"]:
        print(f"  GLOBAL (left in place): {rel}")
    for amb in res["ambiguous"]:
        print(f"    (used by multiple: {', '.join(amb['investigations'])})")
    if not args.apply:
        print("(plan only — pass --apply to perform the moves)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
