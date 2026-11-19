"""Benchmark 1 — hidden metabolic regulator: the held-out model fixture.

A reference (regulated) model saturates with substrate — the hidden metabolite feedback
inhibition — so growth is ``vmax·d/(Km+d)``. The candidate (unregulated) model omits the
regulation and is the low-dose tangent ``(vmax/Km)·d``: it *matches the reference at the
baseline dose* and only diverges as dose rises. Held-out = unseen dose/nutrient combinations
where the difference shows. Deterministic, oracle-visible-only, toy. Pure; AI-free.

The ``oracle`` is the hidden truth (the regulated model). ``regulated`` is the model the full
engine recovers once it has proposed + built the feedback mechanism; ``unregulated`` is the
candidate a deterministic-only run is stuck with. Held-out residual separates them.
"""
from __future__ import annotations

from dataclasses import dataclass, field

GROWTH = "growth.rate"
CONCEALED_MECHANISM = "metabolite_feedback_inhibition"

_VMAX = 2.0
_KM = 1.0


def regulated(cond: dict) -> float:
    """Reference / recovered model: saturating (Michaelis–Menten) response."""
    d = float(cond["dose"])
    nu = float(cond.get("nutrient_scale", 1.0))
    return nu * _VMAX * d / (_KM + d)


def unregulated(cond: dict) -> float:
    """Candidate model: the low-dose tangent ``(vmax/Km)·d`` — matches the reference as
    ``dose → 0`` and diverges at higher dose (the missing saturation/regulation)."""
    d = float(cond["dose"])
    nu = float(cond.get("nutrient_scale", 1.0))
    return nu * (_VMAX / _KM) * d


def oracle(cond: dict) -> float:
    """The hidden ground truth is the regulated model."""
    return regulated(cond)


# The baseline (training) dose is small, so the two models nearly agree there; the held-out
# conditions are larger doses × nutrient shifts the engine never fit on, where they diverge.
TRAINING_CONDITIONS = [{"dose": 0.05, "nutrient_scale": 1.0}]
HOLDOUT_CONDITIONS = [
    {"dose": d, "nutrient_scale": s}
    for d in (0.5, 1.0, 2.0)
    for s in (1.0, 1.5)
]


@dataclass(frozen=True)
class MetabolicTruth:
    observable: str = GROWTH
    concealed_mechanism: str = CONCEALED_MECHANISM
    training_conditions: tuple = field(default_factory=lambda: tuple(TRAINING_CONDITIONS))
    holdout_conditions: tuple = field(default_factory=lambda: tuple(HOLDOUT_CONDITIONS))


def heldout_score(model):
    """Held-out EvidenceRecords + residual summary for ``model`` against the oracle on the
    unseen conditions (via :func:`evaluate_holdout`)."""
    from discovery_engine.benchmark.holdout import evaluate_holdout
    return evaluate_holdout(HOLDOUT_CONDITIONS, predict=model, observe=oracle,
                            observable=GROWTH, tol=0.05,
                            source={"model_version": getattr(model, "__name__", "model")})
