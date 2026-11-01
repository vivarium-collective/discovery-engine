"""Reconcile a study's runs.db into study.yaml and expose the canonical outcome
surface. Mechanical run fields are code-owned; authored outcomes/prose are preserved."""
from __future__ import annotations

from pathlib import Path

from . import study_io, run_registry

# Single-sourced from the shared viva-workspace implementation.
# ``finding_observations``/``chart_store`` import ``canonical_run`` via this module,
# so the name must stay exposed here.
from viva_workspace.outcomes import canonical_outcomes, canonical_run  # noqa: F401


# ---------------------------------------------------------------------------
# Unified graded outcomes (RFC: unified graded-test ontology, Phase 1)
# ---------------------------------------------------------------------------

_COMPLETE_STATES = {"completed", "complete", "ran", "passed", "evaluated"}


def _canonical_run_safe(spec_or_runs):
    """``canonical_run``, robust to mixed ``str``/``float`` run timestamps.

    ``viva_workspace.canonical_run`` sorts by ``timestamp`` and crashes with
    ``TypeError: '>' not supported between 'str' and 'float'`` when a workspace has both
    shapes of timestamp (seen on origins-of-life-vent). Coerce the sort key to ``str``.
    """
    runs = (spec_or_runs.get("runs") if isinstance(spec_or_runs, dict) else spec_or_runs) or []
    runs = [r for r in runs if isinstance(r, dict)]
    flagged = [r for r in runs if r.get("canonical") is True]
    if flagged:
        return flagged[-1]
    completed = [r for r in runs if str(r.get("status", "")).lower() in _COMPLETE_STATES]
    pool = completed or runs
    return max(pool, key=lambda r: str(r.get("timestamp") or "")) if pool else None


def canonical_graded_outcomes(spec: dict) -> dict:
    """The canonical run's graded per-test outcomes, **computed-first**.

    The single source the roll-ups (study verdict / gate / investigation acceptance / knowledge
    verdict) read, so they cannot disagree with the real run-data grade. Prefer
    ``computed_outcomes[T]`` (the only run-data-derived grade; ``study_evaluator.compute_outcomes``)
    over the authored ``outcomes[T]``; fall back to the authored result **only** when a test has no
    computed grade, tagged ``evaluated_by: "authored"`` so a view can show "author-asserted, never
    computed". Returns ``{test_name: {result, ...}}`` — a superset of ``canonical_outcomes``'s
    shape, so callers reading ``outcomes[name]["result"]`` keep working unchanged.

    Before this (RFC root cause): every roll-up read ``canonical_outcomes`` = the AUTHORED
    outcomes, never ``computed_outcomes`` — so a test computing FAIL still read ``passed`` through
    gate/acceptance/verdict. This function makes computed authoritative by construction.
    """
    run = _canonical_run_safe(spec)
    return graded_outcomes_for_run(run) if run else {}


def graded_outcomes_for_run(run: dict) -> dict:
    """Computed-first per-test outcomes for ONE run (see :func:`canonical_graded_outcomes`).

    Prefer ``computed_outcomes[T]`` over the authored ``outcomes[T]``; fall back to authored
    (tagged ``evaluated_by: authored``) only where no computed grade exists. Meta keys
    (``_status`` / ``_grader``) are skipped. Used both for the canonical run and for the
    across-runs union (``knowledge_store._merged_run_outcomes``)."""
    if not isinstance(run, dict):
        return {}
    computed = run.get("computed_outcomes") or {}
    authored = run.get("outcomes") or {}
    out: dict = {}
    for name in ({k for k in computed if not str(k).startswith("_")} | set(authored or {})):
        c = computed.get(name)
        if isinstance(c, dict) and c.get("result") not in (None, "ungraded"):
            out[name] = dict(c)  # computed is authoritative
        elif name in (authored or {}):
            a = authored[name]
            if isinstance(a, dict):
                rec = dict(a)
                rec.setdefault("evaluated_by", "authored")
                out[name] = rec
            else:
                out[name] = a  # a bare authored result (e.g. "FAIL"); bucket_tests reads it as-is
    return out


# ---------------------------------------------------------------------------
# Run reconciliation
# ---------------------------------------------------------------------------

