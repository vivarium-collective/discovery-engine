import pytest

from discovery_engine.phases.model_acquisition import (
    ACCEPTED, DECLINED, PROPOSED,
    build_model_acquisition_proposal,
    coerce_model_acquisition_proposal,
    validate_model_acquisition_proposal,
)

_TEST = {"behavior_test": "growth_rate_in_band", "pass_if": {"low": 0.4, "high": 0.8}}


def _raw(**kw):
    p = {"mechanism": "dispersal-limited colonization kernel",
         "expected_behavioral_test": dict(_TEST),
         "provenance": {"trigger": "needs_author"}}
    p.update(kw)
    return p


def test_valid_passes():
    assert validate_model_acquisition_proposal(_raw())["mechanism"]


@pytest.mark.parametrize("bad", [
    _raw(mechanism=""), _raw(mechanism="  "),
    _raw(expected_behavioral_test=None),
    _raw(expected_behavioral_test={"behavior_test": "x"}),
    _raw(expected_behavioral_test={"pass_if": {"low": 1}}),
    _raw(provenance={}), _raw(provenance={"trigger": " "}),
    _raw(status="verified"),
])
def test_honesty_gate_rejects(bad):
    with pytest.raises(ValueError):
        validate_model_acquisition_proposal(bad)


def test_non_mapping_rejected():
    with pytest.raises(ValueError):
        validate_model_acquisition_proposal("x")


def test_build_defaults_and_id():
    p = build_model_acquisition_proposal(
        "m", expected_behavioral_test=_TEST, provenance={"trigger": "needs_author"})
    assert p["id"].startswith("ma:") and len(p["id"]) == 15
    assert p["status"] == PROPOSED and p["at"]
    assert p["provenance"]["by"] == "agent"
    assert {"existing_model_ref", "change", "assumptions"} <= set(p)
    assert (build_model_acquisition_proposal(
        "m", expected_behavioral_test=_TEST, provenance={"trigger": "needs_author"})["id"] == p["id"])


def test_coerce_assigns_id_status_at():
    out = coerce_model_acquisition_proposal(_raw())
    assert out["id"].startswith("ma:") and out["status"] == PROPOSED and out["at"]
    assert out["assumptions"] == []


def test_coerce_rejects_malformed():
    with pytest.raises(ValueError):
        coerce_model_acquisition_proposal({"mechanism": "x"})


def test_constants():
    assert (PROPOSED, ACCEPTED, DECLINED) == ("proposed", "accepted", "declined")
