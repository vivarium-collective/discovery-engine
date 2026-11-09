"""Shared vocabulary and generic predicates for the rigor scorecard.

Foundation module: severity vocabulary, study-type vocabulary, emitter kinds,
and the small pure helpers (``_as_list`` / ``_nonempty`` / ``_findings`` /
``_dim`` / ``_study_type`` / ``_replicate_count`` / ``_replication_agreement`` /
``run_is_emitter_backed``) reused across every other rigor submodule. Depends on
nothing else inside the package, so the rest of the package can import from it
without cycles.
"""
from __future__ import annotations

from typing import Any

# Severity vocabulary, ordered worst→best for roll-ups.
GAP = "gap"
WARN = "warn"
OK = "ok"
_SEVERITY_RANK = {GAP: 0, WARN: 1, OK: 2}

# A fourth, non-scoring severity (mode-awareness, below): a dimension that does
# not APPLY to this study's kind. It is neither a gap nor credit — it is excluded
# from the gap/warn/ok score and the addressed/total roll-up entirely. Used for
# the hypothesis-test dimensions on a descriptive / informational study.
NA = "not_applicable"

# Above this coefficient of variation, per-measure spread across seeds is
# treated as cross-seed disagreement (item 14 — replication scores AGREEMENT,
# not merely count). Deterministic threshold; tolerant of missing sub-fields.
_HIGH_CV = 0.5

# study_type vocabulary (critique #10). A study's intent governs how its
# passing tests are read: an ``exploratory`` study OBSERVES (its passes are not
# falsification credit); an ``adversarial`` study tries to BREAK the framework
# (stronger credit); a ``confirmatory`` study tests pre-declared criteria (its
# pass is downgraded when the criteria were not pre-registered — see #18);
# ``diagnostic`` studies probe a prior failure; ``standard`` is the default.
STUDY_TYPES = (
    "exploratory", "confirmatory", "diagnostic", "adversarial", "standard",
)

# Emitter kinds a run record may declare to evidence that its trajectory was
# PERSISTED (not just summarised). A run that emits via one of these — or that
# carries a run-db reference (db_path / run_db / …) — is reproducible from disk.
EMITTER_KINDS = ("sqlite", "parquet", "xarray")

# Keys on a run record that reference a persisted run database / output file.
_RUN_DB_REF_KEYS = ("db_path", "run_db", "runs_db", "db_file", "db", "run_db_path")


def run_is_emitter_backed(run: Any) -> bool:
    """True when a single ``runs[]`` record evidences emitter-backed persistence.

    A run is "persisted via an emitter" when its record either:

    * carries an ``emitter`` whose value (a string, or a dict's ``type`` /
      ``kind``) is one of :data:`EMITTER_KINDS` (sqlite / parquet / xarray), or
    * carries a non-empty run-db / output reference under any of
      :data:`_RUN_DB_REF_KEYS` (``db_path`` / ``run_db`` / ``runs_db`` / …).

    Pure and tolerant: a non-dict / malformed record returns ``False``.
    """
    if not isinstance(run, dict):
        return False
    em = run.get("emitter")
    if isinstance(em, str) and em.strip().lower() in EMITTER_KINDS:
        return True
    if isinstance(em, dict):
        kind = str(em.get("type") or em.get("kind") or em.get("emitter") or "").strip().lower()
        if kind in EMITTER_KINDS:
            return True
    for k in _RUN_DB_REF_KEYS:
        if _nonempty(run.get(k)):
            return True
    return False


def _study_type(spec: dict) -> str:
    """Return the study's intent type (critique #10), generalizing the old
    ``_is_adversarial`` helper.

    Reads the explicit ``study_type`` field, falling back to the legacy
    ``kind`` / ``study_kind`` aliases (so existing ``kind: adversarial`` studies
    keep working). An unknown / unset value degrades to ``"standard"``.
    """
    spec = spec or {}
    raw = str(
        spec.get("study_type")
        or spec.get("kind")
        or spec.get("study_kind")
        or ""
    ).strip().lower()
    return raw if raw in STUDY_TYPES else "standard"