# Code-owned mechanical fields written from runs.db
#
# ``provenance_status``/``env_id``: the SAME runs.db is written by
# vivarium_workbench.lib.composite_runs (which ALTERs in these nullable columns —
# the env-fingerprint + reproducibility-verdict machinery). run_registry.list_runs
# does ``SELECT *``, so a migrated DB's row carries them through as ``db_row``;
# without listing them here they were silently dropped before reaching study.yaml,
# where needs_attention.env_stale_items / nondeterministic_items read them from
# (viva-superpowers cannot import vivarium_workbench to read the DB directly).
# Best-effort/nullable: an older DB missing the columns simply omits the keys.
_MECHANICAL = ("status", "kind", "emitter", "seeds", "params", "timestamp", "commit",
               "generations", "sim_minutes", "n_readouts",
               "provenance_status", "env_id", "variant")


def _emitter_kind(emitter_path: str | None) -> str:
    p = (emitter_path or "").lower()
    if not p:
        return "unknown"
    if p.endswith(".db") or "sqlite" in p:
        return "sqlite"
    if "parquet" in p:
        return "parquet"
    if "zarr" in p or "xarray" in p:
        return "xarray"
    return "unknown"


def _mechanical_record(db_row: dict) -> dict:
    emitter_path = db_row.get("emitter_path")
    emitter: dict = {"kind": _emitter_kind(emitter_path)}
    if emitter_path is not None:
        emitter["store"] = emitter_path
    rec = {
        "name": db_row.get("run_id"),
        "status": db_row.get("status"),
        "timestamp": db_row.get("completed_at") or db_row.get("started_at"),
        "emitter": emitter,
        # reproducibility provenance, when the DB row has it
        # (vwb-migrated runs.db only — absent on an older DB).
        "provenance_status": db_row.get("provenance_status"),
        "env_id": db_row.get("env_id"),
    }
    # Tag the run with its variant (the recorded ``label``), so canonical-run
    # selection excludes a variant/sweep arm from the baseline claim's gate: an
    # untagged sweep arm is indistinguishable from the baseline and its FAIL
    # leaks into the verdict. "baseline"/empty is the baseline, not a variant.
    label = db_row.get("label")
    if label and str(label).strip().lower() != "baseline":
        rec["variant"] = label
    # Canonical run summary — every recorded simulation carries its quantitative
    # shape (generations, simulated time, readouts collected) read from its store
    # via RunReader, so all simulations render uniformly. Best-effort.
    if emitter_path:
        try:
            from viva_emitters.run_reader import RunReader
            summ = RunReader.open(emitter_path).summary()
            if summ.get("generations") is not None:
                rec["generations"] = summ["generations"]
            if summ.get("sim_minutes") is not None:
                rec["sim_minutes"] = summ["sim_minutes"]
            if summ.get("n_observables") is not None:
                rec["n_readouts"] = summ["n_observables"]
        except Exception:  # noqa: BLE001 — never block run recording
            pass
    if db_row.get("generation_id") is not None:
        rec["generation_id"] = db_row.get("generation_id")
    params = db_row.get("params") or db_row.get("params_json")
    if params:
        rec["params"] = params
    return {k: v for k, v in rec.items() if v is not None}


def record_runs(study_dir) -> dict:
    """Merge the study's runs.db rows into study.yaml's runs[] by run name.
    Updates only mechanical fields; preserves authored outcomes/prose and all
    YAML comments via ruamel round-trip write. Idempotent.
    Returns {"added": n, "updated": n}."""
    study_dir = Path(study_dir)
    study_yaml = study_dir / "study.yaml"
    spec = study_io.load_yaml_mapping(study_yaml)
    db_rows = run_registry.list_runs(study_dir / "runs.db")

    runs = spec.get("runs")
    if not isinstance(runs, list):
        runs = []
    by_name = {r["name"]: r for r in runs if isinstance(r, dict) and r.get("name")}

    # Count-only pass on the plain-loaded spec (no mutation here).
    added = updated = 0
    for row in db_rows:
        rec = _mechanical_record(row)
        name = rec.get("name")
        if not name:
            continue
        if name in by_name:
            target = by_name[name]
            changed = any(k in rec and target.get(k) != rec[k] for k in _MECHANICAL)
            if changed:
                updated += 1
        else:
            added += 1

    if added or updated:
        _write_runs_preserving_comments(study_yaml, db_rows)
    return {"added": added, "updated": updated}


