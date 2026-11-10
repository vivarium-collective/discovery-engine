"""Framework-self metrics (critique #26) — aggregate the EXISTING rigor fields
across many studies / investigations into a single scorecard. Each metric is a
``{fraction, count, total}`` triple (fraction is None when total == 0). Pure,
deterministic, and tolerant of missing inputs.
"""
from __future__ import annotations

from discovery_engine.reporting.rigor.bands import _test_threshold_sourced
from discovery_engine.reporting.rigor.common import (
    _as_list,
    _findings,
    _nonempty,
    _replicate_count,
    _study_type,
    run_is_emitter_backed,
)
from discovery_engine.reporting.rigor.evidence_weight import _excluded_alternatives


def _frac(count: int, total: int) -> dict:
    return {
        "fraction": (count / total) if total else None,
        "count": count,
        "total": total,
    }


def _has_discriminating_control(spec: dict) -> bool:
    """≥1 negative/adversarial control that ran and recorded a PASS (reuses the
    same ``discriminating`` predicate as :func:`study_rigor`)."""
    for c in _as_list((spec or {}).get("controls")):
        if (isinstance(c, dict)
                and (c.get("kind") or "").lower() in ("negative", "adversarial")
                and str(c.get("result", "")).upper() == "PASS"
                and _nonempty(c.get("observed"))):
            return True
    return False


def _study_passed(spec: dict) -> bool:
    pg = (spec or {}).get("pipeline_gate") or {}
    ge = (pg.get("gate_evaluator") or {}) if isinstance(pg, dict) else {}
    res = str(ge.get("result") or (spec or {}).get("gate_status") or "").lower()
    return res in ("passed", "pass")


def _study_exposes_falsification(spec: dict) -> bool:
    """A study contributes falsification exposure when it is adversarial, carries
    a discriminating negative control, or (being non-exploratory) did NOT pass."""
    st = _study_type(spec)
    if st == "adversarial":
        return True
    if _has_discriminating_control(spec):
        return True
    if st != "exploratory" and not _study_passed(spec):
        return True
    return False


def framework_metrics(study_specs: list[dict], inv_specs: list[dict]) -> dict:
    """Aggregate existing rigor fields across studies/investigations (critique #26).

    ``study_specs`` — member study specs (any order). ``inv_specs`` —
    investigation specs (for AC coverage). Returns a dict of
    ``{fraction, count, total}`` metrics plus ``n_studies`` / ``n_investigations``.
    Pure; tolerant of ``None`` / empty inputs (a metric over an empty set has
    ``fraction: None``). Reuses the existing predicates so it can't drift from
    the per-study / per-investigation scorecards.
    """
    study_specs = [s for s in (study_specs or []) if isinstance(s, dict)]
    inv_specs = [i for i in (inv_specs or []) if isinstance(i, dict)]
    n_studies = len(study_specs)

    # 1. Discriminating negative/adversarial control coverage.
    n_disc = sum(1 for s in study_specs if _has_discriminating_control(s))

    # 2. Interpretation-tier findings: emergent vs missing mechanism_origin.
    interp_total = 0
    emergent = 0
    missing_origin = 0
    for s in study_specs:
        for f in _findings(s):
            if (f.get("tier") or "").lower() != "interpretation":
                continue
            interp_total += 1
            origin = (f.get("mechanism_origin") or "").strip().lower()
            if origin == "emergent":
                emergent += 1
            elif not origin:
                missing_origin += 1

    # 3. Threshold-provenance coverage — behavior_tests / tests carrying a
    #    quantitative band (pass_if or calibration_anchor) that also declare a
    #    source (cites or calibration_anchor).
    band_total = 0
    band_cited = 0
    for s in study_specs:
        for section in ("behavior_tests", "tests"):
            for t in _as_list(s.get(section)):
                if not isinstance(t, dict):
                    continue
                if not (t.get("pass_if") or t.get("calibration_anchor")):
                    continue
                band_total += 1
                # A band is "sourced" by a literature link (cites /
                # calibration_anchor) OR an honest pass_if.provenance.kind (#9).
                if _test_threshold_sourced(t):
                    band_cited += 1

    # 4. Replication coverage — ≥3 replicates (reuse _replicate_count).
    n_replicated = sum(1 for s in study_specs if _replicate_count(s)[0] >= 3)

    # 5. AC coverage — 1 - |gaps|/|criteria| over all investigations' acceptance
    #    criteria (a criterion is a gap when it has no ``study:`` link — the same
    #    predicate linkage_index.ac_gating_matrix uses).
    ac_total = 0
    ac_covered = 0
    for inv in inv_specs:
        for crit in _as_list(inv.get("acceptance_criteria")):
            if not isinstance(crit, dict):
                continue
            ac_total += 1
            if crit.get("study"):
                ac_covered += 1

    # 6. Verdict-divergence rate (reuse study_verdict.diverges_from_authored).
    n_divergent = 0
    try:
        from discovery_engine.study.verdict import diverges_from_authored
        n_divergent = sum(1 for s in study_specs if diverges_from_authored(s))
    except Exception:  # noqa: BLE001 — defensive cross-module import
        pass

    # 7. Falsification-exposure rate.
    n_exposed = sum(1 for s in study_specs if _study_exposes_falsification(s))

    # 8. Alternatives-excluded rate.
    n_alts_excluded = sum(1 for s in study_specs if _excluded_alternatives(s))

    # 9. Emitter coverage — of the studies that record runs, the fraction whose
    #    runs are emitter-backed (≥1 run carries an emitter / run-db reference).
    #    Denominator is studies-with-runs (a study with no runs is not a
    #    persistence candidate), mirroring threshold_provenance's band-scoped rate.
    n_studies_with_runs = 0
    n_emitter_backed = 0
    for s in study_specs:
        runs = [r for r in _as_list(s.get("runs")) if isinstance(r, dict)]
        if not runs:
            continue
        n_studies_with_runs += 1
        if any(run_is_emitter_backed(r) for r in runs):
            n_emitter_backed += 1

    return {
        "n_studies": n_studies,
        "n_investigations": len(inv_specs),
        "discriminating_controls": _frac(n_disc, n_studies),
        "emergent_interpretations": _frac(emergent, interp_total),
        "missing_mechanism_origin": _frac(missing_origin, interp_total),
        "threshold_provenance": _frac(band_cited, band_total),
        "replication_coverage": _frac(n_replicated, n_studies),
        "ac_coverage": _frac(ac_covered, ac_total),
        "verdict_divergence": _frac(n_divergent, n_studies),
        "falsification_exposure": _frac(n_exposed, n_studies),
        "alternatives_excluded": _frac(n_alts_excluded, n_studies),
        "emitter_coverage": _frac(n_emitter_backed, n_studies_with_runs),
    }
