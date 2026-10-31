"""Tests for visualization appropriateness (prototype)."""
from __future__ import annotations

from discovery_engine.viz_appropriateness import (
    INVESTIGATION,
    MISPLACED_SCOPE,
    STUDY,
    UNKNOWN,
    UNTIED,
    classify_viz,
    duplicated_cross_arm,
    investigation_viz_appropriateness,
    study_viz_appropriateness,
    viz_scope,
)

# Input-port schemas mirroring the real sbml-decomposition viz classes.
SCHEMAS = {
    "local:TradeoffMap": {"arms": "list[string]", "labels": "list[string]", "points": "list[tree]"},
    "local:ComposabilityScorecard": {"arms": "list[string]", "labels": "list[string]", "dims": "list[string]"},
    "local:RuntimeComparison": {"arms": "list[string]", "labels": "list[string]", "overhead": "tree"},
    "local:SpeciesDynamics": {"time": "list[float]", "species": "tree"},
}


def scope_of(address):
    return SCHEMAS.get(address)


def test_viz_scope_from_ports():
    assert viz_scope(SCHEMAS["local:TradeoffMap"]) == INVESTIGATION
    assert viz_scope(SCHEMAS["local:SpeciesDynamics"]) == STUDY
    assert viz_scope(None) == UNKNOWN  # unresolved class -> no false alarm


def test_cross_arm_viz_on_a_study_is_misplaced():
    entry = {"name": "tradeoff_map", "address": "local:TradeoffMap", "covers": ["speed", "accuracy"]}
    c = classify_viz(entry, scope_of("local:TradeoffMap"))
    assert c["scope"] == INVESTIGATION
    assert [i["kind"] for i in c["issues"]] == [MISPLACED_SCOPE]  # covered, so not untied


def test_study_viz_untied_when_no_covers():
    entry = {"name": "species_dynamics", "address": "local:SpeciesDynamics"}  # no covers
    c = classify_viz(entry, scope_of("local:SpeciesDynamics"))
    assert c["scope"] == STUDY
    assert [i["kind"] for i in c["issues"]] == [UNTIED]


def test_study_viz_ok_when_scoped_and_tied():
    entry = {"name": "species_dynamics", "address": "local:SpeciesDynamics", "covers": ["glycolysis_trajectory"]}
    assert classify_viz(entry, scope_of("local:SpeciesDynamics"))["ok"] is True


def test_unknown_scope_only_flags_untied_not_misplaced():
    entry = {"name": "mystery", "address": "local:NotInCatalog"}
    c = classify_viz(entry, scope_of("local:NotInCatalog"))
    assert c["scope"] == UNKNOWN
    assert [i["kind"] for i in c["issues"]] == [UNTIED]  # never misplaced on an unresolved class


def test_duplicated_cross_arm_detects_the_real_ambiguity():
    # TradeoffMap declared on 3 studies, ComposabilityScorecard on 2 (the sbml-decomposition pattern)
    studies = {
        "sbml-in-tellurium": {"visualizations": [
            {"address": "local:SpeciesDynamics"}, {"address": "local:TradeoffMap"}]},
        "cross-engine-coupling": {"visualizations": [
            {"address": "local:TradeoffMap"}, {"address": "local:ComposabilityScorecard"}]},
        "bigger-models-and-abm-limit": {"visualizations": [
            {"address": "local:TradeoffMap"}, {"address": "local:ComposabilityScorecard"}]},
    }
    dupes = duplicated_cross_arm(studies, scope_of)
    by_addr = {d["address"]: d for d in dupes}
    assert by_addr["local:TradeoffMap"]["count"] == 3
    assert by_addr["local:ComposabilityScorecard"]["count"] == 2
    # a study-scope viz is never reported as a cross-arm duplicate
    assert "local:SpeciesDynamics" not in by_addr


def test_investigation_rollup_any_issue():
    studies = {
        "s1": {"visualizations": [{"address": "local:TradeoffMap"}]},  # misplaced + untied
        "s2": {"visualizations": [{"address": "local:TradeoffMap"}]},  # duplicated
    }
    roll = investigation_viz_appropriateness(studies, scope_of)
    assert roll["any_issue"] is True
    assert roll["duplicated_cross_arm"][0]["count"] == 2
    assert all(not v["ok"] for rows in roll["per_study"].values() for v in rows)


def test_clean_investigation_has_no_issues():
    studies = {
        "s1": {"visualizations": [{"address": "local:SpeciesDynamics", "covers": ["traj"]}]},
    }
    roll = investigation_viz_appropriateness(studies, scope_of)
    assert roll["any_issue"] is False and roll["duplicated_cross_arm"] == []


def test_appropriateness_is_non_blocking():
    from discovery_engine import discovery_engine as de
    for kind in ("misplaced_scope", "untied", "viz_appropriateness", "duplicated_cross_arm"):
        assert kind not in de._CLOSURE_BLOCKING
