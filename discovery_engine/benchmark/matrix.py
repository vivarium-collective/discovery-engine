"""Baseline comparison with confidence intervals.

The plan compares the full engine against baselines (deterministic-only, general agent,
no-critique, no-design, no-multiscale) under equal budgets, across repeated agent seeds and
data replicates, and reports mean ± CI — never a single lucky run. This provides the CI
aggregation and the non-overlapping-interval comparison; the full A–E config set is wired as
its skills land (design / multiscale are Wave B). Pure; AI-free.
"""
from __future__ import annotations

import math


def aggregate(values) -> tuple:
    """``(mean, ci_lo, ci_hi)`` at ~95% (mean ± 1.96·SEM) over the non-``None`` values.
    One value -> ``(v, v, v)``; none -> ``(None, None, None)``."""
    xs = [float(v) for v in values if v is not None]
    if not xs:
        return (None, None, None)
    n = len(xs)
    mean = sum(xs) / n
    if n == 1:
        return (mean, mean, mean)
    var = sum((x - mean) ** 2 for x in xs) / (n - 1)
    half = 1.96 * (math.sqrt(var) / math.sqrt(n))
    return (mean, mean - half, mean + half)


def _total(score):
    return getattr(score, "total", score)


def aggregate_matrix(scores_by_config: dict) -> dict:
    """``{config: [RubricScore | float, ...]}`` -> ``{config: (mean, lo, hi)}`` over totals."""
    return {cfg: aggregate([_total(s) for s in scores])
            for cfg, scores in scores_by_config.items()}


def beats_with_ci(variant: tuple, baseline: tuple) -> bool:
    """True iff ``variant``'s CI lower bound exceeds ``baseline``'s CI upper bound — the
    'improves over baseline with confidence' test (non-overlapping intervals, variant higher)."""
    (_vm, vlo, _vhi) = variant
    (_bm, _blo, bhi) = baseline
    return vlo is not None and bhi is not None and vlo > bhi