def _as_list(v: Any) -> list:
    return v if isinstance(v, list) else ([] if v is None else [v])


def _nonempty(v: Any) -> bool:
    """True if a str/list field carries actual content."""
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, list):
        return any(str(x).strip() for x in v)
    return bool(v)


def _findings(spec: dict) -> list[dict]:
    out = []
    for f in _as_list(spec.get("findings")):
        if isinstance(f, dict):
            out.append(f)
        elif isinstance(f, str):
            out.append({"statement": f})
    return out


def _replicate_count(spec: dict) -> tuple[int, bool]:
    """Return (n_replicates, parameter_sweep) from declared fields.

    Prefers an explicit ``robustness`` block; else counts ``simulation_set``
    seeds; else counts recorded ``runs``.
    """
    rob = spec.get("robustness") or {}
    if isinstance(rob, dict):
        n = rob.get("n_replicates")
        seeds = rob.get("seeds")
        sweep = bool(rob.get("parameter_sweep"))
        if isinstance(n, int):
            return n, sweep
        if isinstance(seeds, list):
            return len(seeds), sweep
    # Derive from simulation_set seeds.
    seeds_total = 0
    for sim in _as_list(spec.get("simulation_set")):
        if isinstance(sim, dict):
            seeds_total += len(_as_list(sim.get("seeds")))
    if seeds_total:
        return seeds_total, False
    # Fall back to count of recorded runs.
    return len(_as_list(spec.get("runs"))), False


def _replication_agreement(spec: dict, n_rep: int) -> tuple[bool, str]:
    """Inspect ``robustness`` for cross-seed AGREEMENT (item 14).

    Returns ``(disagrees, reason)``. Two deterministic signals, both optional:

    * ``robustness.per_measure[*]`` — an explicit ``cv`` (coefficient of
      variation), else derived as ``|std / mean|``; a value above
      :data:`_HIGH_CV` means the measure is not stable across seeds.
    * ``robustness.seeds_with_advantage`` — count (int) or list of seeds, or a
      fraction in ``(0, 1]`` (float); no majority (``<= 0.5`` of replicates)
      means the seeds don't agree on the effect.

    When no agreement evidence is present, returns ``(False, "")`` — the
    replicate count alone stands. Tolerant of malformed sub-fields.
    """
    rob = spec.get("robustness")
    if not isinstance(rob, dict):
        return False, ""
    reasons: list[str] = []

    # 1. Per-measure coefficient of variation.
    for m in _as_list(rob.get("per_measure")):
        if not isinstance(m, dict):
            continue
        cv = m.get("cv")
        if cv is None:
            std = m.get("std")
            mean = m.get("mean")
            try:
                if std is not None and mean not in (None, 0):
                    cv = abs(float(std) / float(mean))
            except (TypeError, ValueError, ZeroDivisionError):
                cv = None
        try:
            if cv is not None and float(cv) > _HIGH_CV:
                reasons.append(f"{m.get('name') or 'a measure'} CoV={float(cv):.2f}")
        except (TypeError, ValueError):
            pass

    # 2. Majority agreement among seeds.
    swa = rob.get("seeds_with_advantage")
    frac = None
    if isinstance(swa, bool):
        frac = None
    elif isinstance(swa, list):
        frac = (len(swa) / n_rep) if n_rep else None
    elif isinstance(swa, int):
        frac = (swa / n_rep) if n_rep else None
    elif isinstance(swa, float):
        frac = swa if 0 < swa <= 1 else ((swa / n_rep) if n_rep else None)
    if frac is not None and frac <= 0.5:
        reasons.append("no majority of seeds shows the advantage")

    return (bool(reasons), "; ".join(reasons))


def _dim(id_: str, label: str, severity: str, detail: str, comments: list[str]) -> dict:
    return {"id": id_, "label": label, "severity": severity,
            "detail": detail, "comments": comments}
