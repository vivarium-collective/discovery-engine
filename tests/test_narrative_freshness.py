"""Tests for investigation-level narrative freshness (prototype)."""
from __future__ import annotations

from discovery_engine.narrative_freshness import (
    EXPIRED,
    FRESH,
    UNSTAMPED,
    classify_decisions,
    classify_narrative,
    classify_verdict,
    investigation_freshness,
    narrative_basis,
    renew_executive,
    stamp_narrative,
)

STUDIES = {
    "recover-mass-action": {"claim": "recovered", "verdict": "supported", "findings": [{"id": "f1"}]},
    "recover-oscillator": {"claim": "recovered via gradient matching", "verdict": "supported", "findings": []},
}


def test_verdict_fresh_when_authored_matches_computed():
    inv = {"executive": {"verdict_status": "complete"}}
    assert classify_verdict(inv, "complete")["state"] == FRESH


def test_verdict_expired_on_divergence():
    # the discovery-sbml-recovery symptom: authored 'planning' vs computed 'passing'
    inv = {"executive": {"verdict_status": "planning"}}
    c = classify_verdict(inv, "passing")
    assert c["state"] == EXPIRED and c["authored"] == "planning" and c["computed"] == "passing"


def test_decisions_expired_when_resolved_but_listed():
    inv = {"executive": {"decisions_needed": [{"question": "Q1"}, {"question": "Q2"}]},
           "resolutions": [{"target": "Q1"}]}
    c = classify_decisions(inv)
    assert c["state"] == EXPIRED and c["resolved_pending"] == ["Q1"]


def test_decisions_fresh_when_none_resolved():
    inv = {"executive": {"decisions_needed": [{"question": "Q1"}]}, "resolutions": []}
    assert classify_decisions(inv)["state"] == FRESH


def test_narrative_unstamped_then_fresh_then_expired():
    inv = {"executive": {}}
    assert classify_narrative(inv, STUDIES)["state"] == UNSTAMPED
    stamp_narrative(inv, STUDIES)
    assert classify_narrative(inv, STUDIES)["state"] == FRESH
    # a study claim changes -> narrative expires
    changed = {**STUDIES, "recover-oscillator": {**STUDIES["recover-oscillator"], "claim": "REFUTED"}}
    assert classify_narrative(inv, changed)["state"] == EXPIRED


def test_investigation_freshness_any_expired():
    inv = {"executive": {"verdict_status": "planning"}}
    f = investigation_freshness(inv, STUDIES, computed_verdict_status="passing")
    assert f["verdict"]["state"] == EXPIRED
    assert f["narrative"]["state"] == UNSTAMPED
    assert f["any_expired"] is True


def test_renew_moves_decisions_and_stamps_but_keeps_verdict_without_rationale():
    inv = {"executive": {"verdict_status": "planning",
                         "decisions_needed": [{"question": "Q1"}, {"question": "Q2"}]},
           "resolutions": [{"target": "Q1"}]}
    renew_executive(inv, STUDIES, computed_verdict_status="passing")  # no rationale
    ex = inv["executive"]
    assert [d["question"] for d in ex["decisions_needed"]] == ["Q2"]
    assert [d["question"] for d in ex["decisions_resolved"]] == ["Q1"]
    assert ex["verdict_status"] == "planning"  # authored verdict NOT overwritten without a rationale
    assert ex["narrative_basis"] == narrative_basis(STUDIES)  # narrative stamped


def test_renew_accepts_verdict_with_rationale():
    inv = {"executive": {"verdict_status": "planning"}}
    renew_executive(inv, STUDIES, computed_verdict_status="passing",
                    accept_verdict_rationale="all 5 studies pass")
    ex = inv["executive"]
    assert ex["verdict_status"] == "passing"
    assert ex["verdict_history"][-1]["rationale"] == "all 5 studies pass"
    # and the tab is now fresh
    assert classify_verdict(inv, "passing")["state"] == FRESH
