"""Baseline comparison with confidence intervals."""
from discovery_engine.benchmark.matrix import (
    aggregate,
    aggregate_matrix,
    beats_with_ci,
)


def test_aggregate_single_and_multi():
    assert aggregate([0.5]) == (0.5, 0.5, 0.5)
    assert aggregate([]) == (None, None, None)
    m, lo, hi = aggregate([0.4, 0.5, 0.6])
    assert abs(m - 0.5) < 1e-9 and lo < m < hi


def test_beats_with_ci_requires_non_overlap():
    high = (0.9, 0.85, 0.95)
    low = (0.5, 0.45, 0.55)
    assert beats_with_ci(high, low)          # intervals disjoint, high above
    assert not beats_with_ci(low, high)
    # overlapping intervals -> not a confident win
    assert not beats_with_ci((0.6, 0.5, 0.7), (0.55, 0.45, 0.65))


def test_aggregate_matrix_over_totals():
    class S:
        def __init__(self, t): self.total = t
    matrix = aggregate_matrix({
        "full": [S(0.9), S(0.92), S(0.88)],
        "baseline": [S(0.5), S(0.48), S(0.52)],
    })
    assert beats_with_ci(matrix["full"], matrix["baseline"])
