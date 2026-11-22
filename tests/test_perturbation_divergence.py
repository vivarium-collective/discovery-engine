"""R3 §3 acceptance — the engine finds a NEW perturbation under which competing predictions
diverge (acceptance 1), and returns a reasoned abstention + a concrete data/model requirement when
none is feasible (acceptance 2). Built on the existing EIG scorer + the perturbation generator."""
from __future__ import annotations

from discovery_engine.phases.experiment_search import (
    ExperimentSearchSpace,
    search_discriminating_perturbation,
)

# Two mechanisms that AGREE under default (dose 0) but respond differently to the dose:
#   linear    predicts value = dose
#   amplified predicts value = 2 * dose
_HYPS = [{"id": "linear"}, {"id": "amplified"}]


def _predict(h, pert):
    dose = pert.get("dose", 0.0)
    gain = 2.0 if h["id"] == "amplified" else 1.0
    return {"value": gain * dose, "uncertainty": 0.1}


def test_finds_a_new_perturbation_where_predictions_diverge():
    space = ExperimentSearchSpace(interventions=[{"parameter": "dose", "values": [0, 5, 10]}])
    res = search_discriminating_perturbation(_HYPS, space, predict=_predict, min_score=0.5)
    assert res["outcome"] == "experiment"
    assert res["method"] == "perturbation-search"
    # the invented regime is the high-dose one, where 10 vs 20 separate far beyond uncertainty
    assert res["condition"]["dose"] == 10
    assert res["score"] > 0.5


def test_abstains_with_a_concrete_requirement_when_nothing_separates():
    # both mechanisms predict the same constant everywhere -> no perturbation separates them
    flat = [{"id": "a"}, {"id": "b"}]
    space = ExperimentSearchSpace(interventions=[{"parameter": "dose", "values": [0, 5, 10]}])
    res = search_discriminating_perturbation(
        flat, space, predict=lambda h, p: {"value": 1.0, "uncertainty": 0.1}, min_score=0.5)
    assert res["outcome"] == "stop" and res["reason"] == "no_informative_experiment"
    assert "need an additional observable" in res["requirement"]
    assert "dose" in res["requirement"]


def test_abstains_when_search_space_is_empty():
    res = search_discriminating_perturbation(
        _HYPS, ExperimentSearchSpace(interventions=[]), predict=_predict)
    assert res["outcome"] == "stop"
    assert res["n_candidates"] == 0
    assert "no candidate perturbations" in res["requirement"]


def test_divergence_respects_min_score_threshold():
    # a weak separation (gain 1.0 vs 1.05) stays below a strict threshold -> abstain
    weak = [{"id": "a"}, {"id": "b"}]
    space = ExperimentSearchSpace(interventions=[{"parameter": "dose", "values": [0, 10]}])
    res = search_discriminating_perturbation(
        weak, space,
        predict=lambda h, p: {"value": (1.05 if h["id"] == "b" else 1.0) * p.get("dose", 0),
                              "uncertainty": 5.0},   # large uncertainty swamps the small spread
        min_score=0.5)
    assert res["outcome"] == "stop"
