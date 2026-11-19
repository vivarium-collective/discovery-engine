"""The weighted, multi-objective scoring rubric."""
from dataclasses import dataclass

from discovery_engine.benchmark.rubric import (
    WEIGHTS,
    categories_from,
    score_rubric,
)


def test_all_categories_present_weighted_mean():
    cats = {k: 1.0 for k in WEIGHTS}
    s = score_rubric(cats)
    assert abs(s.total - 1.0) < 1e-9 and not s.missing


def test_missing_category_is_reweighted_not_zeroed():
    # only mechanism_id (0.25) measured, at 1.0 -> total should be 1.0 (not 0.25)
    s = score_rubric({"mechanism_id": 1.0})
    assert abs(s.total - 1.0) < 1e-9
    assert "heldout_prediction" in s.missing


def test_values_are_clamped():
    s = score_rubric({"mechanism_id": 2.0, "explanatory_clarity": -1.0})
    assert s.categories["mechanism_id"] == 1.0 and s.categories["explanatory_clarity"] == 0.0


def test_categories_from_outputs():
    @dataclass
    class M:
        mechanism_proposed: bool

    @dataclass
    class H:
        mean_abs_residual: float
        within_fraction: float

    cats = categories_from(metrics=M(True), holdout=H(0.0, 1.0), gate_violations=[])
    assert cats["mechanism_id"] == 1.0
    assert cats["heldout_prediction"] == 1.0
    assert cats["provenance_honesty"] == 1.0          # no gate violations


def test_full_beats_baseline_on_total():
    @dataclass
    class M:
        mechanism_proposed: bool

    @dataclass
    class H:
        mean_abs_residual: float
        within_fraction: float | None

    full = score_rubric(categories_from(metrics=M(True), holdout=H(0.0, 1.0), gate_violations=[]))
    base = score_rubric(categories_from(metrics=M(False), holdout=H(1.0, 0.0),
                                        gate_violations=[]))
    assert full.beats(base)


def test_gate_violation_zeros_provenance():
    from discovery_engine.benchmark.gates import GateViolation
    cats = categories_from(gate_violations=[GateViolation("g", "d")])
    assert cats["provenance_honesty"] == 0.0
