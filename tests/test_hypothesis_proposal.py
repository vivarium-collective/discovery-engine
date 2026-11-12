"""DE-06 — HypothesisProposal: the engine surfaces where a hypothesis is needed and
validates/records an agent-authored one; it never authors the content."""
from __future__ import annotations

import pytest
import yaml

from discovery_engine.phases.hypothesis_proposal import (
    build_hypothesis_proposal,
    hypothesis_triggers,
    promote_to_hypothesis,
    validate_hypothesis_proposal,
)

_PRED = [{"observable": "colonization_rate", "expected": {"op": "<", "value": 0.2},
          "falsifier": "colonization_rate >= 0.2 on the far island"}]


def _good():
    return build_hypothesis_proposal(
        "Colonization falls with isolation, not area.",
        mechanism="Dispersal is distance-limited; area sets extinction, not arrival.",
        predictions=_PRED, assumptions=["rescue effect negligible"],
        alternatives=["area-controlled null"],
        provenance={"trigger": "unexplained_anomaly", "source": "s1#colonization_rate"})


# ---- the contract ---------------------------------------------------------

def test_valid_proposal_round_trips():
    p = _good()
    assert validate_hypothesis_proposal(p) is p
    assert p["id"].startswith("hp:") and p["status"] == "proposed"
    assert p["provenance"]["trigger"] == "unexplained_anomaly"


def test_build_id_is_stable():
    assert _good()["id"] == _good()["id"]


@pytest.mark.parametrize("mutate,msg", [
    (lambda p: p.update(statement=""), "statement"),
    (lambda p: p.update(mechanism=""), "mechanism"),
    (lambda p: p.update(predictions=[]), "prediction"),
    (lambda p: p.update(predictions=[{"observable": "x", "expected": {"op": "<", "value": 1}}]), "falsifier"),
    (lambda p: p.update(predictions=[{"observable": "x", "falsifier": "y", "expected": {}}]), "prediction"),
    (lambda p: p.update(provenance={}), "provenance"),
    (lambda p: p.update(status="verified"), "status"),
])
def test_invalid_proposals_are_rejected(mutate, msg):
    p = _good()
    mutate(p)
    with pytest.raises(ValueError, match=msg):
        validate_hypothesis_proposal(p)


def test_mechanism_is_required_so_a_bare_restatement_is_not_a_hypothesis():
    with pytest.raises(ValueError, match="mechanism"):
        build_hypothesis_proposal("X happens.", mechanism="  ", predictions=_PRED,
                                  provenance={"trigger": "manual"})


# ---- the trigger (engine points at the gap) -------------------------------

def test_triggers_surface_an_unexplained_anomaly():
    inv = {"name": "inv", "studies": ["s1"], "hypotheses": [
        {"id": "h-iso", "study": "s1", "status": "open",
         "predictions": [{"observable": "rate", "expected": {"op": ">=", "value": 0.5}}]},
        {"id": "h-area", "study": "s1", "status": "open",
         "predictions": [{"observable": "rate", "expected": {"op": "<=", "value": 0.1}}]}]}
    studies = [{"name": "s1", "findings": [{"id": "f", "evidence": {"measure": "rate", "observed": 0.3}}]}]
    trig = hypothesis_triggers(inv, studies)
    assert len(trig) == 1
    t = trig[0]
    assert t["kind"] == "unexplained_anomaly" and t["study"] == "s1"
    assert t["observable"] == "rate" and t["observed"] == 0.3 and t["id"].startswith("ht:")


def test_no_trigger_when_a_hypothesis_predicts_the_value():
    inv = {"name": "inv", "studies": ["s1"], "hypotheses": [
        {"id": "h-iso", "study": "s1", "status": "open",
         "predictions": [{"observable": "rate", "expected": {"op": ">=", "value": 0.2}}]},
        {"id": "h-area", "study": "s1", "status": "open",
         "predictions": [{"observable": "rate", "expected": {"op": "<=", "value": 0.1}}]}]}
    studies = [{"name": "s1", "findings": [{"id": "f", "evidence": {"measure": "rate", "observed": 0.3}}]}]
    assert hypothesis_triggers(inv, studies) == []     # 0.3 satisfies the >=0.2 band


# ---- record + accept (agent authors, engine records; accept promotes) ------

