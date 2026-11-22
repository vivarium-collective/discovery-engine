"""R4 live-cycle integration: the five closure states are derived from a cycle's closure +
evidence + claim declarations, and land in the persisted cycle_state (build_cycle_state)."""
from __future__ import annotations

from discovery_engine import discover
from discovery_engine.projection.closure_states import (
    CLOSURE_STATES,
    closure_states_from_cycle,
)

_CLOSED = {"closed": True, "unaddressed_criteria": [], "open_gaps": 0, "open_challenges": 0}


# --- the derivation: refining signals from evidence + claim declarations ---

def test_provisional_study_makes_evidence_insufficient():
    evs = {"by_study": {"s1": {"status": "provisional"}, "s2": {"status": "verified"}}}
    r = closure_states_from_cycle(_CLOSED, {}, evidence_state=evs)
    assert not r["states"]["evidence_sufficient"]
    assert r["states"]["workflow_complete"]          # workflow is independent of evidence strength


def test_adequacy_gate_is_opt_in():
    # a hypothesis that declares no `requires` never triggers the adequacy gate
    r = closure_states_from_cycle(_CLOSED, {"hypotheses": [{"id": "h"}]}, studies={}, evidence_state={})
    assert r["states"]["model_adequate"]


def test_claim_with_unmet_required_mechanism_is_inadequate():
    inv = {"hypotheses": [{"id": "h", "statement": "strong", "requires": ["m1", "m2"]}]}
    studies = {"s": {"findings": [{"mechanism": "m1", "evidence": {"status": "verified"}}]}}  # m2 missing
    r = closure_states_from_cycle(_CLOSED, inv, studies=studies, evidence_state={})
    assert not r["states"]["model_adequate"]
    assert not r["states"]["scientifically_resolved"]


def test_required_mechanism_established_via_verified_study_status():
    inv = {"hypotheses": [{"id": "h", "requires": ["m1"]}]}
    studies = {"s": {"findings": [{"mechanism": "m1"}]}}
    evs = {"by_study": {"s": {"status": "verified"}}}
    r = closure_states_from_cycle(_CLOSED, inv, studies=studies, evidence_state=evs)
    assert r["states"]["model_adequate"]


def test_active_discoveries_from_tracked_candidates():
    r = closure_states_from_cycle(_CLOSED, {"finding_candidates": ["fc:1"]})
    assert r["states"]["discovery_active"]


# --- the wire: build_cycle_state carries the states into the persisted cycle_state ---

def test_build_cycle_state_includes_closure_states():
    inv = {"name": "i", "studies": ["s"]}
    studies = {"s": {"name": "s", "investigation": "i"}}
    result = discover.run_cycle(inv, studies)
    state = discover.build_cycle_state(result, inv, studies=studies, evidence_state={"by_study": {}})
    assert "closure_states" in state and set(state["closure_states"]) == set(CLOSURE_STATES)
    assert "closure_reasons" in state
    # the boolean compatibility view is still present + agrees with workflow+scientific resolution
    assert "closed" in state
