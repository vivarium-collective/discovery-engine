"""Auto-populate the simulation_set block in a study from baseline/variants/runs.

Pure deterministic Python — no AI involved. The dashboard calls
``populate_simulation_set`` via ``study_outcomes.sync`` and renders the result.

Read-time derivation already exists in vivarium-workbench's
``lib/investigations.py:388-416`` (projects baseline+variants → in-memory
simulation_set). This module PERSISTS that mapping and extends it with
run-derived fields (seeds, status, metrics, pass_fail_tests).

Conservative write policy (never-clobber, never-delete):
- Existing authored entry (matched by name) → fill ONLY absent code-owned fields.
- No matching entry → append the full derived entry.
- Authored entries the deriver didn't produce → left untouched (never deleted).
- Only write if something changed; idempotent second call returns {added:0, updated:0}.
"""
from __future__ import annotations

from pathlib import Path

from . import study_io

# Code-owned fields the deriver may fill.  All OTHER fields are authored and
# MUST NEVER be overwritten by this module.
_CODE_OWNED = frozenset(
    ("name", "kind", "base_model", "is_baseline", "seeds", "status",
     "metrics", "pass_fail_tests", "params", "perturbation")
)

_COMPLETE = {"complete", "completed", "ran", "done"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seeds_from_runs(runs: list[dict]) -> list[int]:
    """Union of seeds across all runs, de-duped and sorted ascending."""
    seen: set[int] = set()
    for r in runs:
        for s in (r.get("seeds") or []):
            if isinstance(s, int):
                seen.add(s)
    return sorted(seen)


def _status_from_runs(runs: list[dict]) -> str:
    """'ran' if any run has a completed-class status, else 'ready'."""
    for r in runs:
        if str(r.get("status", "")).lower() in _COMPLETE:
            return "ran"
    return "ready"


def _metric_names(spec: dict) -> list[str]:
    """Names of the study's readouts[] (the primary measurement series)."""
    return [
        r["name"]
        for r in (spec.get("readouts") or [])
        if isinstance(r, dict) and r.get("name")
    ]


def _test_names(spec: dict) -> list[str]:
    """Names of tests[] (v4) or behavior_tests[] (legacy)."""
    tests = spec.get("tests") or spec.get("behavior_tests") or []
    return [t["name"] for t in tests if isinstance(t, dict) and t.get("name")]


def _pass_fail_from_runs(runs: list[dict], test_names: list[str]) -> list[str]:
    """Outcome keys that appear in any run AND are listed as test names.

    Preserves the ordering from test_names.
    """
    outcome_keys: set[str] = set()
    for r in runs:
        outcome_keys.update((r.get("outcomes") or {}).keys())
    test_set = set(test_names)
    matched = outcome_keys & test_set
    return [n for n in test_names if n in matched]


# ---------------------------------------------------------------------------
# Public API: derive_entries
# ---------------------------------------------------------------------------

def derive_entries(spec: dict) -> list[dict]:
    """Derive simulation_set entries from a loaded study spec.

    Handles both:
    - v4 style: ``conditions.baseline`` + ``conditions.variants``
    - legacy style: top-level ``baseline[]`` + ``variants[]``

    Returns a list of dicts with code-owned fields populated.  Never
    fabricates — if no baseline composite is defined, returns ``[]``.
    """
    runs = [r for r in (spec.get("runs") or []) if isinstance(r, dict)]
    seeds = _seeds_from_runs(runs)
    status = _status_from_runs(runs)
    metrics = _metric_names(spec)
    test_names = _test_names(spec)
    pass_fail = _pass_fail_from_runs(runs, test_names)

    entries: list[dict] = []

    # ── v4: conditions.baseline / conditions.variants ─────────────────────
    cond = spec.get("conditions") or {}
    bl = cond.get("baseline") or {}
    baseline_composite: str | None = bl.get("composite")
    study_name: str = spec.get("name") or "baseline"

    if baseline_composite:
        bl_entry: dict = {
            "name": study_name + "-baseline",
            "kind": "single",
            "base_model": baseline_composite,
            "is_baseline": True,
        }
        if seeds:
            bl_entry["seeds"] = seeds
        bl_entry["status"] = status
        bl_params = dict(bl.get("params") or {})
        if bl_params:
            bl_entry["params"] = bl_params
        if metrics:
            bl_entry["metrics"] = metrics
        if pass_fail:
            bl_entry["pass_fail_tests"] = pass_fail
        entries.append(bl_entry)

        for v in (cond.get("variants") or []):
            if not isinstance(v, dict):
                continue
            ve: dict = {
                "name": v.get("name"),
                "kind": v.get("kind", "single"),
                "base_model": (
                    v.get("composite") or v.get("base_composite") or baseline_composite
                ),
            }
            perturb = dict(v.get("parameter_overrides") or v.get("params") or {})
            if perturb:
                ve["perturbation"] = perturb
            if seeds:
                ve["seeds"] = seeds
            ve["status"] = status
            if metrics:
                ve["metrics"] = metrics
            if pass_fail:
                ve["pass_fail_tests"] = pass_fail
            entries.append(ve)

    else:
        # ── Legacy: top-level baseline[] / variants[] ─────────────────────
        top_baselines = spec.get("baseline") or []
        top_variants = spec.get("variants") or []

        legacy_composite: str | None = None
        if isinstance(top_baselines, list):
            for bl_item in top_baselines:
                if not isinstance(bl_item, dict):
                    continue
                bl_c = bl_item.get("composite")
                if not bl_c:
                    continue
                legacy_composite = bl_c
                bl_name = bl_item.get("name") or study_name
                bl_e: dict = {
                    "name": bl_name + "-baseline",
                    "kind": "single",
                    "base_model": bl_c,
                    "is_baseline": True,
                }
                if seeds:
                    bl_e["seeds"] = seeds
                bl_e["status"] = status
                bl_params = dict(bl_item.get("params") or {})
                if bl_params:
                    bl_e["params"] = bl_params
                if metrics:
                    bl_e["metrics"] = metrics
                if pass_fail:
                    bl_e["pass_fail_tests"] = pass_fail
                entries.append(bl_e)

        if isinstance(top_variants, list):
            for v in top_variants:
                if not isinstance(v, dict):
                    continue
                ve = {
                    "name": v.get("name"),
                    "kind": v.get("kind", "single"),
                    "base_model": (
                        v.get("composite") or v.get("base_composite") or legacy_composite
                    ),
                }
                perturb = dict(v.get("parameter_overrides") or v.get("params") or {})
                if perturb:
                    ve["perturbation"] = perturb
                if seeds:
                    ve["seeds"] = seeds
                ve["status"] = status
                if metrics:
                    ve["metrics"] = metrics
                if pass_fail:
                    ve["pass_fail_tests"] = pass_fail
                entries.append(ve)

    return entries


# ---------------------------------------------------------------------------
# Public API: populate_simulation_set
# ---------------------------------------------------------------------------

def populate_simulation_set(study_dir) -> dict:
    """Fill missing simulation_set entries/fields in study.yaml.

    Conservative: existing authored entry (matched by name) gets only ABSENT
    code-owned fields filled; authored values are NEVER overwritten.  No
    matching entry → append full derived entry.  Authored entries the deriver
    didn't produce are never deleted.  Only writes if something changed.
    Idempotent — second call returns {added:0, updated:0} and is byte-identical.

    Returns {added: int, updated: int}.
    """
    study_dir = Path(study_dir)
    study_yaml = study_dir / "study.yaml"
    spec = study_io.load_yaml_mapping(study_yaml)

    derived = derive_entries(spec)
    if not derived:
        return {"added": 0, "updated": 0}

    # Count-only pass on the plain-loaded spec (no ruamel, no mutation here).
    existing = spec.get("simulation_set") or []
    if not isinstance(existing, list):
        existing = []
    by_name = {
        e["name"]: e
        for e in existing
        if isinstance(e, dict) and e.get("name")
    }

    added = updated = 0
    for de in derived:
        name = de.get("name")
        if not name:
            continue
        if name in by_name:
            target = by_name[name]
            # Count as updated if any code-owned field in the derived entry is absent
            if any(k in de and k not in target for k in _CODE_OWNED):
                updated += 1
        else:
            added += 1

    if added or updated:
        _write_simset_preserving_comments(study_yaml, derived)
    return {"added": added, "updated": updated}


def _write_simset_preserving_comments(
    study_yaml: Path, derived: list[dict]
) -> None:
    """Apply derived simulation_set entries into study_yaml via ruamel round-trip.

    Mirrors ``study_outcomes._write_runs_preserving_comments``:
    - ``YAML(preserve_quotes=True, width=4096)`` to avoid comment loss / reflow.
    - Only code-owned fields are set; authored fields are NEVER touched.
    - New entries (no name match) are appended; existing authored entries whose
      name the deriver didn't produce are left untouched (never deleted).
    """
    from io import StringIO

    from ruamel.yaml import YAML

    ryaml = YAML()
    ryaml.preserve_quotes = True
    ryaml.width = 4096  # avoid line-wrap reflow on long prose values

    rt_spec = ryaml.load(study_yaml.read_text(encoding="utf-8"))
    if rt_spec is None:
        rt_spec = {}

    # Locate or create the simulation_set list
    rt_simset = rt_spec.get("simulation_set")
    if not isinstance(rt_simset, list):
        rt_simset = []
        rt_spec["simulation_set"] = rt_simset

    rt_by_name = {
        e["name"]: e
        for e in rt_simset
        if isinstance(e, dict) and e.get("name")
    }

    for de in derived:
        name = de.get("name")
        if not name:
            continue
        if name in rt_by_name:
            target = rt_by_name[name]
            # Fill ONLY absent code-owned fields — NEVER overwrite authored values
            for k in _CODE_OWNED:
                if k in de and k not in target:
                    target[k] = de[k]
        else:
            # Append the full derived entry (plain dict; ruamel serialises fine)
            rt_simset.append(de)
            rt_by_name[name] = de

    buf = StringIO()
    ryaml.dump(rt_spec, buf)
    study_io.atomic_write(study_yaml, buf.getvalue())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    """CLI: populate simulation_set in one or all studies in a workspace."""
    import argparse

    from .workspace_paths import WorkspacePaths

    ap = argparse.ArgumentParser(
        description="Auto-populate simulation_set in study.yaml from baseline/variants/runs"
    )
    ap.add_argument("--workspace", default=".")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--study", help="study slug")
    grp.add_argument("--all", action="store_true", help="every study in the workspace")
    args = ap.parse_args(argv)

    paths = WorkspacePaths.load(Path(args.workspace))
    dirs = list(paths.iter_study_dirs()) if args.all else [paths.study_dir(args.study)]

    total = {"added": 0, "updated": 0}
    for d in dirs:
        s = populate_simulation_set(d)
        total["added"] += s["added"]
        total["updated"] += s["updated"]
        print(f"{d.name}: added={s['added']} updated={s['updated']}")
    print(f"TOTAL added={total['added']} updated={total['updated']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
