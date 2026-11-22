"""R3 — design_by_invention wires the perturbation search + explicit roles into a design-producing
path: it INVENTS a discriminating regime (not just ranks authored ones) and takes controls from
declared roles, or abstains with a concrete requirement."""
from __future__ import annotations

from discovery_engine.phases.experiment_search import (
    ExperimentSearchSpace,
    design_by_invention,
)

_INV = {"hypotheses": [{"id": "linear", "study": "s"}, {"id": "amplified", "study": "s"}]}


def _predict(h, pert):
    dose = pert.get("dose", 0.0)
    gain = 2.0 if h["id"] == "amplified" else 1.0
    return {"value": gain * dose, "uncertainty": 0.1}


def test_invents_a_design_under_the_divergent_regime():
    space = ExperimentSearchSpace(interventions=[{"parameter": "dose", "values": [0, 5, 10]}])
    arms = [{"name": "treat", "role": "treatment"}, {"name": "ctrl", "role": "negative_control"}]
    out = design_by_invention(_INV, "s", space, predict=_predict, arms=arms)

    d = out["design"]
    assert d is not None and out["abstention"] is None
    assert d["method"] == "perturbation-search"
    assert d["invented_regime"] == {"dose": 10}                 # the max-separation regime
    # controls come from the DECLARED role, not an arm name
    assert [c["role"] for c in d["controls"]] == ["negative_control"]
    assert d["control_issues"] == []                            # both arms declared valid roles
    assert len(d["predictions"]) == 2
    assert d["hypothesis_contrast"] == ["linear", "amplified"]


def test_abstains_with_a_concrete_requirement_when_nothing_separates():
    flat = {"hypotheses": [{"id": "a", "study": "s"}, {"id": "b", "study": "s"}]}
    space = ExperimentSearchSpace(interventions=[{"parameter": "dose", "values": [0, 5, 10]}])
    out = design_by_invention(flat, "s", space,
                              predict=lambda h, p: {"value": 1.0, "uncertainty": 0.1})
    assert out["design"] is None
    assert out["abstention"]["reason"] == "no_informative_experiment"
    assert "need an additional observable" in out["abstention"]["requirement"]


def test_design_flags_arms_that_declare_no_role():
    space = ExperimentSearchSpace(interventions=[{"parameter": "dose", "values": [0, 10]}])
    # a control-sounding NAME with no declared role is not accepted as a control
    arms = [{"name": "treat", "role": "treatment"}, {"name": "no-dose-control"}]
    out = design_by_invention(_INV, "s", space, predict=_predict, arms=arms)
    assert out["design"]["controls"] == []                      # name alone is not a control
    assert any("declares no valid role" in i for i in out["design"]["control_issues"])
