"""R4 §4 acceptance — the claim-specific adequacy gate rejects a strong claim when a required
mechanism is missing, names the supported inference, and proposes a revision + test."""
from __future__ import annotations

from discovery_engine.phases.model_adequacy import assess_model_adequacy


def test_rejects_endosymbiosis_claim_when_inheritance_is_missing():
    # §4's acceptance: exchange is shown, heritable transmission is not
    claim = {"statement": "endosymbiosis has evolved",
             "requires": ["metabolic_exchange", "heritable_transmission"]}
    r = assess_model_adequacy(claim, established_mechanisms=["metabolic_exchange"])

    assert r["verdict"] == "inadequate"
    assert r["excluded_inference"] == "endosymbiosis has evolved"
    assert r["missing_mechanisms"] == ["heritable_transmission"]
    assert "metabolic_exchange" in r["supported_inference"]
    assert "heritable_transmission" in r["proposed_revision"]
    assert "heritable_transmission" in r["proposed_test"]


def test_adequate_when_all_required_mechanisms_established():
    claim = {"statement": "coupling drives selection", "requires": ["m1", "m2"]}
    r = assess_model_adequacy(claim, established_mechanisms=["m1", "m2", "m3"])
    assert r["verdict"] == "adequate"
    assert r["excluded_inference"] is None
    assert r["missing_mechanisms"] == []
    assert r["assumptions"] == ["m3"]            # established beyond what the claim required


def test_established_mechanisms_derived_from_supported_observations():
    claim = {"statement": "X", "requires": ["exchange", "inheritance"]}
    obs = [{"mechanism": "exchange", "supported": True},
           {"mechanism": "inheritance", "supported": False}]   # measured but NOT supported
    r = assess_model_adequacy(claim, observations=obs)
    assert r["verdict"] == "inadequate"
    assert r["missing_mechanisms"] == ["inheritance"]
    assert r["established_mechanisms"] == ["exchange"]


def test_supports_verdict_strings_for_observations():
    claim = {"statement": "X", "requires": ["exchange"]}
    r = assess_model_adequacy(claim, observations=[{"mechanism": "exchange", "verdict": "within_tol"}])
    assert r["verdict"] == "adequate"


def test_no_required_mechanisms_is_not_gated():
    r = assess_model_adequacy("a bare observation", required_mechanisms=[])
    assert r["verdict"] == "adequate" and r["excluded_inference"] is None


def test_none_established_names_no_supported_inference():
    claim = {"statement": "big claim", "requires": ["a", "b"]}
    r = assess_model_adequacy(claim, established_mechanisms=[])
    assert r["verdict"] == "inadequate"
    assert r["missing_mechanisms"] == ["a", "b"]
    assert "no required mechanism" in r["supported_inference"]
