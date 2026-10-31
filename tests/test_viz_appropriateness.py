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
    viz_scope,
)

# Input-port schemas from the REAL sbml-decomposition viz classes (via inputs()).
SCHEMAS = {
    "local:ComposabilityScorecard": {"arms": "list[string]", "labels": "list[string]", "dims": "list[string]"},
    "local:RuntimeComparison": {"arms": "list[string]", "labels": "list[string]", "overhead_fraction": "tree"},
    "local:AccuracyDivergence": {"arms": "list[string]", "labels": "list[string]", "max_rel_err": "tree"},
    "local:ConvergenceCurves": {"curves": "tree", "labels": "list[string]"},  # per-study multi-curve
    "local:TradeoffMap": {"points": "list[tree]", "off_axis": "list[string]"},  # cross-arm, PRE-AGGREGATED
    "local:DecisionTable": {"rows": "list[tree]"},  # cross-arm, PRE-AGGREGATED
    "local:SpeciesDynamics": {"time": "list[float]", "species": "tree"},
}


def scope_of(address):
    return SCHEMAS.get(address)


def test_arms_port_is_investigation():
    for addr in ("local:ComposabilityScorecard", "local:RuntimeComparison", "local:AccuracyDivergence"):
        assert viz_scope(SCHEMAS[addr]) == INVESTIGATION


def test_labels_alone_is_not_a_cross_arm_signal():
    # ConvergenceCurves labels its intervals, not arms — must NOT be flagged investigation
    assert viz_scope(SCHEMAS["local:ConvergenceCurves"]) == STUDY


def test_preaggregated_cross_arm_needs_explicit_scope():
    # ports alone can't tell: TradeoffMap(points)/DecisionTable(rows) look study-scope
    assert viz_scope(SCHEMAS["local:TradeoffMap"]) == STUDY
    # the escape hatch: an explicit scope: on the entry wins
    assert viz_scope(SCHEMAS["local:TradeoffMap"], declared="investigation") == INVESTIGATION


def test_unresolved_class_is_unknown():
    assert viz_scope(None) == UNKNOWN


def test_cross_arm_viz_on_a_study_is_misplaced():
    entry = {"name": "runtime", "address": "local:RuntimeComparison", "covers": ["wall_clock_s"]}
    c = classify_viz(entry, scope_of("local:RuntimeComparison"))
    assert c["scope"] == INVESTIGATION
    assert [i["kind"] for i in c["issues"]] == [MISPLACED_SCOPE]


def test_explicit_scope_flags_preaggregated_cross_arm():
    entry = {"name": "tradeoff", "address": "local:TradeoffMap", "scope": "investigation", "covers": ["speed"]}
    c = classify_viz(entry, scope_of("local:TradeoffMap"))
    assert c["scope"] == INVESTIGATION and [i["kind"] for i in c["issues"]] == [MISPLACED_SCOPE]


def test_study_viz_untied_when_no_covers():
    c = classify_viz({"name": "sp", "address": "local:SpeciesDynamics"}, scope_of("local:SpeciesDynamics"))
    assert c["scope"] == STUDY and [i["kind"] for i in c["issues"]] == [UNTIED]


def test_study_viz_ok_when_scoped_and_tied():
    entry = {"name": "sp", "address": "local:SpeciesDynamics", "covers": ["glycolysis_trajectory"]}
    assert classify_viz(entry, scope_of("local:SpeciesDynamics"))["ok"] is True


def test_duplicated_cross_arm_detects_the_real_ambiguity():
    studies = {
        "sbml-in-tellurium": {"visualizations": [
            {"address": "local:SpeciesDynamics"}, {"address": "local:RuntimeComparison"}]},
        "fully-native-pbg": {"visualizations": [
            {"address": "local:RuntimeComparison"}, {"address": "local:ComposabilityScorecard"}]},
        "cross-engine-coupling": {"visualizations": [{"address": "local:ComposabilityScorecard"}]},
    }
    by_addr = {d["address"]: d for d in duplicated_cross_arm(studies, scope_of)}
    assert by_addr["local:RuntimeComparison"]["count"] == 2
    assert by_addr["local:ComposabilityScorecard"]["count"] == 2
    assert "local:SpeciesDynamics" not in by_addr  # study-scope never a cross-arm dup


def test_investigation_rollup_any_issue_and_clean():
    dirty = {"s1": {"visualizations": [{"address": "local:RuntimeComparison"}]},
             "s2": {"visualizations": [{"address": "local:RuntimeComparison"}]}}
    roll = investigation_viz_appropriateness(dirty, scope_of)
    assert roll["any_issue"] is True and roll["duplicated_cross_arm"][0]["count"] == 2

    clean = {"s1": {"visualizations": [{"address": "local:SpeciesDynamics", "covers": ["traj"]}]}}
    roll2 = investigation_viz_appropriateness(clean, scope_of)
    assert roll2["any_issue"] is False and roll2["duplicated_cross_arm"] == []


def test_appropriateness_is_non_blocking():
    from discovery_engine import discovery_engine as de
    for kind in ("misplaced_scope", "untied", "viz_appropriateness", "duplicated_cross_arm"):
        assert kind not in de._CLOSURE_BLOCKING