def _write_runs_preserving_comments(study_yaml: Path, db_rows: list[dict]) -> None:
    """Apply db_rows into study_yaml using ruamel round-trip so YAML comments
    and formatting are preserved.  Only mechanical fields are touched on
    existing entries; new entries are appended as plain dicts."""
    from io import StringIO

    from ruamel.yaml import YAML

    ryaml = YAML()
    ryaml.preserve_quotes = True
    ryaml.width = 4096  # avoid line-wrap reflow on long prose values

    rt_spec = ryaml.load(study_yaml.read_text(encoding="utf-8"))
    if rt_spec is None:
        rt_spec = {}

    rt_runs = rt_spec.get("runs")
    if not isinstance(rt_runs, list):
        rt_runs = []
        rt_spec["runs"] = rt_runs

    rt_by_name = {r["name"]: r for r in rt_runs if isinstance(r, dict) and r.get("name")}

    for row in db_rows:
        rec = _mechanical_record(row)
        name = rec.get("name")
        if not name:
            continue
        if name in rt_by_name:
            target = rt_by_name[name]
            for k in _MECHANICAL:
                if k in rec:
                    target[k] = rec[k]
        else:
            rt_runs.append(rec)
            rt_by_name[name] = rec

    buf = StringIO()
    ryaml.dump(rt_spec, buf)
    study_io.atomic_write(study_yaml, buf.getvalue())


def sync(study_dir) -> dict:
    """Reconcile runs (record_runs) AND compute code verdicts (compute_outcomes)
    AND auto-populate simulation_set (populate_simulation_set) AND write
    gate_evaluator (write_gate_evaluator) AND populate finding observations
    (populate_finding_observations).

    Every step after record_runs is best-effort: errors are captured (never
    raised) so record_runs always completes. finding-observations runs last so
    compute_outcomes' measured values are present.
    """
    summary = record_runs(study_dir)
    try:
        from .study_evaluator import compute_outcomes
        summary["computed"] = compute_outcomes(study_dir)
    except ImportError as exc:
        summary["computed"] = {"skipped": f"evaluator extra unavailable: {exc}"}
    except Exception as exc:  # noqa: BLE001
        summary["computed"] = {"error": str(exc)}
    try:
        from .simulation_set import populate_simulation_set
        summary["simulation_set"] = populate_simulation_set(study_dir)
    except Exception as exc:  # noqa: BLE001
        summary["simulation_set"] = {"error": str(exc)}
    try:
        from .study_verdict import write_gate_evaluator
        changed = write_gate_evaluator(study_dir)
        summary["gate"] = {"changed": changed}
    except Exception as exc:  # noqa: BLE001
        summary["gate"] = {"error": str(exc)}
    try:
        from .finding_observations import populate_finding_observations
        summary["findings"] = populate_finding_observations(study_dir)
    except Exception as exc:  # noqa: BLE001
        summary["findings"] = {"error": str(exc)}
    return summary


def main(argv=None) -> int:
    import argparse
    from .workspace_paths import WorkspacePaths
    ap = argparse.ArgumentParser(description="Reconcile study runs.db into study.yaml")
    ap.add_argument("--workspace", default=".")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--study", help="study slug")
    grp.add_argument("--all", action="store_true", help="every study in the workspace")
    args = ap.parse_args(argv)

    paths = WorkspacePaths.load(Path(args.workspace))
    if args.all:
        dirs = list(paths.iter_study_dirs())
    else:
        dirs = [paths.study_dir(args.study, must_exist=True)]

    total = {"added": 0, "updated": 0}
    for d in dirs:
        s = record_runs(d)
        total["added"] += s["added"]; total["updated"] += s["updated"]
        print(f"{d.name}: added={s['added']} updated={s['updated']}")
    print(f"TOTAL added={total['added']} updated={total['updated']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
