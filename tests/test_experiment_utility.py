"""Experiment utility (EIG active design): rank experiments by discrimination, or abstain."""
from discovery_engine.phases.experiment_utility import (
    best_experiment,
    discrimination_score,
    rank_experiments,
)


def test_discrimination_score_separation_vs_overlap():
    # well-separated relative to uncertainty -> near 1
    assert discrimination_score([{"value": 1.0, "uncertainty": 0.05},
                                 {"value": 4.0, "uncertainty": 0.05}]) > 0.9
    # overlapping (same value within uncertainty) -> near 0
    assert discrimination_score([{"value": 1.0, "uncertainty": 0.5},
                                 {"value": 1.02, "uncertainty": 0.5}]) < 0.1
    # fewer than two numeric predictions -> 0
    assert discrimination_score([{"value": 1.0}]) == 0.0
    assert discrimination_score([]) == 0.0


def test_accepts_band_predictions():
    # {low,high} bands -> midpoint/half-width; disjoint bands discriminate
    s = discrimination_score([{"low": 0.0, "high": 0.2}, {"low": 0.8, "high": 1.0}])
    assert s > 0.5


def test_rank_experiments_sorts_by_discrimination():
    hyps = ["a", "b"]
    def predict(h, c):
        return {"value": (c if h == "a" else 2 * c), "uncertainty": 0.1}
    ranked = rank_experiments(hyps, [0.1, 1.0, 5.0], predict=predict)
    assert [r["condition"] for r in ranked] == [5.0, 1.0, 0.1]   # bigger dose -> more divergence
    assert all("predictions" in r for r in ranked)


def test_best_experiment_abstains_when_nothing_discriminates():
    hyps = ["a", "b"]
    def predict(h, c):
        return {"value": 1.0, "uncertainty": 0.3}                # identical predictions
    out = best_experiment(hyps, [{"x": 1}, {"x": 2}], predict=predict)
    assert out["outcome"] == "stop" and out["reason"] == "no_informative_experiment"


def test_eig_finds_perturbation_where_disjoint_band_design_would_abstain():
    """The Wave-C gate: two hypotheses whose predictions OVERLAP at the baseline dose
    (so the old disjoint-band design returns None) but DIVERGE under a dose perturbation —
    EIG picks the discriminating perturbation."""
    from discovery_engine.benchmark.scenarios.metabolic_regulator import regulated, unregulated
    models = {"regulated": regulated, "unregulated": unregulated}
    hyps = list(models)
    def predict(h, cond):
        return {"value": models[h](cond), "uncertainty": 0.1}

    baseline = [{"dose": 0.05, "nutrient_scale": 1.0}]
    # at the baseline dose alone the two models nearly agree -> NO-EXPERIMENT
    assert best_experiment(hyps, baseline, predict=predict)["outcome"] == "stop"

    # given the perturbation space, EIG selects the most discriminating dose
    conditions = [{"dose": d, "nutrient_scale": 1.0} for d in (0.05, 0.5, 2.0)]
    best = best_experiment(hyps, conditions, predict=predict)
    assert best["outcome"] == "experiment"
    assert best["condition"]["dose"] == 2.0 and best["score"] > 0.5
