"""R4 §5 — the boolean closure becomes five explicit states (workflow / evidence / model /
scientific / discovery), with the boolean kept as a compatibility view."""
from __future__ import annotations

from discovery_engine.projection.closure_states import CLOSURE_STATES, closure_states

_SETTLED = {"closed": True, "unaddressed_criteria": [], "open_gaps": 0, "open_challenges": 0}


def test_all_five_states_present():
    r = closure_states(_SETTLED)
    assert set(r["states"]) == set(CLOSURE_STATES)


def test_fully_settled_investigation():
    r = closure_states(_SETTLED)
    assert r["states"]["workflow_complete"] and r["states"]["scientifically_resolved"]
    assert r["states"]["evidence_sufficient"] and r["states"]["model_adequate"]
    assert not r["states"]["discovery_active"]
    assert r["closed"] is True


def test_open_operational_items_block_only_workflow():
    r = closure_states({"closed": False, "unaddressed_criteria": ["c1"], "open_gaps": 1,
                        "open_challenges": 0})
    assert not r["states"]["workflow_complete"]
    assert r["states"]["scientifically_resolved"]        # science can be settled while work remains
    assert r["closed"] is False


def test_provisional_evidence_blocks_evidence_sufficient_and_scientific_resolution():
    # A claim on provisional-only evidence is not scientifically resolved: evidence sufficiency is
    # a precondition for resolution, so the two states cannot disagree (the coupling that stops the
    # autonomous controller from closing over a live frontier). Workflow can still be complete.
    r = closure_states(_SETTLED, provisional_claims=["claim:a"])
    assert not r["states"]["evidence_sufficient"]
    assert not r["states"]["scientifically_resolved"]
    assert r["states"]["workflow_complete"]
    assert "provisional-only" in r["reasons"]["evidence_sufficient"]
    assert "provisional-only" in r["reasons"]["scientifically_resolved"]


def test_inadequate_claim_blocks_model_and_scientific_resolution():
    r = closure_states(_SETTLED, inadequate_claims=["claim:overreach"])
    assert not r["states"]["model_adequate"]
    assert not r["states"]["scientifically_resolved"]    # an overreaching claim is not resolved
    assert "overreach" in r["reasons"]["model_adequate"]


def test_open_challenge_blocks_scientific_resolution():
    r = closure_states({"closed": False, "unaddressed_criteria": [], "open_gaps": 0,
                        "open_challenges": 2})
    assert r["states"]["workflow_complete"]
    assert not r["states"]["scientifically_resolved"]
    assert "2 open challenge" in r["reasons"]["scientifically_resolved"]


def test_discovery_active_flag():
    r = closure_states(_SETTLED, active_discoveries=["fc:1"])
    assert r["states"]["discovery_active"]
    assert r["closed"] is True                           # an active discovery doesn't un-close


def test_compat_closed_passes_through_when_present_else_derived():
    assert closure_states({"closed": False})["closed"] is False
    # no 'closed' key -> derived from workflow + challenges
    derived = closure_states({"unaddressed_criteria": [], "open_gaps": 0, "open_challenges": 0})
    assert derived["closed"] is True
