"""DE-06 — CritiqueReport: the engine surfaces results that outrun their evidence and
validates/records an agent-authored critique; it never authors the content or moves a verdict."""
from __future__ import annotations

import pytest
import yaml

from discovery_engine.phases.critique_report import (
    build_critique_report,
    critique_triggers,
    validate_critique_report,
)


def _good():
    return build_critique_report(
        "mw-equilibrium-reproduction",
        alternative_diagnoses=["the gain is movement-to-resources, not sensing"],
        unsupported_claims=[{"claim": "equilibrium reached", "why": "passed but no verified run"}],
        suggested_discriminations=[{"observable": "colonization_rate", "test": "non-sensing control"}],
        provenance={"trigger": "verdict_divergence", "source": "mw-equilibrium-reproduction"})


# ---- the contract ---------------------------------------------------------

def test_valid_report_round_trips():
    r = _good()
    assert validate_critique_report(r) is r
    assert r["id"].startswith("cr:") and r["status"] == "open"


def test_build_id_is_stable():
    assert _good()["id"] == _good()["id"]


def test_one_section_is_enough():
    r = build_critique_report("s1", unsupported_claims=[{"claim": "x", "why": "y"}],
                              provenance={"trigger": "manual"})
    assert validate_critique_report(r) is r


def test_no_section_rejected():
    with pytest.raises(ValueError, match="section"):
        build_critique_report("s1", provenance={"trigger": "manual"})


def test_raw_report_without_provenance_rejected():
    # build_ fills a default provenance, so the provenance gate is enforced on raw dicts
    # (the record/coerce path), not on build_.
    with pytest.raises(ValueError, match="provenance"):
        validate_critique_report({"target": "s1", "alternative_diagnoses": ["a"]})


def test_empty_target_rejected():
    with pytest.raises(ValueError, match="target"):
        build_critique_report("  ", alternative_diagnoses=["a"], provenance={"trigger": "x"})


def test_bad_status_rejected():
    r = _good()
    r["status"] = "resolved"
    with pytest.raises(ValueError, match="status"):
        validate_critique_report(r)


# ---- the trigger (engine points at the overclaim) -------------------------

def test_trigger_on_verdict_divergence():
    # authored gate_status 'passed' but no tests -> computed verdict not_started -> diverges
    studies = [{"name": "s1", "gate_status": "passed"}]
    trig = critique_triggers(studies)
    assert len(trig) == 1
    assert trig[0]["kind"] == "verdict_divergence" and trig[0]["study"] == "s1"
    assert trig[0]["authored"] == "passed" and trig[0]["id"].startswith("ct:")


def test_no_trigger_when_authored_matches_evidence():
    assert critique_triggers([{"name": "s1"}]) == []           # nothing authored to diverge


# ---- record + address (agent authors, engine records; advisory) -----------

def test_record_is_idempotent_and_validates(tmp_path):
    from discovery_engine.discover.actions import record_critique_report
    inv_path = tmp_path / "investigation.yaml"
    inv_path.write_text(yaml.safe_dump({"name": "inv"}), encoding="utf-8")
    rec = record_critique_report(inv_path, _good())
    record_critique_report(inv_path, _good())                  # same id -> no duplicate
    disk = yaml.safe_load(inv_path.read_text())
    assert [r["id"] for r in disk["critique_reports"]] == [rec["id"]]
    with pytest.raises(ValueError):
        record_critique_report(inv_path, {"target": "s1"})     # no sections


def test_address_marks_status_without_moving_a_verdict(tmp_path):
    from discovery_engine.discover.actions import (
        record_critique_report, set_critique_report_status)
    inv_path = tmp_path / "investigation.yaml"
    inv_path.write_text(yaml.safe_dump({"name": "inv"}), encoding="utf-8")
    cr = record_critique_report(inv_path, _good())
    out = set_critique_report_status(inv_path, cr["id"], "addressed", rationale="added a control")
    assert out["status"] == "addressed"
    disk = yaml.safe_load(inv_path.read_text())
    assert disk["critique_reports"][0]["status"] == "addressed"
    assert "hypotheses" not in disk and "discoveries" not in disk   # advisory: no state moved
