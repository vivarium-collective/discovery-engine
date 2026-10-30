"""Experiment design (DE 2.0 Wave 1 / Cycle N+1 Phase C).

Gate: a discriminating design registers a prediction per competing hypothesis,
carries control arms, and commits a decision rule before any run.
"""
from __future__ import annotations

from discovery_engine import experiment_design as D


def _inv():
    return {"studies": ["s2"], "hypotheses": [
        {"id": "h-spatial", "study": "s2", "status": "open", "predictions": [
            {"observable": "flux", "when": {"structure": "proximate"}, "expected": {"op": ">=", "value": 1e-22}},
            {"observable": "flux", "when": {"structure": "distant"}, "expected": {"op": "<=", "value": 5e-23}}]},
        {"id": "h-well-mixed-null", "study": "s2", "status": "open", "predictions": [
            {"observable": "flux", "when": {"structure": "proximate"}, "expected": {"op": "<=", "value": 5e-23}}]},
    ]}


def test_design_discriminates_and_registers_predictions():
    d = D.design_discriminating_study(_inv(), "s2")
    assert d is not None
    assert set(d["hypothesis_contrast"]) == {"h-spatial", "h-well-mixed-null"}
    assert d["measurements"] == ["flux"]
    assert len(d["predictions"]) == 3          # 2 from h-spatial + 1 from the null
    assert all("intervention" in p and "expected" in p for p in d["predictions"])


def test_null_hypothesis_intervention_is_a_control():
    d = D.design_discriminating_study(_inv(), "s2")
    assert any(c["hypothesis"] == "h-well-mixed-null" for c in d["controls"])


def test_decision_rule_is_precommitted_with_arms():
    d = D.design_discriminating_study(_inv(), "s2")
    dr = d["decision_criterion"]
    assert dr["precommitted"] is True and dr["observable"] == "flux"
    assert len(dr["arms"]) >= 3 and "anomaly" in dr["rule"]


def test_validate_controls_flags_missing_required_control():
    d = D.design_discriminating_study(_inv(), "s2")
    issues = D.validate_controls(d, required_controls=["no-exchange"])
    assert any("no-exchange" in i for i in issues)


def test_no_design_for_non_discriminable_study():
    inv = {"studies": ["s"], "hypotheses": [
        {"id": "only", "study": "s", "status": "open",
         "predictions": [{"observable": "x", "expected": {"op": ">=", "value": 0.5}}]}]}
    assert D.design_discriminating_study(inv, "s") is None


def test_register_prediction_carries_falsifier_and_intervention():
    h = {"id": "h", "predictions": [{"observable": "x", "when": {"r": "a"}, "expected": {"op": ">=", "value": 1}}]}
    preds = D.register_prediction(h)
    assert preds[0]["hypothesis_id"] == "h" and preds[0]["intervention"] == {"r": "a"}
    assert "outside" in preds[0]["falsifier"]