def test_record_is_idempotent_and_validates(tmp_path):
    from discovery_engine.discover.actions import record_hypothesis_proposal
    inv_path = tmp_path / "investigation.yaml"
    inv_path.write_text(yaml.safe_dump({"name": "inv", "studies": ["s1"]}), encoding="utf-8")
    rec = record_hypothesis_proposal(inv_path, _good())
    record_hypothesis_proposal(inv_path, _good())          # same id -> no duplicate
    disk = yaml.safe_load(inv_path.read_text())
    assert [p["id"] for p in disk["hypothesis_proposals"]] == [rec["id"]]
    with pytest.raises(ValueError):
        record_hypothesis_proposal(inv_path, {"statement": "no mechanism"})


def test_accept_promotes_into_authored_hypotheses(tmp_path):
    from discovery_engine.discover.actions import (
        record_hypothesis_proposal, set_hypothesis_proposal_status)
    inv_path = tmp_path / "investigation.yaml"
    inv_path.write_text(yaml.safe_dump({"name": "inv", "studies": ["s1"]}), encoding="utf-8")
    hp = record_hypothesis_proposal(inv_path, _good())
    out = set_hypothesis_proposal_status(inv_path, hp["id"], "accepted", rationale="best explanation")
    assert out["promoted"] is True
    disk = yaml.safe_load(inv_path.read_text())
    assert disk["hypothesis_proposals"][0]["status"] == "accepted"
    hyps = disk["hypotheses"]
    assert len(hyps) == 1 and hyps[0]["id"] == hp["id"] and hyps[0]["status"] == "open"
    assert hyps[0]["predictions"][0]["observable"] == "colonization_rate"


def test_decline_does_not_promote(tmp_path):
    from discovery_engine.discover.actions import (
        record_hypothesis_proposal, set_hypothesis_proposal_status)
    inv_path = tmp_path / "investigation.yaml"
    inv_path.write_text(yaml.safe_dump({"name": "inv", "studies": ["s1"]}), encoding="utf-8")
    hp = record_hypothesis_proposal(inv_path, _good())
    out = set_hypothesis_proposal_status(inv_path, hp["id"], "declined")
    assert out["promoted"] is False
    disk = yaml.safe_load(inv_path.read_text())
    assert not disk.get("hypotheses")
    assert disk["hypothesis_proposals"][0]["status"] == "declined"


def test_promote_to_hypothesis_shape():
    h = promote_to_hypothesis(_good())
    assert h["status"] == "open" and h["origin"]["kind"] == "proposed"
    assert h["predictions"][0]["falsifier"]


def _forked_proposal(study, op, value):
    p = build_hypothesis_proposal(
        f"Formate {op} {value} under the in-situ gradient.",
        mechanism="H2-driven CO2 reduction at reduced gradient efficiency.",
        predictions=[{"observable": "settled_formate_mM", "when": {"gradient_efficiency": 0.1},
                      "expected": {"op": op, "value": value},
                      "falsifier": f"settled_formate_mM not {op} {value} at gradient_efficiency=0.1"}],
        provenance={"trigger": "competing_frameworks", "source": f"{study}#settled_formate_mM"})
    p["study"] = study
    return p


def test_promote_preserves_the_discriminating_when_condition():
    """A prediction's experimental condition (`when`) is what the autonomous design pass
    turns into the discriminating arm (experiment_design.design_discriminating_study). If
    promotion drops it, the materialized arms lose their concrete condition — so promotion
    must carry `when` through onto the authored hypothesis."""
    h = promote_to_hypothesis(_forked_proposal("og-01", ">=", 1.0))
    assert h["predictions"][0]["when"] == {"gradient_efficiency": 0.1}


def test_promote_preserves_study_so_the_fork_is_discoverable():
    """A hypothesis must carry its `study`, or discriminating_hypotheses can't scope it and
    the autonomous design pass never sees the fork. Two accepted proposals on the same study
    predicting disjoint bands on one observable must form a discriminable fork."""
    from discovery_engine.phases.hypotheses import discriminating_hypotheses
    classical = promote_to_hypothesis(_forked_proposal("og-01", ">=", 1.0))
    reassess = promote_to_hypothesis(_forked_proposal("og-01", "<=", 0.05))
    assert classical["study"] == "og-01" and reassess["study"] == "og-01"
    disc = discriminating_hypotheses({"hypotheses": [classical, reassess]})
    assert disc.get("og-01", {}).get("discriminable") is True
    assert disc["og-01"]["observable"] == "settled_formate_mM"
