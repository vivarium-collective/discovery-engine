"""discriminating_hypotheses: which studies carry competing hypotheses a single
run would separate (the stage-3 heuristic information-value signal)."""
from __future__ import annotations

from discovery_engine import hypotheses as H


def test_two_opposed_bands_on_one_observable_are_discriminable():
    inv = {"hypotheses": [
        {"id": "hi", "study": "s", "status": "open",
         "predictions": [{"observable": "x", "expected": {"op": ">=", "value": 0.5}}]},
        {"id": "lo", "study": "s", "status": "open",
         "predictions": [{"observable": "x", "expected": {"op": "<=", "value": 0.5}}]},
    ]}
    d = H.discriminating_hypotheses(inv)
    assert d["s"]["discriminable"] is True
    assert d["s"]["observable"] == "x"
    assert {c["hypothesis"] for c in d["s"]["competing"]} == {"hi", "lo"}


def test_overlapping_bands_are_not_discriminable():
    inv = {"hypotheses": [
        {"id": "a", "study": "s", "status": "open",
         "predictions": [{"observable": "x", "expected": {"low": 0.0, "high": 0.8}}]},
        {"id": "b", "study": "s", "status": "open",
         "predictions": [{"observable": "x", "expected": {"low": 0.4, "high": 1.0}}]},
    ]}
    assert H.discriminating_hypotheses(inv)["s"]["discriminable"] is False


def test_retired_hypothesis_is_not_counted():
    inv = {"hypotheses": [
        {"id": "hi", "study": "s", "status": "open",
         "predictions": [{"observable": "x", "expected": {"op": ">=", "value": 0.5}}]},
        {"id": "lo", "study": "s", "status": "excluded",
         "predictions": [{"observable": "x", "expected": {"op": "<=", "value": 0.5}}]},
    ]}
    # only one live hypothesis remains on x -> not discriminable
    assert H.discriminating_hypotheses(inv).get("s", {}).get("discriminable", False) is False


def test_no_hypotheses_block_is_empty():
    assert H.discriminating_hypotheses({}) == {}
    assert H.discriminating_hypotheses({"hypotheses": None}) == {}


def test_far_apart_scalar_bands_discriminate():
    inv = {"hypotheses": [
        {"id": "hi", "study": "s2", "status": "open",
         "predictions": [{"observable": "flux", "expected": {"op": ">=", "value": 1.0e-22}}]},
        {"id": "lo", "study": "s2", "status": "open",
         "predictions": [{"observable": "flux", "expected": {"op": "<=", "value": 5.0e-23}}]},
    ]}
    assert H.discriminating_hypotheses(inv)["s2"]["discriminable"] is True


# --- anomalies: observed outside all competing predictions ---

def _inv_s2():
    return {"hypotheses": [
        {"id": "hi", "study": "s2", "status": "open",
         "predictions": [{"observable": "flux", "expected": {"op": ">=", "value": 1.0e-22}}]},
        {"id": "lo", "study": "s2", "status": "open",
         "predictions": [{"observable": "flux", "expected": {"op": "<=", "value": 5.0e-23}}]},
    ]}


def test_observed_in_the_band_gap_is_an_anomaly():
    # 7e-23 is above the <=5e-23 band and below the >=1e-22 band -> outside both
    study = {"name": "s2", "findings": [{"evidence": {"measure": "flux", "observed": 7.0e-23}}]}
    a = H.anomalies(_inv_s2(), [study])
    assert len(a) == 1
    assert a[0]["study"] == "s2" and a[0]["observable"] == "flux" and a[0]["observed"] == 7.0e-23
    assert "new hypothesis" in a[0]["proposed_question"]


def test_observed_satisfying_a_prediction_is_not_an_anomaly():
    study = {"name": "s2", "findings": [{"evidence": {"measure": "flux", "observed": 2.0e-22}}]}
    assert H.anomalies(_inv_s2(), [study]) == []


def test_no_findings_no_anomaly():
    assert H.anomalies(_inv_s2(), [{"name": "s2", "findings": []}]) == []


def test_single_hypothesis_observable_is_not_checked_for_anomaly():
    inv = {"hypotheses": [{"id": "hi", "study": "s2", "status": "open",
           "predictions": [{"observable": "flux", "expected": {"op": ">=", "value": 1.0e-22}}]}]}
    study = {"name": "s2", "findings": [{"evidence": {"measure": "flux", "observed": 0.0}}]}
    assert H.anomalies(inv, [study]) == []


# --- fidelity_flags: automatic model-fidelity caveats (stage 4/6) ---

def _inv_s3():
    return {"hypotheses": [
        {"id": "reach", "study": "s3", "status": "open",
         "predictions": [{"observable": "p", "expected": {"op": ">=", "value": 0.5}}]},
        {"id": "noreach", "study": "s3", "status": "open",
         "predictions": [{"observable": "p", "expected": {"op": "<=", "value": 0.5}}]},
    ]}


def test_superseded_evidence_is_flagged():
    study = {"name": "s3", "findings": [{"evidence": {
        "measure": "p", "superseded": {"source": "cpm (2D passive)", "observed": 1.0}}}]}
    flags = H.fidelity_flags(_inv_s3(), [study])
    assert any(f["kind"] == "superseded_evidence" for f in flags)


def test_gated_multi_value_evidence_is_flagged():
    study = {"name": "s3", "findings": [{"evidence": {
        "measure": "p", "by_topology": {"classical": 0.27, "inside_out": 0.83}}}]}
    flags = H.fidelity_flags(_inv_s3(), [study])
    assert any(f["kind"] == "gated_evidence" for f in flags)


def test_low_fidelity_support_is_flagged_with_the_hypothesis():
    study = {"name": "s3", "findings": [{"evidence": {
        "measure": "p", "observed": 0.9, "fidelity": "geometric_feasibility"}}]}  # rank 1
    flags = H.fidelity_flags(_inv_s3(), [study], min_support_rank=2)
    low = [f for f in flags if f["kind"] == "low_fidelity_support"]
    assert low and "reach" in low[0]["hypotheses"]


def test_high_fidelity_support_is_not_flagged():
    study = {"name": "s3", "findings": [{"evidence": {
        "measure": "p", "observed": 0.9, "fidelity": "continuum_membrane_energetics"}}]}  # rank 3
    assert [f for f in H.fidelity_flags(_inv_s3(), [study]) if f["kind"] == "low_fidelity_support"] == []


def test_no_evidence_no_flags():
    assert H.fidelity_flags(_inv_s3(), [{"name": "s3", "findings": []}]) == []
