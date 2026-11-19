"""Experiment utility — rank candidate experiments by expected information gain (FR-03.02).

The disjoint-band design (`design_discriminating_study`) returns ``None`` unless two
hypotheses' predicted bands are *already* disjoint on one observable. This generalizes it:
given competing hypotheses and a set of candidate conditions, score each condition by how
much the hypotheses' predictions **diverge** there relative to their uncertainty — an
expected-information-gain proxy — and pick the most discriminating one, or **abstain**
(a NO-EXPERIMENT stop) when nothing meaningfully separates them.

Model-agnostic: it takes a ``predict(hypothesis, condition) -> value | {value, uncertainty}``
callable (the hypothesis's model prediction under that condition), so it is testable now with
toy models and wires later to the calibrated predictive distributions the plan calls for. When
predictions carry no uncertainty the score is a bare separation heuristic and the caller should
declare the missing likelihood assumptions (AC-03.02). Pure; AI-free.
"""
from __future__ import annotations


def _value_uncertainty(p) -> tuple[float | None, float | None]:
    """Coerce a prediction to ``(value, uncertainty)``. Accepts a number, a
    ``{value, uncertainty}`` mapping, or a ``{low, high}`` band (→ midpoint, half-width)."""
    if isinstance(p, dict):
        v = p.get("value")
        u = p.get("uncertainty")
        lo, hi = p.get("low"), p.get("high")
        if v is None and lo is not None and hi is not None:
            v = (float(lo) + float(hi)) / 2.0
            if u is None:
                u = abs(float(hi) - float(lo)) / 2.0
        return (None if v is None else float(v), None if u is None else float(u))
    try:
        return (float(p), None)
    except (TypeError, ValueError):
        return (None, None)


def discrimination_score(predictions) -> float:
    """0..1: how distinguishable ≥2 hypotheses' predictions are under one condition, given
    their uncertainty. ``spread / (spread + 2·mean_uncertainty)`` — 1 = well-separated
    relative to uncertainty, 0 = overlapping. Fewer than two numeric predictions → 0. Pure."""
    vus = [_value_uncertainty(p) for p in (predictions or [])]
    vals = [v for v, _ in vus if v is not None]
    if len(vals) < 2:
        return 0.0
    spread = max(vals) - min(vals)
    uncs = [u for _, u in vus if u is not None]
    unc = (sum(uncs) / len(uncs)) if uncs else 0.0
    denom = spread + 2.0 * unc
    return 0.0 if denom <= 0 else spread / denom


def rank_experiments(hypotheses, conditions, *, predict) -> list[dict]:
    """Rank candidate ``conditions`` by how well they discriminate ``hypotheses``.
    ``predict(hypothesis, condition)`` returns that hypothesis's prediction. Returns
    ``[{condition, score, predictions}]`` sorted by score descending (stable). Pure."""
    rows: list[dict] = []
    for c in conditions or []:
        preds = [predict(h, c) for h in (hypotheses or [])]
        rows.append({"condition": c, "score": discrimination_score(preds), "predictions": preds})
    rows.sort(key=lambda r: -r["score"])
    return rows


def best_experiment(hypotheses, conditions, *, predict, min_score: float = 0.5) -> dict:
    """The most informative experiment, or a **NO-EXPERIMENT** stop when the best
    discrimination is below ``min_score`` (nothing meaningfully separates the hypotheses).

    Returns ``{outcome: "experiment", condition, score, predictions, ranked}`` or the typed
    stop ``{outcome: "stop", reason: "no_informative_experiment", ...}`` (the plan's legitimate
    abstention — not a failure). Pure."""
    ranked = rank_experiments(hypotheses, conditions, predict=predict)
    return _select(ranked, min_score)


def _select(ranked: list[dict], min_score: float) -> dict:
    """Pick the top-ranked experiment, or a NO-EXPERIMENT stop below ``min_score``."""
    top = ranked[0] if ranked else None
    if top is None or top["score"] < min_score:
        from discovery_engine.discover._policy import NO_INFORMATIVE_EXPERIMENT, stop  # lazy: avoid cycle
        detail = (f"best discrimination {top['score']:.3f} < {min_score}"
                  if top else "no candidate conditions")
        return stop(NO_INFORMATIVE_EXPERIMENT, detail=detail)
    return {"outcome": "experiment", "condition": top["condition"], "score": top["score"],
            "predictions": top["predictions"], "ranked": ranked}


def _expected_as_band(expected) -> dict | None:
    """Coerce a hypothesis prediction's ``expected`` to the scorer's ``{low, high}``,
    or ``None`` when it is non-numeric or one-sided/unbounded. Reuses the engine's
    canonical interval parser so it reads the same bands the disjoint-band test does."""
    import math

    from discovery_engine.phases.hypotheses import _interval  # lazy: avoid cycle
    iv = _interval(expected)
    if not iv:
        return None
    lo, hi = iv
    if not (math.isfinite(lo) and math.isfinite(hi)):
        return None
    return {"low": lo, "high": hi}


def rank_study_experiments(inv_spec: dict, study_slug: str, *, min_score: float = 0.3) -> dict:
    """Rank a study's candidate experiments — each a (observable, intervention ``when``)
    regime — by how well it discriminates the study's live competing hypotheses, scoring
    over the predicted bands the hypotheses already declare. Generalizes the disjoint-band
    design: it chooses the MOST separating regime even when no single pair is fully disjoint,
    and abstains (NO-EXPERIMENT) when even the best regime barely separates them.

    Pure; derives entirely from ``inv_spec`` (no model/predict callable needed, since the
    hypotheses' declared bands are the predictions). Returns ``best_experiment``'s shape:
    ``{outcome: "experiment", condition: {observable, when}, score, ranked}`` or the typed
    NO-EXPERIMENT stop. ``min_score`` defaults below 0.5 so partially-separating regimes —
    which the disjoint-band path rejects outright — are still recommended, with the score
    attached so a reviewer sees how strong the discrimination is."""
    from discovery_engine.phases.hypotheses import _as_list, _RETIRED_STATUS  # lazy: avoid cycle
    inv_spec = inv_spec or {}
    hyps = [h for h in _as_list(inv_spec.get("hypotheses"))
            if isinstance(h, dict) and h.get("study") == study_slug
            and str(h.get("status", "open")).strip().lower() not in _RETIRED_STATUS]
    buckets: dict = {}
    for h in hyps:
        for p in _as_list(h.get("predictions")):
            if not isinstance(p, dict) or not p.get("observable"):
                continue
            band = _expected_as_band(p.get("expected"))
            if band is None:
                continue
            when = p.get("when") or {}
            key = (p["observable"], tuple(sorted(when.items())))
            buckets.setdefault(key, {"observable": p["observable"], "when": dict(when), "bands": []})
            buckets[key]["bands"].append(band)
    ranked = [{"condition": {"observable": b["observable"], "when": b["when"]},
               "score": discrimination_score(b["bands"]), "predictions": b["bands"]}
              for b in buckets.values() if len(b["bands"]) >= 2]
    ranked.sort(key=lambda r: -r["score"])
    return _select(ranked, min_score)
