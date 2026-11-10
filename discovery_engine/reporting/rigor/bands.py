"""Threshold provenance + sensitivity (critique #9).

Numeric acceptance bands: finding the band tests on a study, deciding whether a
band's cutoff is sourced, and re-evaluating a test's pass predicate across
scaled cutoffs (``threshold_sensitivity``). The canonical band detector is
re-exported from :mod:`discovery_engine.study.band_provenance` as
``_has_numeric_band``.
"""
from __future__ import annotations

# Canonical band detector — see band_provenance.has_numeric_band's docstring
# for the pass_if.value reconciliation rule (numeric value = band regardless
# of comparator op; non-numeric value, e.g. a config-selector, is not).
from discovery_engine.study.band_provenance import has_numeric_band as _has_numeric_band

from discovery_engine.reporting.rigor.common import _as_list

# threshold-provenance vocabulary (critique #9). Where an acceptance threshold
# came from — distinct from ``cites`` (a literature source link).
THRESHOLD_PROVENANCE_KINDS = (
    "theory", "calibration", "literature", "expert", "exploratory", "post_hoc",
)


def _study_test_entries(spec: dict) -> list[dict]:
    """Every behavior_tests[] / tests[] entry (dicts only)."""
    out: list[dict] = []
    for section in ("behavior_tests", "tests"):
        for t in _as_list((spec or {}).get(section)):
            if isinstance(t, dict):
                out.append(t)
    return out


# _has_numeric_band: see the top-of-file import — was a local reimplementation
# here that disagreed with band_provenance.py / report_linter.py.


def _numeric_band_tests(spec: dict) -> list[dict]:
    return [t for t in _study_test_entries(spec) if _has_numeric_band(t)]


def _test_threshold_sourced(test: dict) -> bool:
    """A numeric-band test is "sourced" when it links a literature source
    (``cites`` on the test or its ``calibration_anchor``) OR declares an honest
    ``pass_if.provenance.kind`` (critique #9)."""
    if not isinstance(test, dict):
        return False
    if test.get("cites"):
        return True
    anch = test.get("calibration_anchor")
    if isinstance(anch, dict) and (anch.get("cites") or anch.get("literature_target") is not None):
        return True
    pass_if = test.get("pass_if")
    if isinstance(pass_if, dict):
        prov = pass_if.get("provenance")
        if isinstance(prov, dict) and str(prov.get("kind") or "").strip():
            return True
    return False


def threshold_sensitivity(spec: dict, test_name: str,
                          deltas: tuple[float, ...] = (-0.2, -0.1, 0.1, 0.2)) -> list[dict]:
    """Re-evaluate a test's pass predicate against its RECORDED observed value
    across cutoffs scaled by ``deltas`` (critique #9).

    Pure ``spec -> list[{delta, cutoff, result}]``. Finds the named test, reads
    its numeric band (via :func:`band_provenance._band_from_pass_if`), reads the
    canonical run's recorded ``outcomes[test_name].observed`` (falling back to
    ``measured_value``), then for each delta scales every numeric band bound by
    ``(1 + delta)`` and re-checks whether the observed value still satisfies the
    band. Shows how brittle a pass/fail is to the exact cutoff.

    Returns ``[]`` (the guard) when the test is not found, has no numeric band,
    or has no recorded observed value — there is nothing to re-evaluate.
    ``cutoff`` is the scaled band dict; ``result`` is ``"PASS"`` / ``"FAIL"``.
    """
    spec = spec or {}
    test = None
    for t in _study_test_entries(spec):
        if t.get("name") == test_name:
            test = t
            break
    if test is None:
        return []

    try:
        from discovery_engine.study.band_provenance import _band_from_pass_if
    except Exception:  # noqa: BLE001 — defensive
        _band_from_pass_if = None  # type: ignore
    band = _band_from_pass_if(test.get("pass_if")) if _band_from_pass_if else None
    pass_if = test.get("pass_if") if isinstance(test.get("pass_if"), dict) else {}
    op = pass_if.get("op")
    # Fallback for the {op, value} pass_if shape (which _band_from_pass_if, keyed
    # on low/high/threshold, does not recognise): treat ``value`` as the cutoff.
    if not band:
        val = pass_if.get("value")
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            band = {"threshold": val}
    if not band:
        return []

    # Recorded observed value from the canonical run's outcomes.
    try:
        from discovery_engine.study.outcomes import canonical_outcomes
        outcomes = canonical_outcomes(spec)
    except Exception:  # noqa: BLE001 — defensive
        outcomes = {}
    out = outcomes.get(test_name) if isinstance(outcomes, dict) else None
    observed = None
    if isinstance(out, dict):
        observed = out.get("observed")
        if observed is None:
            observed = out.get("measured_value")
    try:
        observed = float(observed)
    except (TypeError, ValueError):
        return []

    results: list[dict] = []
    for d in deltas:
        scaled = {k: (v * (1 + d)) for k, v in band.items()
                  if isinstance(v, (int, float)) and not isinstance(v, bool)}
        results.append({
            "delta": d,
            "cutoff": scaled,
            "result": "PASS" if _observed_satisfies(observed, scaled, op) else "FAIL",
        })
    return results


def _observed_satisfies(observed: float, band: dict, op: str | None) -> bool:
    """Pure predicate: does a scalar ``observed`` satisfy a numeric band?

    Handles range bands (low/high), explicit comparator ``op`` against a
    threshold/value, and a bare threshold (defaults to ``>=``).
    """
    if "low" in band and "high" in band:
        return band["low"] <= observed <= band["high"]
    ref = band.get("threshold", band.get("value"))
    o = (op or "").strip()
    if o in ("<=", "lte"):
        return ref is not None and observed <= ref
    if o in (">=", "gte"):
        return ref is not None and observed >= ref
    if o == "<":
        return ref is not None and observed < ref
    if o == ">":
        return ref is not None and observed > ref
    if o in ("==", "eq"):
        return ref is not None and observed == ref
    if o in ("!=", "ne"):
        return ref is not None and observed != ref
    if "threshold" in band:
        return observed >= band["threshold"]
    if "value" in band:
        return observed == band["value"]
    return False
