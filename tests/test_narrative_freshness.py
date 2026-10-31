"""Tests for investigation-level narrative freshness (prototype)."""
from __future__ import annotations

import pytest

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
    stamp_narrative(inv, STUDIES, rationale="re-read", by="eran")
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


def test_renew_moves_decisions_keeps_verdict_and_does_not_stamp_narrative():
    inv = {"executive": {"verdict_status": "planning",
                         "decisions_needed": [{"question": "Q1"}, {"question": "Q2"}]},
           "resolutions": [{"target": "Q1"}]}
    renew_executive(inv, STUDIES, computed_verdict_status="passing")  # no rationale
    ex = inv["executive"]
    assert [d["question"] for d in ex["decisions_needed"]] == ["Q2"]
    assert [d["question"] for d in ex["decisions_resolved"]] == ["Q1"]
    assert ex["verdict_status"] == "planning"
    assert "narrative_basis" not in ex  # C1: persist-time renew never certifies prose


def test_narrative_can_expire_and_persist_renew_does_not_refresh_it():
    inv = {"executive": {}}
    stamp_narrative(inv, STUDIES, rationale="re-read", by="eran")
    changed = {**STUDIES, "recover-oscillator": {**STUDIES["recover-oscillator"], "claim": "REFUTED"}}
    assert classify_narrative(inv, changed)["state"] == EXPIRED
    for _ in range(3):  # persist_cycle runs every cycle
        renew_executive(inv, changed)
    assert classify_narrative(inv, changed)["state"] == EXPIRED  # stays expired
    assert investigation_freshness(inv, changed)["any_expired"] is True
    stamp_narrative(inv, changed, rationale="re-read after change", by="eran")
    assert classify_narrative(inv, changed)["state"] == FRESH


def test_renew_with_none_studies_never_stamps():
    inv = {"executive": {"verdict_status": "x"}}
    renew_executive(inv, None)
    assert "narrative_basis" not in inv["executive"]
    assert classify_narrative(inv, None)["state"] == UNSTAMPED


def test_stamp_refuses_blank_or_whitespace_rationale_and_empty_studies():
    inv = {"executive": {}}
    for bad in ("", "   ", None, "\n\t"):
        with pytest.raises(ValueError):
            stamp_narrative(inv, STUDIES, rationale=bad, by="eran")
    with pytest.raises(ValueError):
        stamp_narrative(inv, None, rationale="ok", by="eran")
    assert "narrative_basis" not in inv["executive"]


def test_renew_whitespace_verdict_rationale_not_accepted():
    inv = {"executive": {"verdict_status": "planning"}}
    renew_executive(inv, STUDIES, computed_verdict_status="passing", accept_verdict_rationale="   ")
    assert inv["executive"]["verdict_status"] == "planning"
    assert "verdict_history" not in inv["executive"]


def test_basis_changes_when_finding_content_changes_at_same_count():
    base = {"s": {"claim": "c", "verdict": "supported", "findings": [{"id": "f1", "status": "supported"}]}}
    swapped = {"s": {"claim": "c", "verdict": "supported", "findings": [{"id": "f1", "status": "refuted"}]}}
    assert len(base["s"]["findings"]) == len(swapped["s"]["findings"])
    assert narrative_basis(base) != narrative_basis(swapped)


def test_basis_is_deterministic():
    assert narrative_basis(STUDIES) == narrative_basis(STUDIES)
    reordered = dict(reversed(list(STUDIES.items())))
    assert narrative_basis(STUDIES) == narrative_basis(reordered)


def test_verdict_comparison_is_normalized():
    inv = {"executive": {"verdict_status": "Passing "}}
    assert classify_verdict(inv, "passing")["state"] == FRESH


def test_verdict_uses_engine_divergence_flag_when_present():
    inv = {"executive": {"verdict_status": "planning",
                         "computed_acceptance": {"diverges_from_authored": False}}}
    assert classify_verdict(inv, "passing")["state"] == FRESH
    inv["executive"]["computed_acceptance"]["diverges_from_authored"] = True
    assert classify_verdict(inv, "passing")["state"] == EXPIRED


def test_unstamped_is_not_expired():
    f = investigation_freshness({"executive": {}}, STUDIES)
    assert f["narrative"]["state"] == UNSTAMPED and f["any_expired"] is False


def test_freshness_is_non_blocking():
    from discovery_engine import discovery_engine as de
    for kind in ("narrative", "verdict", "decisions", "narrative_expired", "expired"):
        assert kind not in de._CLOSURE_BLOCKING


def test_renew_accepts_verdict_with_rationale():
    inv = {"executive": {"verdict_status": "planning"}}
    renew_executive(inv, STUDIES, computed_verdict_status="passing",
                    accept_verdict_rationale="all 5 studies pass")
    ex = inv["executive"]
    assert ex["verdict_status"] == "passing"
    assert ex["verdict_history"][-1]["rationale"] == "all 5 studies pass"
    # and the tab is now fresh
    assert classify_verdict(inv, "passing")["state"] == FRESH
