"""Held-out (out-of-sample) evaluation — the plan's third quality gate made mechanical.

A model improvement counts only after comparison on conditions the engine did not fit on.
``evaluate_holdout`` runs a model's prediction against an oracle's observation on a set of
held-out conditions and returns the quantitative residual summary (mean/max |residual|, the
within-uncertainty fraction) plus the per-condition EvidenceRecords (``holdout=True``).

Model-agnostic: it takes ``predict(condition) -> value`` and ``observe(condition) -> value``
callables, so it is testable now with toy models and is wired later (C1) to the Benchmark
manifest's ``holdout`` conditions, the engine's chosen model, and the scenario oracle. Pure
over its callables; AI-free.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from discovery_engine.study.evaluator.evidence_record import build_evidence_record


@dataclass(frozen=True)
class HoldoutScore:
    n: int
    mean_abs_residual: float | None
    max_abs_residual: float | None
    within_fraction: float | None
    records: list = field(default_factory=list)

    def beats(self, other: "HoldoutScore") -> bool:
        """True iff this score has a strictly lower mean |residual| than ``other``
        (both must have a defined mean)."""
        a, b = self.mean_abs_residual, other.mean_abs_residual
        return a is not None and b is not None and a < b


def evaluate_holdout(conditions, *, predict, observe, observable: str = "observable",
                     tol: float | None = None, source: dict | None = None) -> HoldoutScore:
    """Score predicted-vs-observed over held-out ``conditions``.

    ``predict(condition)`` is the engine's chosen model; ``observe(condition)`` is the oracle
    on the *unseen* condition. ``tol`` (optional) is the predicted uncertainty used for the
    within-uncertainty fraction. Returns a :class:`HoldoutScore` with one
    ``holdout=True`` EvidenceRecord per condition."""
    records: list = []
    for cond in conditions:
        pred = {"value": predict(cond)}
        if tol is not None:
            pred["uncertainty"] = tol
        records.append(build_evidence_record(
            observable, predicted=pred, observed={"value": observe(cond)},
            conditions=cond, holdout=True, source=source))
    resid = [abs(r["residual"]) for r in records if r["residual"] is not None]
    within = [r["within_uncertainty"] for r in records if r["within_uncertainty"] is not None]
    return HoldoutScore(
        n=len(records),
        mean_abs_residual=(sum(resid) / len(resid)) if resid else None,
        max_abs_residual=max(resid) if resid else None,
        within_fraction=(sum(1 for w in within if w) / len(within)) if within else None,
        records=records)
