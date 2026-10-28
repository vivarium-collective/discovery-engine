"""Competing-hypotheses support roll-up (critique #6 + #16).

A skeptical reviewer of a multi-study investigation asks: *which competing
explanations did you actually put forward, and what does the accumulated
evidence do to each one?* The framework already records, per study, the
quantitative evidence (``findings[].evidence.observed`` keyed by measure) and
the Decide-phase competing explanations
(``discovery_implications.alternate_hypotheses``). This module folds those into
a per-hypothesis ``support_log`` — a transparent ▲supports / ▼weakens /
⊘excludes tally (NOT a Bayesian update).

Shapes (all OPTIONAL, additive)
-------------------------------
investigation.yaml::

    hypotheses:
      - id: closure-without-viability
        statement: "Operational closure can hold without viability."
        predictions:
          - observable: closure_gap
            expected: {low: 0.0, high: 0.1}
          - observable: survival_advantage
            expected: {op: "<=", value: 0.0}
        status: open
        support_log:        # COMPUTED by rollup_support — do not author
          - {study: study-1, observation: "closure_gap=0.04 vs {low:0,high:0.1}", delta: supports}

study.yaml (a study's Decide-phase synthesis can link to a hypothesis)::

    discovery_implications:
      alternate_hypotheses:
        - claim: "Survival gain is plain movement-to-resources."
          hypothesis_id: closure-without-viability   # links to inv hypothesis
          status: excluded                            # excluded | not-excluded | untested
          evidence_for: ["..."]
          evidence_against: ["non-sensing motile control still gains"]

Everything here is pure and deterministic (no LLM/AI) so the AI-free dashboard
can call it directly.
"""
from __future__ import annotations

from typing import Any

# The three delta verbs a support_log entry can carry (the #6 contract).
SUPPORTS = "supports"
WEAKENS = "weakens"
EXCLUDES = "excludes"

# Relative tolerance for matching an observed scalar to a scalar ``expected``
# (when no band / operator is given). Deterministic; documented for callers.
_SCALAR_REL_TOL = 0.05


def _as_list(v: Any) -> list:
    return v if isinstance(v, list) else ([] if v is None else [v])


def _study_slug(study: dict) -> str:
    s = study or {}
    return s.get("name") or s.get("slug") or "study"


def _findings(study: dict) -> list[dict]:
    out: list[dict] = []
    for f in _as_list((study or {}).get("findings")):
        if isinstance(f, dict):
            out.append(f)
        elif isinstance(f, str):
            out.append({"statement": f})
    return out


def _finding_measure_observed(finding: dict) -> tuple[str | None, Any]:
    """Return ``(measure_name, observed_value)`` for a finding.

    The measure name is keyed primarily off ``evidence.measure`` /
    ``evidence.observable`` / ``evidence.from_test`` (the per-finding contract),
    falling back to the finding's own ``measure`` / ``name``. The observed value
    is ``evidence.observed`` (written by the finding-observations pass), falling
    back to ``evidence.measured_value``. Either may be ``None`` when absent.
    """
    ev = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    measure = None
    for k in ("measure", "observable", "from_test"):
        v = ev.get(k)
        if isinstance(v, str) and v.strip():
            measure = v.strip()
            break
    if measure is None:
        for k in ("measure", "observable", "name"):
            v = finding.get(k)
            if isinstance(v, str) and v.strip():
                measure = v.strip()
                break
    observed = ev.get("observed")
    if observed is None:
        observed = ev.get("measured_value")
    return measure, observed


