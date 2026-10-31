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
    is_synthesis_study,
    study_viz_appropriateness,
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


def test_synthesis_study_detection_and_exemption():
    # a study that builds-on >=2 arms is a synthesis node
    synth = {"parent_studies": [{"study": "a", "relation": "builds-on"},
                                {"study": "b", "relation": "builds-on"}]}
    assert is_synthesis_study(synth) is True
    assert is_synthesis_study({"parent_studies": [{"study": "a", "relation": "builds-on"}]}) is False
    # an investigation-scope viz on a synthesis study is NOT misplaced
    c = classify_viz({"address": "local:RuntimeComparison", "covers": ["t"]},
                     scope_of("local:RuntimeComparison"), on_synthesis=True)
    assert c["scope"] == INVESTIGATION and c["ok"] is True


def test_synthesis_hosts_cross_arm_arm_studies_keep_own():
    # the sbml-decomposition fix shape: a synthesis builds-on all arms, hosts the cross-arm viz
    studies = {
        "sbml-in-tellurium": {"visualizations": [{"address": "local:SpeciesDynamics", "covers": ["traj"]}]},
        "decomposition-synthesis": {
            "parent_studies": [{"study": "sbml-in-tellurium", "relation": "builds-on"},
                               {"study": "fully-native-pbg", "relation": "builds-on"}],
            "visualizations": [{"address": "local:RuntimeComparison", "covers": ["t"], "scope": "investigation"},
                               {"address": "local:ComposabilityScorecard", "covers": ["c"]}]},
    }
    roll = investigation_viz_appropriateness(studies, scope_of)
    assert roll["any_issue"] is False and roll["duplicated_cross_arm"] == []


def _syn(*entries, field="pipeline_gate"):
    if field == "pipeline_gate":
        return {"pipeline_gate": {"prerequisites": list(entries)}}
    return {field: list(entries)}


def test_synthesis_via_canonical_pipeline_gate_prerequisites():
    spec = _syn({"study": "a", "relation": "builds-on"}, {"study": "b", "relation": "builds-on"})
    assert is_synthesis_study(spec) is True


def test_synthesis_via_parent_relations():
    spec = _syn({"study": "a", "relation": "builds-on"}, {"study": "b", "relation": "builds-on"},
                field="parent_relations")
    assert is_synthesis_study(spec) is True


def test_synthesis_gathers_across_sources():
    spec = {"pipeline_gate": {"prerequisites": [{"study": "a", "relation": "builds-on"}]},
            "parent_studies": [{"study": "b", "relation": "builds-on"}]}
    assert is_synthesis_study(spec) is True


def test_synthesis_dedupes_by_distinct_study():
    same = {"study": "a", "relation": "builds-on"}
    assert is_synthesis_study(_syn(same, dict(same))) is False
    # same parent declared in two different sources is still ONE parent
    spec = {"pipeline_gate": {"prerequisites": [same]}, "parent_studies": [dict(same)],
            "parent_relations": [dict(same)]}
    assert is_synthesis_study(spec) is False


def test_synthesis_requires_builds_on_relation():
    spec = _syn({"study": "a", "relation": "tests"}, {"study": "b", "relation": "builds-on"})
    assert is_synthesis_study(spec) is False
    assert is_synthesis_study(_syn({"study": "a"}, {"study": "b"})) is False  # bare organizational


def test_synthesis_relation_normalized_and_malformed_safe():
    spec = _syn({"study": "a", "relation": " Builds_On "}, {"study": "b", "relation": "BUILDS-ON"})
    assert is_synthesis_study(spec) is True
    for bad in (None, {"pipeline_gate": "x"}, {"pipeline_gate": {"prerequisites": "a"}},
                {"parent_studies": [["a"], 3, None, "s"]}, {"parent_relations": {"study": "a"}}):
        assert is_synthesis_study(bad) is False


def test_dup_excludes_synthesis_rehost():
    studies = {
        "plain": {"visualizations": [{"address": "local:RuntimeComparison"}]},
        "synth": {**_syn({"study": "a", "relation": "builds-on"}, {"study": "b", "relation": "builds-on"}),
                  "visualizations": [{"address": "local:RuntimeComparison"}]},
    }
    assert duplicated_cross_arm(studies, scope_of) == []


def test_dup_same_study_listing_twice_counts_once():
    studies = {"s1": {"visualizations": [{"address": "local:RuntimeComparison"},
                                         {"address": "local:RuntimeComparison"}]}}
    assert duplicated_cross_arm(studies, scope_of) == []
    studies["s2"] = {"visualizations": [{"address": "local:RuntimeComparison"}]}
    (d,) = duplicated_cross_arm(studies, scope_of)
    assert d["count"] == 2 and d["studies"] == ["s1", "s2"]


