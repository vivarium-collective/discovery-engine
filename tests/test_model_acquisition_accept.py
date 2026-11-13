import pytest

from discovery_engine.discover.actions import (
    accept_model_acquisition_proposal, decline_model_acquisition_proposal,
    record_model_acquisition_proposal)
from discovery_engine.investigation.import_ import investigations_dir
from discovery_engine.study.io import load_yaml_mapping, save_yaml_atomic

SLUG = "inv"


def _setup(tmp_path, *mechs):
    inv_dir = investigations_dir(tmp_path) / SLUG
    inv_dir.mkdir(parents=True)
    path = inv_dir / "investigation.yaml"
    save_yaml_atomic(path, {"slug": SLUG})
    ids = []
    for i, m in enumerate(mechs):
        p = record_model_acquisition_proposal(path, {
            "mechanism": m,
            "expected_behavioral_test": {"behavior_test": "t", "pass_if": {"min": 1}},
            "provenance": {"trigger": f"trig{i}"},
        })
        ids.append(p["id"])
    return path, ids


def test_accept_requires_rationale(tmp_path):
    _, (i,) = _setup(tmp_path, "feedback_inhibition")
    with pytest.raises(ValueError):
        accept_model_acquisition_proposal(tmp_path, SLUG, i, rationale="  ")


def test_accept_sets_status_and_feeds_required_mechanisms(tmp_path):
    path, (i,) = _setup(tmp_path, "feedback_inhibition")
    p = accept_model_acquisition_proposal(tmp_path, SLUG, i, rationale="build it")
    assert p["status"] == "accepted"
    assert p["history"][0]["event"] == "accepted"
    spec = load_yaml_mapping(path)
    assert spec["required_mechanisms"].count("feedback_inhibition") == 1
    assert spec["model_acquisition_proposals"][0]["status"] == "accepted"


def test_accept_is_idempotent_on_required_mechanisms(tmp_path):
    path, (a, b) = _setup(tmp_path, "feedback_inhibition", "feedback_inhibition")
    assert a != b
    accept_model_acquisition_proposal(tmp_path, SLUG, a, rationale="r")
    accept_model_acquisition_proposal(tmp_path, SLUG, b, rationale="r")
    assert load_yaml_mapping(path)["required_mechanisms"].count("feedback_inhibition") == 1


def test_accept_prose_mechanism_not_fed(tmp_path):
    prose = "Distance-limited dispersal kernel."
    path, (i,) = _setup(tmp_path, prose)
    p = accept_model_acquisition_proposal(tmp_path, SLUG, i, rationale="r")
    assert p["status"] == "accepted"
    spec = load_yaml_mapping(path)
    assert prose not in (spec.get("required_mechanisms") or [])
    assert any(h.get("event") == "accepted_no_gap" for h in p["history"])


def test_decline_sets_status(tmp_path):
    path, (i,) = _setup(tmp_path, "feedback_inhibition")
    with pytest.raises(ValueError):
        decline_model_acquisition_proposal(tmp_path, SLUG, i, rationale="")
    p = decline_model_acquisition_proposal(tmp_path, SLUG, i, rationale="no")
    assert p["status"] == "declined"
    assert not load_yaml_mapping(path).get("required_mechanisms")


def test_accept_unknown_id_raises(tmp_path):
    _setup(tmp_path, "feedback_inhibition")
    with pytest.raises(ValueError):
        accept_model_acquisition_proposal(tmp_path, SLUG, "ma:nope", rationale="r")


def test_accept_non_proposed_rejected(tmp_path):
    path, (i,) = _setup(tmp_path, "feedback_inhibition")
    accept_model_acquisition_proposal(tmp_path, SLUG, i, rationale="r")
    with pytest.raises(ValueError):
        accept_model_acquisition_proposal(tmp_path, SLUG, i, rationale="r")
    assert load_yaml_mapping(path)["required_mechanisms"].count("feedback_inhibition") == 1
