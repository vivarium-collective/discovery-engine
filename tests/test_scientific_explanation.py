"""R4 §5/§8 — evidence-linked ScientificExplanation + evidence-fingerprinted resolutions that
reopen when the premise changes."""
from __future__ import annotations

from discovery_engine.phases.scientific_explanation import (
    ScientificExplanation,
    build_scientific_explanation,
    evidence_fingerprint,
    fingerprinted_resolution,
    resolution_is_stale,
    validate_explanation,
)


def test_build_links_claim_to_evidence_and_alternatives():
    e = build_scientific_explanation(
        {"statement": "coupling drives selection"},
        mechanisms=["coupling"], model_versions=["mv:1"],
        supporting_observations=[{"id": "o1", "verdict": "supported"}],
        unresolved_alternatives=["drift"], scope="low-nutrient regime")
    assert isinstance(e, ScientificExplanation)
    assert e.claim == "coupling drives selection"
    assert e.mechanisms == ["coupling"] and e.unresolved_alternatives == ["drift"]


def test_validate_flags_prose_without_evidence_and_open_alternatives():
    weak = build_scientific_explanation("a claim", mechanisms=["m"])   # no obs, no model version
    issues = validate_explanation(weak)
    assert any("no supporting observation" in i for i in issues)
    assert any("no model version" in i for i in issues)

    contested = build_scientific_explanation(
        "a claim", supporting_observations=[{"id": "o1"}],
        refuting_observations=[{"id": "o2"}], unresolved_alternatives=["alt"])
    issues2 = validate_explanation(contested)
    assert any("refuting observation" in i for i in issues2)
    assert any("unresolved alternative" in i for i in issues2)


def test_well_supported_explanation_has_no_issues():
    e = build_scientific_explanation(
        "a claim", mechanisms=["m"], model_versions=["mv:1"],
        supporting_observations=[{"id": "o1", "verdict": "supported"}])
    assert validate_explanation(e) == []


# --- evidence-fingerprinted resolutions (§8) ---

def test_fingerprint_is_content_addressed_and_order_independent():
    a = evidence_fingerprint(observations=[{"id": "o1", "value": 1.0}, {"id": "o2", "value": 2.0}])
    b = evidence_fingerprint(observations=[{"id": "o2", "value": 2.0}, {"id": "o1", "value": 1.0}])
    assert a == b                                          # order-independent
    c = evidence_fingerprint(observations=[{"id": "o1", "value": 9.9}, {"id": "o2", "value": 2.0}])
    assert c != a                                          # a changed value changes the fingerprint


def test_resolution_reopens_when_the_premise_changes():
    obs = [{"id": "o1", "value": 1.0, "verdict": "supported"}]
    res = fingerprinted_resolution("claim:x", kind="open_challenge", observations=obs,
                                   rationale="settled by o1")
    # unchanged premise -> not stale
    assert not resolution_is_stale(res, observations=obs)
    # a new counter-observation changes the premise -> stale -> should reopen (the (kind,target)
    # key alone would have kept it discharged)
    changed = obs + [{"id": "o2", "value": 0.0, "verdict": "refuted"}]
    assert resolution_is_stale(res, observations=changed)


def test_resolution_without_fingerprint_is_stale():
    assert resolution_is_stale({"target": "x", "kind": "open_challenge"}, observations=[{"id": "o"}])


def test_model_version_change_also_restales_a_resolution():
    res = fingerprinted_resolution("claim:x", kind="open_decision",
                                   observations=[{"id": "o1", "value": 1.0}], model_versions=["mv:1"])
    assert not resolution_is_stale(res, observations=[{"id": "o1", "value": 1.0}], model_versions=["mv:1"])
    assert resolution_is_stale(res, observations=[{"id": "o1", "value": 1.0}], model_versions=["mv:2"])