def _to_float(v: Any) -> float | None:
    try:
        if isinstance(v, bool):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _expected_band(expected: Any) -> dict | None:
    """Extract a structured band/operator from a prediction's ``expected``.

    Returns ``{"low": .., "high": ..}`` / ``{"threshold": ..}`` /
    ``{"op": "<=", "value": ..}`` / ``{"value": ..}`` when numeric bounds are
    present; ``None`` for a non-numeric / unparseable expected.
    """
    if isinstance(expected, dict):
        band: dict = {}
        for k in ("low", "high", "threshold", "value"):
            if isinstance(expected.get(k), (int, float)) and not isinstance(expected.get(k), bool):
                band[k] = float(expected[k])
        op = expected.get("op")
        if isinstance(op, str) and op.strip():
            band["op"] = op.strip()
        return band or None
    f = _to_float(expected)
    if f is not None:
        return {"value": f}
    # Tolerate a prose ``expected`` that LEADS with a comparator + number, e.g.
    # "<= 0 (closed) for the genuine loop" or "> 0 — opens the gap". Authors
    # write predictions as readable sentences; parse the machine-comparable head.
    if isinstance(expected, str):
        import re
        m = re.match(r"\s*(<=|>=|==|!=|<|>|=)?\s*(-?\d+(?:\.\d+)?)", expected)
        if m:
            op, num = m.group(1), float(m.group(2))
            if not op or op == "=":
                return {"value": num}
            return {"op": "==" if op == "=" else op, "value": num}
    return None


def _evaluate(observed: float, band: dict) -> bool:
    """Pure predicate: does ``observed`` satisfy the expected band/operator?"""
    op = band.get("op")
    if "low" in band and "high" in band:
        return band["low"] <= observed <= band["high"]
    if op in ("<=", "lte"):
        ref = band.get("value", band.get("threshold"))
        return ref is not None and observed <= ref
    if op in (">=", "gte"):
        ref = band.get("value", band.get("threshold"))
        return ref is not None and observed >= ref
    if op == "<":
        ref = band.get("value", band.get("threshold"))
        return ref is not None and observed < ref
    if op == ">":
        ref = band.get("value", band.get("threshold"))
        return ref is not None and observed > ref
    if op in ("==", "eq"):
        ref = band.get("value", band.get("threshold"))
        return ref is not None and observed == ref
    if op in ("!=", "ne"):
        ref = band.get("value", band.get("threshold"))
        return ref is not None and observed != ref
    if "threshold" in band:
        # Bare threshold with no operator → default to >= (the common "at least"
        # acceptance reading); documented contract.
        return observed >= band["threshold"]
    if "value" in band:
        ref = band["value"]
        if ref == 0:
            return observed == 0
        return abs(observed - ref) / abs(ref) <= _SCALAR_REL_TOL
    return False


def score_support(hypothesis: dict, study_specs: list[dict]) -> list[dict]:
    """Tally the quantitative support each study lends a hypothesis (#16).

    For each study finding carrying an ``evidence.observed`` keyed by a measure
    that matches one of the hypothesis ``predictions[].observable``, compare the
    observed value to the prediction's ``expected`` band/operator → a
    ``supports`` / ``weakens`` entry. Transparent tally, NOT a Bayesian update.

    Returns ``list[{study, observation, delta, discriminating}]``. Only findings
    whose measure matches a prediction AND carry a numeric observed value are
    included (the rest cannot discriminate). ``discriminating`` is always True
    here (a conclusive numeric comparison was made); the field exists so callers
    can distinguish quantitative entries from the prose entries folded in by
    :func:`rollup_support`.
    """
    hypothesis = hypothesis or {}
    preds: dict[str, Any] = {}
    for p in _as_list(hypothesis.get("predictions")):
        if not isinstance(p, dict):
            continue
        obs = p.get("observable")
        if isinstance(obs, str) and obs.strip():
            preds[obs.strip().lower()] = p.get("expected")
    if not preds:
        return []

    log: list[dict] = []
    for study in study_specs or []:
        if not isinstance(study, dict):
            continue
        slug = _study_slug(study)
        for finding in _findings(study):
            measure, observed = _finding_measure_observed(finding)
            if not measure:
                continue
            # A prediction's ``observable`` may name the behavior-test (e.g.
            # ``operational-closure``) OR the underlying measure field (e.g.
            # ``closure_gap_size``). Resolve both so authored predictions that
            # reference the measure field still match findings keyed by test.
            candidates = {measure.lower()}
            field = _test_measure_field(study, measure)
            if field:
                candidates.add(field.lower())
            matched = next((c for c in candidates if c in preds), None)
            if matched is None:
                continue
            ov = _to_float(observed)
            if ov is None:
                continue
            band = _expected_band(preds[matched])
            if band is None:
                continue
            ok = _evaluate(ov, band)
            log.append({
                "study": slug,
                "observation": f"{matched}={observed} vs expected {preds[matched]}",
                "delta": SUPPORTS if ok else WEAKENS,
                "discriminating": True,
            })
    return log


