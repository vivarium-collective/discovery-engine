"""EvidenceRecord — a quantitative predicted-vs-observed comparison with uncertainty.

Grading today answers pass/fail (``computed_outcomes``). The rubric and the out-of-sample
gate need the *quantitative* record: what the model/hypothesis predicted (with uncertainty),
what was observed, the signed residual, and whether the observation sits within the predicted
uncertainty — stamped with an immutable source (run, model version, grader).

This module DERIVES that record from data already on disk (``computed_outcomes`` +
``behavior_tests[].pass_if``); it does not touch the grading writer, so it adds no risk to the
measured path. ``holdout=True`` records are produced by the held-out evaluator
(``benchmark/holdout.py``) for conditions the engine never fit on. Pure; AI-free.
"""
from __future__ import annotations


def _num(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, dict):
        for k in ("value", "mean", "scalar"):
            if k in v:
                return _num(v[k])
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def band_prediction(pass_if: dict) -> tuple[float | None, float | None]:
    """A ``(value, uncertainty)`` point prediction summarizing an acceptance band.

    range ``{low, high}`` -> (midpoint, half-width); threshold ``{op, value|threshold}`` ->
    (threshold, None); equality ``{value, tolerance|epsilon}`` -> (value, tolerance). Pure."""
    if not isinstance(pass_if, dict):
        return None, None
    lo, hi = _num(pass_if.get("low")), _num(pass_if.get("high"))
    if lo is not None and hi is not None:
        return (lo + hi) / 2.0, abs(hi - lo) / 2.0
    thr = _num(pass_if.get("threshold"))
    if thr is not None:
        return thr, None
    val = _num(pass_if.get("value"))
    if val is not None:
        unc = _num(pass_if.get("tolerance"))
        if unc is None:
            unc = _num(pass_if.get("epsilon"))
        return val, unc
    return None, None


def build_evidence_record(observable: str, *, predicted: dict, observed: dict,
                          source: dict | None = None, conditions=None,
                          holdout: bool = False) -> dict:
    """Assemble an EvidenceRecord, computing ``residual`` and ``within_uncertainty``.

    ``predicted`` / ``observed`` are mappings carrying at least ``value`` (and optional
    ``uncertainty``); ``predicted`` may also carry the summarized ``band``. Pure."""
    pred = predicted if isinstance(predicted, dict) else {"value": _num(predicted)}
    obs = observed if isinstance(observed, dict) else {"value": _num(observed)}
    pv, ov = _num(pred.get("value")), _num(obs.get("value"))
    unc = _num(pred.get("uncertainty"))
    residual = (ov - pv) if (ov is not None and pv is not None) else None
    within = (abs(residual) <= unc) if (residual is not None and unc is not None) else None
    rec: dict = {
        "observable": str(observable),
        "predicted": {"value": pv, **({"uncertainty": unc} if unc is not None else {}),
                      **({"band": pred["band"]} if isinstance(pred.get("band"), dict) else {})},
        "observed": {"value": ov,
                     **({"uncertainty": _num(obs.get("uncertainty"))}
                        if _num(obs.get("uncertainty")) is not None else {})},
        "residual": residual,
        "within_uncertainty": within,
        "holdout": bool(holdout),
        "source": dict(source or {}),
    }
    if conditions is not None:
        rec["conditions"] = conditions
    return rec


def _tests_by_name(study: dict) -> dict:
    out = {}
    for t in (study.get("behavior_tests") or study.get("tests") or []):
        if isinstance(t, dict) and t.get("name"):
            out[t["name"]] = t
    return out


def _observable_of(test: dict, name: str) -> str:
    measure = test.get("measure") if isinstance(test, dict) else None
    if isinstance(measure, dict):
        for k in ("observable", "path"):
            if str(measure.get(k) or "").strip():
                return str(measure[k])
    return name


def evidence_records_for_run(study: dict, run: dict) -> dict:
    """Derive ``{test_name: EvidenceRecord}`` for one run, from its ``computed_outcomes``
    (observed) and the study's ``behavior_tests[].pass_if`` (predicted band). Pure."""
    tests = _tests_by_name(study or {})
    co = (run or {}).get("computed_outcomes") or {}
    grader = ((co.get("_grader") or {}).get("version")) if isinstance(co.get("_grader"), dict) else None
    source_base = {
        "run_id": run.get("run_id") or run.get("id") or run.get("name"),
        "model_version": run.get("model_version"),
        "grader": grader,
    }
    out: dict = {}
    for tname, entry in co.items():
        if tname.startswith("_") or not isinstance(entry, dict):
            continue
        test = tests.get(tname, {})
        value, unc = band_prediction(test.get("pass_if") or {})
        pred = {"value": value}
        if unc is not None:
            pred["uncertainty"] = unc
        if isinstance(test.get("pass_if"), dict):
            pred["band"] = test["pass_if"]
        out[tname] = build_evidence_record(
            _observable_of(test, tname),
            predicted=pred,
            observed={"value": _num(entry.get("measured_value"))},
            source={**source_base, "method": entry.get("evaluated_by")},
            conditions=run.get("params"))
    return out


def evidence_records_for_study(study: dict) -> dict:
    """Derive ``{run_key: {test_name: EvidenceRecord}}`` across all of a study's runs. Pure."""
    out: dict = {}
    for run in (study or {}).get("runs") or []:
        if not isinstance(run, dict):
            continue
        key = run.get("name") or run.get("run_id") or run.get("id")
        if key is None:
            continue
        recs = evidence_records_for_run(study, run)
        if recs:
            out[key] = recs
    return out
