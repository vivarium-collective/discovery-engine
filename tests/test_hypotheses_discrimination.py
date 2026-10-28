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
