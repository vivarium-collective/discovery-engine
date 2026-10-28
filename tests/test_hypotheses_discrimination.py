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
