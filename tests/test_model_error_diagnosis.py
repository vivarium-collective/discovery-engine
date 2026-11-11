"""PR 5 commit 2 — model-error diagnosis (FR-07.04).

A refutation is diagnosed as `revise-model` (competing model arms disagree on the
refuting observable — the model is implicated) or `revise-hypothesis` (no model
disagreement — the hypothesis's prediction was refuted). The typed EvidenceUpdate
steers WHICH child seed `suggest_studies` emits.
"""
from __future__ import annotations

from discovery_engine.discover.cycle import suggest_studies
from discovery_engine.phases.model_error import (
    REVISE_HYPOTHESIS, REVISE_MODEL, diagnose_refutation)


def _study_with_refutation(disagreeing: bool = False) -> dict:
    f = {"id": "f1", "status": "contradicts",
         "statement": "X does not hold under flow",
         "next_action": "Investigate whether raising adhesion restores X.",
         "explanation": "the current model lacks the adhesion the result needs",
         "evidence": {"measure": "flux", "observed": 0.1, "from_test": "t"}}
    if disagreeing:
        # competing model arms with an OPEN (unadjudicated) supersession = a live model
        # disagreement (mirrors tests/test_evaluation.py's s3 fixture).
        f["evidence"]["by_topology"] = {"classical": 0.1, "inside_out": 0.9}
        f["evidence"]["superseded"] = {"source": "CPM", "observed": 1.0}
    return {"name": "s1", "claim": "X holds under flow", "findings": [f]}


# ---- the pure diagnosis ---------------------------------------------------

def test_refutation_with_model_disagreement_is_revise_model():
    studies = {"s1": _study_with_refutation(disagreeing=True)}
    upd = diagnose_refutation("s1", studies["s1"]["findings"][0], {"studies": ["s1"]}, studies)
    assert upd["diagnosis"] == REVISE_MODEL and upd["finding"] == "f1" and upd["study"] == "s1"


def test_refutation_without_disagreement_is_revise_hypothesis():
    studies = {"s1": _study_with_refutation()}
    upd = diagnose_refutation("s1", studies["s1"]["findings"][0], {"studies": ["s1"]}, studies)
    assert upd["diagnosis"] == REVISE_HYPOTHESIS


# ---- the steer: the EvidenceUpdate changes the proposed seed --------------

def test_suggest_studies_emits_a_model_revision_seed():
    studies = {"s1": _study_with_refutation(disagreeing=True)}
    sugg = suggest_studies({}, {"studies": ["s1"]}, studies)
    prop = next(s for s in sugg if s.get("finding_id") == "f1")
    assert prop["evidence_update"]["diagnosis"] == REVISE_MODEL
    assert "Revise the model" in prop["title"]
    assert prop["seed"]["model_change"]["diagnosis"] == REVISE_MODEL


def test_suggest_studies_emits_a_hypothesis_revision_seed():
    studies = {"s1": _study_with_refutation()}
    sugg = suggest_studies({}, {"studies": ["s1"]}, studies)
    prop = next(s for s in sugg if s.get("finding_id") == "f1")
    assert prop["evidence_update"]["diagnosis"] == REVISE_HYPOTHESIS
    assert "Revise the hypothesis" in prop["title"]
    assert prop["seed"].get("revise_hypothesis") is True


def test_same_finding_two_regimes_yield_different_seeds():
    """The whole point: the diagnosis changes WHICH seed is emitted."""
    model = suggest_studies({}, {"studies": ["s1"]},
                            {"s1": _study_with_refutation(disagreeing=True)})
    hyp = suggest_studies({}, {"studies": ["s1"]}, {"s1": _study_with_refutation()})
    pm = next(s for s in model if s.get("finding_id") == "f1")
    ph = next(s for s in hyp if s.get("finding_id") == "f1")
    assert pm["title"] != ph["title"]
    assert pm["evidence_update"]["diagnosis"] != ph["evidence_update"]["diagnosis"]
