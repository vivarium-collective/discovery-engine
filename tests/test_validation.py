"""Tests for discovery_engine.validation — the validator layer (RFC-0007).

One validation-record shape + a (type, scope)-tagged registry, generalizing the
checks the framework already runs. The two-axis rule (execution_status ⊥
scientific_judgment) is enforced via the type axis: a type that only establishes
execution correctness must leave the scientific judgment null.
"""
from __future__ import annotations

import pytest

from discovery_engine import validation as V


# --- record shape ----------------------------------------------------------

def test_validation_record_builds_the_two_axis_shape():
    r = V.validation_record(
        type="scientific", scope="study", target="study_12",
        execution_status="passed", scientific_judgment="unresolved",
        criteria=["controls_adequacy"], findings=["competing mechanism not excluded"],
        recommendations=["add control simulation"], evidence=["run_34"],
        validator_id="scientific.study.controls")
    assert r["type"] == "scientific" and r["scope"] == "study" and r["target"] == "study_12"
    assert r["result"]["execution_status"] == "passed"
    assert r["result"]["scientific_judgment"] == "unresolved"
    assert r["findings"] == ["competing mechanism not excluded"]
    assert r["provenance"]["validator_id"] == "scientific.study.controls"


def test_validation_record_is_deterministic_no_wallclock():
    # the record is produced in the pure cycle projection — no wall-clock stamp,
    # so the same evidence yields the identical record (Phase-4 staleness needs this).
    kw = dict(type="empirical", scope="claim", target="c", execution_status="passed")
    assert V.validation_record(**kw) == V.validation_record(**kw)
    assert V.validation_record(**kw)["provenance"]["evaluated_at"] is None


def test_validation_record_rejects_off_vocabulary_axes():
    with pytest.raises(ValueError):
        V.validation_record(type="bogus", scope="study", target="t", execution_status="passed")
    with pytest.raises(ValueError):
        V.validation_record(type="scientific", scope="bogus", target="t", execution_status="passed")
    with pytest.raises(ValueError):
        V.validation_record(type="scientific", scope="study", target="t", execution_status="green")
    with pytest.raises(ValueError):
        V.validation_record(type="scientific", scope="study", target="t",
                            execution_status="passed", scientific_judgment="true-ish")


def test_execution_only_types_may_not_assert_a_judgment():
    assert V.establishes_judgment("empirical") is True
    assert V.establishes_judgment("structural") is False
    # a structural validator asserting scientific truth is a construction-time error
    with pytest.raises(ValueError):
        V.validation_record(type="structural", scope="study", target="t",
                            execution_status="passed", scientific_judgment="supported")


# --- registry + dispatch ---------------------------------------------------

def test_registry_dispatch_and_coverage_matrix():
    reg: list[dict] = []

    @V.validator(type="logical", scope="claim", id="t.logical.claim", registry=reg)
    def _v(ctx):
        return [V.validation_record(type="logical", scope="claim", target="c1",
                                    execution_status="failed",
                                    validator_id="t.logical.claim")]

    records = V.run_validators(ctx=None, registry=reg)
    assert len(records) == 1 and records[0]["target"] == "c1"

    matrix = V.coverage_matrix(registry=reg)
    assert matrix["logical"]["claim"] == ["t.logical.claim"]
    assert matrix["visual"]["figure"] == []  # an empty cell is visible


# --- the epistemic-separation invariant ------------------------------------

def test_epistemic_separation_flags_execution_only_judgment():
    good = V.validation_record(type="empirical", scope="claim", target="c",
                               execution_status="passed", scientific_judgment="supported")
    # a record that smuggles a judgment past the builder (constructed raw) is still caught
    smuggled = {"type": "computational", "scope": "study", "target": "s",
                "result": {"execution_status": "passed", "scientific_judgment": "supported"}}
    violations = V.epistemic_separation_violations([good, smuggled])
    assert len(violations) == 1
    assert violations[0]["invariant"] == "epistemic_separation"
    assert violations[0]["target"] == "s"


# --- the two shipped adapters ----------------------------------------------

