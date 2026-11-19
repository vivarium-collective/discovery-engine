"""Held-out evaluation: out-of-sample residuals and the baseline comparison."""
from discovery_engine.benchmark.holdout import HoldoutScore, evaluate_holdout


# unseen conditions the model was not fit on
_CONDITIONS = [{"nutrient": "glucose", "dose": d} for d in (0.1, 0.5, 1.0, 2.0)]


def _oracle(c):          # the hidden truth: a saturating (regulated) response
    return 2.0 * c["dose"] / (1.0 + c["dose"])


def _regulated(c):       # full model: matches the oracle
    return 2.0 * c["dose"] / (1.0 + c["dose"])


def _unregulated(c):     # baseline model: linear, wrong at high dose
    return c["dose"]


def test_holdout_scores_residuals_and_marks_records():
    score = evaluate_holdout(_CONDITIONS, predict=_regulated, observe=_oracle,
                             observable="growth", tol=0.05)
    assert score.n == 4
    assert score.mean_abs_residual < 1e-9          # full model reproduces the oracle
    assert score.within_fraction == 1.0
    assert all(r["holdout"] is True for r in score.records)
    assert all(r["observable"] == "growth" for r in score.records)


def test_full_model_beats_baseline_on_holdout():
    full = evaluate_holdout(_CONDITIONS, predict=_regulated, observe=_oracle)
    base = evaluate_holdout(_CONDITIONS, predict=_unregulated, observe=_oracle)
    assert full.mean_abs_residual < base.mean_abs_residual
    assert full.beats(base) and not base.beats(full)


def test_empty_conditions_is_defined():
    s = evaluate_holdout([], predict=_regulated, observe=_oracle)
    assert s == HoldoutScore(n=0, mean_abs_residual=None, max_abs_residual=None,
                             within_fraction=None, records=[])