def test_dup_name_fallback_only_without_address_and_no_cross_namespace_collision():
    studies = {
        "s1": {"visualizations": [{"name": "Map", "scope": "investigation"}]},
        "s2": {"visualizations": [{"name": " map ", "scope": "investigation"}]},
        "s3": {"visualizations": [{"name": "local:RuntimeComparison", "scope": "investigation"}]},
        "s4": {"visualizations": [{"address": "local:RuntimeComparison"}]},
    }
    got = {d["address"]: d["studies"] for d in duplicated_cross_arm(studies, scope_of)}
    assert got == {"name:map": ["s1", "s2"]}  # name never collides with an address


def test_covers_requires_only_counts_as_tied():
    c = classify_viz({"address": "local:SpeciesDynamics", "requires": ["glucose"]},
                     scope_of("local:SpeciesDynamics"))
    assert c["ok"] is True and c["covers"] == ["glucose"]


def test_covers_inputs_map_values_count_as_ties():
    for entry in ({"address": "local:SpeciesDynamics", "config": {"inputs_map": {"species": "atp_trace"}}},
                  {"address": "local:SpeciesDynamics", "inputs_map": {"species": "atp_trace"}}):
        c = classify_viz(entry, scope_of("local:SpeciesDynamics"))
        assert c["covers"] == ["atp_trace"] and c["ok"] is True


def test_covers_agrees_with_viz_proposals_covered_observables():
    from discovery_engine.viz_proposals import covered_observables
    entry = {"address": "local:SpeciesDynamics", "covers": ["a"], "requires": ["b"],
             "config": {"inputs_map": {"x": "c", "y": 3}}}
    assert set(classify_viz(entry, None)["covers"]) == covered_observables({"visualizations": [entry]}, None)


def test_malformed_covers_never_raises():
    for bad in ([["x"]], "abc", 5, {"k": "v"}, [None, 3, ["y"]], True):
        for key in ("covers", "requires"):
            c = classify_viz({"address": "local:SpeciesDynamics", key: bad}, scope_of("local:SpeciesDynamics"))
            assert isinstance(c["covers"], list)
    c = classify_viz({"address": "x", "config": {"inputs_map": [1, 2]}}, None)
    assert c["covers"] == []
    assert classify_viz(None, None)["covers"] == []
    assert study_viz_appropriateness({"visualizations": "oops"}, scope_of) == []
    assert study_viz_appropriateness({"visualizations": [None, 3, {"covers": [["x"]]}]}, scope_of)


def test_unresolved_scope_is_not_assessed_not_ok():
    c = classify_viz({"address": "local:Nope", "covers": ["t"]}, scope_of("local:Nope"))
    assert c["scope"] == UNKNOWN and c["state"] == "not_assessed"
    assert c["issues"] == [] and c["ok"] is False
    roll = investigation_viz_appropriateness(
        {"s": {"visualizations": [{"address": "local:Nope", "covers": ["t"]}]}}, scope_of)
    assert roll["any_issue"] is False and roll["not_assessed"] == 1


def test_resolved_clean_viz_state_ok():
    c = classify_viz({"address": "local:SpeciesDynamics", "covers": ["t"]}, scope_of("local:SpeciesDynamics"))
    assert c["state"] == "ok" and c["ok"] is True


def test_issue_state_when_issues():
    assert classify_viz({"address": "local:SpeciesDynamics"}, scope_of("local:SpeciesDynamics"))["state"] == "issues"


def test_rollup_row_for_plain_study_carries_misplaced_scope():
    rows = study_viz_appropriateness(
        {"visualizations": [{"address": "local:RuntimeComparison", "covers": ["t"]}]}, scope_of)
    assert [i["kind"] for i in rows[0]["issues"]] == [MISPLACED_SCOPE] and rows[0]["state"] == "issues"
    # ...and the same viz on a canonical synthesis study does not
    synth = {**_syn({"study": "a", "relation": "builds-on"}, {"study": "b", "relation": "builds-on"}),
             "visualizations": [{"address": "local:RuntimeComparison", "covers": ["t"]}]}
    assert study_viz_appropriateness(synth, scope_of)[0]["issues"] == []


def test_scope_normalization_case_and_whitespace():
    assert viz_scope({"x": 1}, declared="  Investigation ") == INVESTIGATION
    assert viz_scope({"x": 1}, declared=" STUDY") == STUDY
    assert viz_scope({" Arms ": 1}) == INVESTIGATION
    assert viz_scope(None, declared=" Investigation\n") == INVESTIGATION


def test_appropriateness_is_non_blocking():
    # fires if any real appropriateness kind is ever added to the closure-blocking set
    from discovery_engine import discovery_engine as de
    from discovery_engine import viz_appropriateness as va
    kinds = {va.MISPLACED_SCOPE, va.UNTIED, va.NOT_ASSESSED,
             "viz_misplaced", "viz_untied", "viz_duplicated", "viz_not_assessed"}
    assert kinds.isdisjoint(de._CLOSURE_BLOCKING)