def test_provenance_adapter_flags_assessed_claim_without_evidence():
    ctx = V.ValidationContext(
        inv_spec={}, studies={},
        argument={"claims": {
            "claim:a": {"verdict": "supported", "evidence_refs": []},
            "claim:b": {"verdict": "supported", "evidence_refs": ["run_1"]},
            "claim:c": {"verdict": "unassessed", "evidence_refs": []},
        }},
        narrative={}, audit=[])
    recs = V.run_validators(ctx=ctx, registry=V.registry())
    prov = [r for r in recs if r["provenance"]["validator_id"] == "logical.claim.provenance"]
    assert [r["target"] for r in prov] == ["claim:a"]  # only the unbacked assessed claim
    assert prov[0]["result"]["execution_status"] == "failed"
    assert prov[0]["result"]["scientific_judgment"] is None  # logical check asserts no truth


def test_run_validators_stamps_surfaces_default_standing_opt_in_frontier():
    reg: list[dict] = []

    @V.validator(type="scientific", scope="study", id="t.standing", registry=reg)
    def _s(ctx):
        return [V.validation_record(type="scientific", scope="study", target="s",
                                    execution_status="failed", validator_id="t.standing")]

    @V.validator(type="logical", scope="claim", id="t.front", surfaces="frontier", registry=reg)
    def _f(ctx):
        return [V.validation_record(type="logical", scope="claim", target="c",
                                    execution_status="failed", validator_id="t.front")]

    by_id = {r["provenance"]["validator_id"]: r for r in V.run_validators(ctx=None, registry=reg)}
    assert by_id["t.standing"]["surfaces"] == "standing"  # default
    assert by_id["t.front"]["surfaces"] == "frontier"


def test_validator_rejects_bad_surface():
    with pytest.raises(ValueError):
        V.validator(type="logical", scope="claim", id="x", surfaces="loud", registry=[])


def test_run_validators_isolates_a_raising_validator():
    # one bad validator must not crash the cycle — it reports itself as errored.
    reg: list[dict] = []

    @V.validator(type="structural", scope="study", id="t.boom", registry=reg)
    def _boom(ctx):
        raise RuntimeError("kaboom")

    @V.validator(type="logical", scope="claim", id="t.ok", registry=reg)
    def _ok(ctx):
        return [V.validation_record(type="logical", scope="claim", target="c",
                                    execution_status="passed", validator_id="t.ok")]

    recs = V.run_validators(ctx=None, registry=reg)
    ids = {r["provenance"]["validator_id"] for r in recs}
    assert {"t.ok", "t.boom"} <= ids  # the good one still ran
    boom = [r for r in recs if r["provenance"]["validator_id"] == "t.boom"][0]
    assert boom["result"]["execution_status"] == "error"
    assert "kaboom" in boom["findings"][0]
    assert boom["type"] == "structural" and boom["scope"] == "study"


def test_rigor_adapter_emits_scientific_study_record_on_a_gap():
    # a bare hypothesis study has rigor GAPs -> a failed scientific/study record.
    ctx = V.ValidationContext(studies={"s": {"name": "s", "claim": "X causes Y",
                                             "behavior_tests": [{"name": "t1"}]}})
    recs = V.run_validators(ctx=ctx, registry=V.registry())
    r = [x for x in recs if x["provenance"]["validator_id"] == "scientific.study.rigor"
         and x["target"] == "s"]
    assert r and r[0]["type"] == "scientific" and r[0]["scope"] == "study"
    assert r[0]["result"]["execution_status"] == "failed"
    assert r[0]["result"]["scientific_judgment"] is None  # rigor measures method, not truth


def test_band_provenance_adapter_emits_empirical_claim_record():
    # a numeric acceptance band with no cited provenance -> a failed empirical/claim record.
    ctx = V.ValidationContext(studies={"s": {"name": "s", "behavior_tests": [
        {"name": "t1", "pass_if": {"low": 0.2, "high": 0.5}}]}})
    recs = V.run_validators(ctx=ctx, registry=V.registry())
    r = [x for x in recs if x["provenance"]["validator_id"] == "empirical.claim.band_provenance"]
    assert r and r[0]["type"] == "empirical" and r[0]["scope"] == "claim"
    assert r[0]["result"]["execution_status"] == "failed"
    assert "t1" in r[0]["findings"][0]


