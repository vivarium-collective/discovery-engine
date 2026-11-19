"""Scoring rubric — a weighted, multi-objective benchmark score.

The plan's rule: never accept a large composite or an attractive report as a substitute for
evidence. A run is scored 0..1 per category (the weights below are the plan's starting points)
and summed to one comparable total. A category with no input is *not measured* — it is excluded
and the remaining weights renormalize, so a partial run still yields an honest 0..1 over what
was actually measured (never silently counted as 0). Pure; AI-free.
"""
from __future__ import annotations

from dataclasses import dataclass

# category -> weight (the plan's suggested baseline, to calibrate with expert evaluation)
WEIGHTS = {
    "mechanism_id": 0.25,
    "heldout_prediction": 0.20,
    "experimental_value": 0.15,
    "composition_validity": 0.15,
    "provenance_honesty": 0.15,
    "explanatory_clarity": 0.10,
}


@dataclass(frozen=True)
class RubricScore:
    categories: dict        # measured category -> 0..1
    total: float            # weighted mean over the measured categories (0..1)
    missing: tuple          # categories with no input (excluded + reweighted)
    weights: dict

    def beats(self, other: "RubricScore") -> bool:
        return self.total > other.total


def score_rubric(categories: dict, *, weights: dict = WEIGHTS) -> RubricScore:
    """Weighted score over ``categories`` (name -> 0..1, or ``None`` = not measured).

    A ``None`` (or absent) category is excluded and the remaining weights renormalize, so the
    total is always a 0..1 number over what was actually measured. Values are clamped to [0, 1]."""
    present = {k: max(0.0, min(1.0, float(v)))
               for k, v in categories.items() if v is not None and k in weights}
    missing = tuple(k for k in weights if k not in present)
    wsum = sum(weights[k] for k in present) or 1.0
    total = sum(weights[k] * present[k] for k in present) / wsum
    return RubricScore(categories=present, total=total, missing=missing, weights=dict(weights))


def _residual_to_score(holdout) -> float | None:
    """Map a HoldoutScore to a 0..1 held-out-prediction category: prefer the within-uncertainty
    fraction; else a bounded transform of the mean |residual| (1 at 0, →0 as it grows)."""
    if holdout is None:
        return None
    if getattr(holdout, "within_fraction", None) is not None:
        return float(holdout.within_fraction)
    mar = getattr(holdout, "mean_abs_residual", None)
    if mar is None:
        return None
    return 1.0 / (1.0 + float(mar))


def categories_from(*, metrics=None, holdout=None, gate_violations=None,
                    composition=None, experimental_value=None,
                    explanatory=None) -> dict:
    """Assemble the rubric categories from the benchmark outputs. Categories whose inputs are
    absent are left out (scored as *not measured*, not 0). ``metrics`` is a benchmark
    ``Metrics``; ``holdout`` a ``HoldoutScore``; ``gate_violations`` the hard-gate result."""
    cats: dict = {}
    if metrics is not None:
        cats["mechanism_id"] = 1.0 if getattr(metrics, "mechanism_proposed", False) else 0.0
    hp = _residual_to_score(holdout)
    if hp is not None:
        cats["heldout_prediction"] = hp
    if gate_violations is not None:
        cats["provenance_honesty"] = 1.0 if len(gate_violations) == 0 else 0.0
    if composition is not None:
        cats["composition_validity"] = float(composition)
    if experimental_value is not None:
        cats["experimental_value"] = float(experimental_value)
    if explanatory is not None:
        cats["explanatory_clarity"] = float(explanatory)
    return cats