def _test_measure_field(study: dict, test_name: str) -> str | None:
    """Resolve a behavior-test name to its ``measure.field`` (so a prediction
    that names the measure field matches a finding keyed by the test name)."""
    if not isinstance(study, dict):
        return None
    for t in study.get("behavior_tests") or []:
        if isinstance(t, dict) and t.get("name") == test_name:
            m = t.get("measure")
            if isinstance(m, dict) and isinstance(m.get("field"), str):
                return m["field"].strip()
    return None


def _alt_entries(study: dict) -> list[dict]:
    """A study's competing explanations, DI-synthesis preferred, top-level
    fallback (same source preference as the rigor ``alternatives`` dimension)."""
    di = (study or {}).get("discovery_implications") or {}
    alts: list[dict] = []
    if isinstance(di, dict):
        alts = [a for a in _as_list(di.get("alternate_hypotheses")) if isinstance(a, dict)]
    if not alts:
        alts = [a for a in _as_list((study or {}).get("alternative_hypotheses")) if isinstance(a, dict)]
    return alts


def compute_support_log(hypothesis: dict, study_specs: list[dict]) -> list[dict]:
    """The full support_log for one hypothesis: the #16 quantitative match plus
    the #6 prose fold from each study's ``alternate_hypotheses`` matched by
    ``hypothesis_id``.

    Returns ``list[{study, observation, delta}]`` (the quantitative entries also
    carry ``discriminating: True``).
    """
    hypothesis = hypothesis or {}
    hid = hypothesis.get("id")
    log: list[dict] = list(score_support(hypothesis, study_specs))

    if hid:
        for study in study_specs or []:
            if not isinstance(study, dict):
                continue
            slug = _study_slug(study)
            for alt in _alt_entries(study):
                if alt.get("hypothesis_id") != hid:
                    continue
                status = str(alt.get("status") or "").strip().lower()
                claim = alt.get("claim") or alt.get("discriminated_by") or "competing explanation"
                if status == "excluded":
                    log.append({
                        "study": slug,
                        "observation": f"alternative excluded: {claim}",
                        "delta": EXCLUDES,
                    })
                for ev in _as_list(alt.get("evidence_for")):
                    if str(ev).strip():
                        log.append({"study": slug, "observation": str(ev),
                                    "delta": SUPPORTS})
                for ev in _as_list(alt.get("evidence_against")):
                    if str(ev).strip():
                        log.append({"study": slug, "observation": str(ev),
                                    "delta": WEAKENS})
    return log


def rollup_support(inv_spec: dict, study_specs: list[dict]) -> dict:
    """Return a copy of ``inv_spec`` with each ``hypotheses[].support_log``
    populated by :func:`compute_support_log`.

    Pure: does not mutate the input. Hypotheses without an ``id`` still receive
    their quantitative (#16) support_log. A non-list / absent ``hypotheses`` is
    returned untouched.
    """
    inv_spec = inv_spec or {}
    hyps = inv_spec.get("hypotheses")
    if not isinstance(hyps, list):
        return dict(inv_spec)

    new_hyps: list[Any] = []
    for h in hyps:
        if not isinstance(h, dict):
            new_hyps.append(h)
            continue
        h2 = dict(h)
        h2["support_log"] = compute_support_log(h, study_specs)
        new_hyps.append(h2)

    out = dict(inv_spec)
    out["hypotheses"] = new_hyps
    return out


def support_summary(support_log: list[dict]) -> dict:
    """A ``{supports, weakens, excludes, net}`` tally over a support_log."""
    counts = {SUPPORTS: 0, WEAKENS: 0, EXCLUDES: 0}
    for entry in support_log or []:
        d = (entry or {}).get("delta")
        if d in counts:
            counts[d] += 1
    return {
        "supports": counts[SUPPORTS],
        "weakens": counts[WEAKENS],
        "excludes": counts[EXCLUDES],
        # Net = corroborating evidence (supports + a competitor excluded) minus
        # weakening evidence. A transparent scalar, not a probability.
        "net": counts[SUPPORTS] + counts[EXCLUDES] - counts[WEAKENS],
    }