def test_relationship_validator_flags_dangling_edge():
    # an epistemic edge to a study that isn't in the investigation -> failed relationship.
    ctx = V.ValidationContext(
        inv_spec={"studies": ["child"]},
        studies={"child": {"name": "child", "pipeline_gate": {"prerequisites": [
            {"study": "ghost", "relation": "builds-on"}]}}},
        argument={"claims": {}})
    recs = [r for r in V.run_validators(ctx=ctx, registry=V.registry())
            if r["provenance"]["validator_id"] == "logical.relationship.edge_integrity"]
    assert recs and recs[0]["type"] == "logical" and recs[0]["scope"] == "relationship"
    assert recs[0]["result"]["execution_status"] == "failed"
    assert "ghost" in recs[0]["findings"][0]


def test_relationship_validator_quiet_on_a_sound_edge():
    # an edge to a present, supported parent is sound -> no record.
    ctx = V.ValidationContext(
        inv_spec={"studies": ["parent", "child"]},
        studies={"parent": {"name": "parent"},
                 "child": {"name": "child", "pipeline_gate": {"prerequisites": [
                     {"study": "parent", "relation": "builds-on"}]}}},
        argument={"claims": {"claim:parent": {"verdict": "supported"}}})
    recs = [r for r in V.run_validators(ctx=ctx, registry=V.registry())
            if r["provenance"]["validator_id"] == "logical.relationship.edge_integrity"]
    assert recs == []


def test_relationship_validator_flags_building_on_a_refuted_parent():
    ctx = V.ValidationContext(
        inv_spec={"studies": ["parent", "child"]},
        studies={"parent": {"name": "parent"},
                 "child": {"name": "child", "pipeline_gate": {"prerequisites": [
                     {"study": "parent", "relation": "builds-on"}]}}},
        argument={"claims": {"claim:parent": {"verdict": "refuted"}}})
    recs = [r for r in V.run_validators(ctx=ctx, registry=V.registry())
            if r["provenance"]["validator_id"] == "logical.relationship.edge_integrity"]
    assert recs and recs[0]["result"]["execution_status"] == "inconclusive"  # weak foundation, informational


def test_record_signature_is_stable_and_content_sensitive():
    base = dict(type="logical", scope="claim", target="c", execution_status="failed",
                findings=["a"], validator_id="v")
    assert V.record_signature(V.validation_record(**base)) == \
        V.record_signature(V.validation_record(**base))  # same content -> same sig
    changed = {**base, "execution_status": "passed"}
    assert V.record_signature(V.validation_record(**base)) != \
        V.record_signature(V.validation_record(**changed))  # content drift -> diff sig


def test_stale_records_detects_drift_removal_and_ignores_new():
    r = V.validation_record(type="logical", scope="claim", target="c",
                            execution_status="failed", findings=["x"], validator_id="v")
    snap = V.snapshot_of([r])
    assert V.stale_records([r], snap) == []  # unchanged -> not stale

    drifted = V.validation_record(type="logical", scope="claim", target="c",
                                  execution_status="passed", validator_id="v")
    s = V.stale_records([drifted], snap)
    assert len(s) == 1 and s[0]["change"] == "changed" and s[0]["target"] == "c"

    s2 = V.stale_records([], snap)
    assert len(s2) == 1 and s2[0]["change"] == "removed"

    fresh = V.validation_record(type="logical", scope="claim", target="new",
                                execution_status="failed", validator_id="v")
    assert V.stale_records([r, fresh], snap) == []  # a brand-new record is not stale


def test_deprecated_study_field_guard_flags_it():
    ctx = V.ValidationContext(studies={
        "a": {"name": "a", "gate_reason": "legacy", "program": "x"},
        "b": {"name": "b"}})  # b is clean
    recs = [r for r in V.run_validators(ctx=ctx, registry=V.registry())
            if r["provenance"]["validator_id"] == "structural.study.deprecated_fields"]
    assert len(recs) == 1 and recs[0]["target"] == "a"
    assert recs[0]["type"] == "structural" and recs[0]["result"]["scientific_judgment"] is None
    assert "gate_reason" in recs[0]["findings"][0] and "program" in recs[0]["findings"][0]


def test_investigation_hygiene_guard_flags_retired_cache_and_deprecated_field():
    ctx = V.ValidationContext(inv_spec={"studies": ["a"], "knowledge_cycle": {"x": 1},
                                        "plan_acts": ["old"]}, studies={})
    recs = [r for r in V.run_validators(ctx=ctx, registry=V.registry())
            if r["provenance"]["validator_id"] == "structural.investigation.hygiene"]
    assert len(recs) == 1 and recs[0]["scope"] == "investigation"
    joined = " ".join(recs[0]["findings"])
    assert "knowledge_cycle" in joined and "plan_acts" in joined


def test_investigation_hygiene_guard_flags_resolved_decision_still_listed_open():
    ctx = V.ValidationContext(
        inv_spec={"studies": ["a"],
                  "resolutions": [{"kind": "open_decision", "target": "prior A?", "rationale": "x"}],
                  "executive": {"decisions_needed": [{"question": "prior A?"}, {"question": "prior B?"}]}},
        studies={})
    recs = [r for r in V.run_validators(ctx=ctx, registry=V.registry())
            if r["provenance"]["validator_id"] == "structural.investigation.hygiene"]
    assert len(recs) == 1
    assert "already resolved" in " ".join(recs[0]["findings"])


def test_hygiene_guards_are_quiet_on_a_clean_workspace():
    ctx = V.ValidationContext(inv_spec={"studies": ["a"]}, studies={"a": {"name": "a"}})
    ids = {r["provenance"]["validator_id"] for r in V.run_validators(ctx=ctx, registry=V.registry())}
    assert "structural.study.deprecated_fields" not in ids
    assert "structural.investigation.hygiene" not in ids


def test_narrative_adapter_reports_gaps_as_one_record():
    ctx_gap = V.ValidationContext(
        inv_spec={}, studies={}, argument={"claims": {}}, narrative={},
        audit=[{"from": "claim:a", "to": "claim:b", "reason": "unsupported transition",
                "followup": {"title": "Discriminate: b"}}])
    recs = V.run_validators(ctx=ctx_gap, registry=V.registry())
    nar = [r for r in recs if r["provenance"]["validator_id"] == "narrative.narrative.coherence"]
    assert len(nar) == 1
    assert nar[0]["result"]["execution_status"] == "failed"
    assert "unsupported transition" in nar[0]["findings"][0]

    ctx_clean = V.ValidationContext(inv_spec={}, studies={}, argument={"claims": {}},
                                    narrative={}, audit=[])
    recs2 = V.run_validators(ctx=ctx_clean, registry=V.registry())
    nar2 = [r for r in recs2 if r["provenance"]["validator_id"] == "narrative.narrative.coherence"]
    assert nar2[0]["result"]["execution_status"] == "passed"


def test_study_narrative_completeness_flags_missing_claim_and_findings_statement():
    ctx = V.ValidationContext(
        inv_spec={"studies": ["a", "b"]},
        studies={
            "a": {"name": "a"},  # no claim -> flagged
            "b": {"name": "b", "claim": "b holds",
                  "findings": [{"id": "f1"}]},  # has findings but no statement -> flagged
        })
    recs = [r for r in V.run_validators(ctx=ctx, registry=V.registry())
            if r["provenance"]["validator_id"] == "structural.study.narrative_completeness"]
    by_target = {r["target"]: " ".join(r["findings"]) for r in recs}
    assert "claim" in by_target.get("a", "")
    assert "findings[].statement" in by_target.get("b", "")


def test_study_narrative_completeness_quiet_when_prose_present():
    ctx = V.ValidationContext(
        inv_spec={"studies": ["a"]},
        studies={"a": {"name": "a", "claim": "a holds",
                       "findings": [{"id": "f1", "statement": "a does X"}]}})
    ids = {r["provenance"]["validator_id"] for r in V.run_validators(ctx=ctx, registry=V.registry())}
    assert "structural.study.narrative_completeness" not in ids


def test_investigation_narrative_completeness_flags_missing_opening_prose():
    ctx = V.ValidationContext(
        inv_spec={"studies": ["a"], "executive": {"verdict": "holds"}},  # no what_is_this / verdict_detail
        studies={})
    recs = [r for r in V.run_validators(ctx=ctx, registry=V.registry())
            if r["provenance"]["validator_id"] == "structural.investigation.narrative_completeness"]
    assert len(recs) == 1
    joined = " ".join(recs[0]["findings"])
    assert "what_is_this" in joined and "verdict_detail" in joined
