"""Tests for discovery_engine.reporting.validation — the validator layer (RFC-0007).

One validation-record shape + a (type, scope)-tagged registry, generalizing the
checks the framework already runs. The two-axis rule (execution_status ⊥
scientific_judgment) is enforced via the type axis: a type that only establishes
execution correctness must leave the scientific judgment null.
"""
from __future__ import annotations

import pytest

from discovery_engine.reporting import validation as V
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


# --- contract/audit consolidation: Finding -> record bridge + contract_face ----

def test_record_from_findings_maps_worst_severity_and_collapses():
    # error present -> failed; codes -> criteria; where+message -> findings strings
    findings = [
        {"severity": "warning", "code": "bad_units", "where": "inputs.x", "message": "cannot parse units"},
        {"severity": "error", "code": "unknown_port", "where": "predicate:c1", "message": "not a declared input port"},
    ]
    r = V.record_from_findings(type="structural", scope="study", target="s1",
                               findings=findings, validator_id="structural.study.contract_face")
    assert r["result"]["execution_status"] == "failed"              # worst severity wins
    assert r["result"]["scientific_judgment"] is None               # structural stays execution-only
    assert r["criteria"] == ["bad_units", "unknown_port"]
    assert "predicate:c1: not a declared input port" in r["findings"]


def test_record_from_findings_empty_is_passed():
    r = V.record_from_findings(type="structural", scope="study", target="s1",
                               findings=[], validator_id="structural.study.contract_face")
    assert r["result"]["execution_status"] == "passed"
    assert r["findings"] == [] and r["criteria"] == []


def test_record_from_findings_warning_only_is_inconclusive():
    r = V.record_from_findings(type="structural", scope="study", target="s1",
                               findings=[{"severity": "warning", "code": "w", "where": "p", "message": "m"}],
                               validator_id="structural.study.contract_face")
    assert r["result"]["execution_status"] == "inconclusive"


def test_contract_face_validator_registered():
    assert "structural.study.contract_face" in [v["id"] for v in V.registry()]


def test_contract_face_grades_clean_broken_and_skips_undeclared():
    clean = {"contract": {"face": {"inputs": {"temp": "float[K]"}, "outputs": {"rate": "float"}}}}
    broken = {"contract": {"face": {"inputs": {"temp": "float[K]"}},
                           "conditions": [{"name": "c1", "kind": "pre", "expr": "inputs.nope > 0"}]}}
    undeclared = {"name": "s-none"}
    ctx = V.ValidationContext(studies={"s-clean": clean, "s-broken": broken, "s-none": undeclared})
    recs = V._study_contract_face(ctx)
    by = {r["target"]: r for r in recs}
    assert set(by) == {"s-clean", "s-broken"}                       # undeclared skipped (not applicable)
    assert by["s-clean"]["result"]["execution_status"] == "passed"
    assert by["s-broken"]["result"]["execution_status"] == "failed"
    assert "unknown_port" in by["s-broken"]["criteria"]
    # structural: never asserts scientific truth
    assert V.epistemic_separation_violations(recs) == []


# --- contract/audit consolidation: evidence-integrity over enriched ctx --------

def test_evidence_integrity_validator_registered():
    assert "empirical.claim.evidence_integrity" in [v["id"] for v in V.registry()]


def test_evidence_integrity_maps_ledger_statuses():
    ledger = {"ledger": [
        {"study": "s1", "finding": "f-ok", "status": "verified", "reason": "matches run", "observed": 0.5},
        {"study": "s1", "finding": "f-prov", "status": "provisional", "reason": "no resolvable run"},
        {"study": "s2", "finding": "f-contra", "status": "contradicted", "reason": "violates pass_if"},
        {"study": "s2", "finding": "f-unsup", "status": "unsupported", "reason": "prose-only"},
    ]}
    recs = V._evidence_integrity(V.ValidationContext(evidence_state=ledger))
    by = {r["target"]: r for r in recs}
    assert by["claim:s1/f-ok"]["result"]["execution_status"] == "passed"
    assert by["claim:s1/f-prov"]["result"]["execution_status"] == "inconclusive"
    assert by["claim:s2/f-contra"]["result"]["execution_status"] == "failed"
    assert by["claim:s2/f-unsup"]["result"]["execution_status"] == "failed"
    # integrity check never asserts scientific truth
    assert all(r["result"]["scientific_judgment"] is None for r in recs)
    assert V.epistemic_separation_violations(recs) == []
    # resolvable findings are standing feedback; failures are actionable (frontier default)
    assert by["claim:s1/f-ok"]["surfaces"] == "standing"
    assert by["claim:s1/f-prov"]["surfaces"] == "standing"
    assert "surfaces" not in by["claim:s2/f-contra"]  # -> frontier via run_validators


def test_evidence_integrity_empty_state_is_noop():
    assert V._evidence_integrity(V.ValidationContext()) == []
    assert V._evidence_integrity(V.ValidationContext(evidence_state={"ledger": []})) == []


# --- contract/audit consolidation: observable coverage over the workspace slice -

def test_observable_coverage_validator_registered():
    assert "structural.study.observable_coverage" in [v["id"] for v in V.registry()]


def test_observable_coverage_grades_gaps_complete_and_abstains():
    studies = {
        "s-complete": {"contract": {"face": {"inputs": {"temp": "float"}, "outputs": {"rate": "float"}}}},
        "s-gap": {"contract": {"face": {"inputs": {"temp": "float"},
                                        "outputs": {"rate": "float", "flux": "float"}}}},
        "s-norun": {"contract": {"face": {"inputs": {"temp": "float"}}}},
        "s-noface": {"name": "x"},
    }
    ws = {"run_observables": {"s-complete": {"temp", "rate"},
                             "s-gap": {"temp", "rate"},  # 'flux' not emitted
                             "s-norun": None}}            # no readable run -> abstain
    recs = V._observable_coverage(V.ValidationContext(studies=studies, workspace=ws))
    by = {r["target"]: r for r in recs}
    assert set(by) == {"s-complete", "s-gap"}                       # norun + noface abstain
    assert by["s-complete"]["result"]["execution_status"] == "passed"
    assert by["s-gap"]["result"]["execution_status"] == "inconclusive"
    assert by["s-gap"]["surfaces"] == "standing"                   # a coverage warning, not a defect
    assert "flux" in " ".join(by["s-gap"]["findings"])
    assert all(r["result"]["scientific_judgment"] is None for r in recs)
    assert V.epistemic_separation_violations(recs) == []


def test_observable_coverage_empty_workspace_is_noop():
    studies = {"s1": {"contract": {"face": {"inputs": {"t": "float"}}}}}
    assert V._observable_coverage(V.ValidationContext(studies=studies)) == []


# --- contract/audit consolidation: wrap the report linter + L0-L5 as validators -

def test_lint_findings_adapter_scopes_and_grades():
    from discovery_engine.reporting.linter.context import LintFinding
    lf = [
        LintFinding(level="error", study_slug="s1", field_path="f", message="m1", override_key="k", check="c_err"),
        LintFinding(level="warning", study_slug="s1", field_path="g", message="m2", override_key="k", check="c_warn"),
        LintFinding(level="info", study_slug="<workspace>", field_path="n", message="m3", override_key="k", check="c_ws"),
    ]
    recs = V.records_from_lint_findings(lf)
    by = {(r["scope"], r["target"]): r for r in recs}
    assert by[("study", "s1")]["result"]["execution_status"] == "failed"       # worst = error
    assert by[("study", "s1")]["criteria"] == ["c_err", "c_warn"]
    assert "surfaces" not in by[("study", "s1")]                               # error -> frontier
    assert by[("investigation", "investigation")]["result"]["execution_status"] == "passed"  # info only
    assert by[("investigation", "investigation")]["surfaces"] == "standing"
    assert all(r["result"]["scientific_judgment"] is None for r in recs)


def test_report_lint_validators_filter_by_scope_and_abstain():
    from discovery_engine.reporting.linter.context import LintFinding
    lf = [LintFinding(level="error", study_slug="s1", field_path="f", message="m", override_key="k", check="c"),
          LintFinding(level="warning", study_slug="<workspace>", field_path="n", message="m", override_key="k", check="c")]
    ctx = V.ValidationContext(workspace={"lint_findings": lf})
    assert [r["scope"] for r in V._report_lint_study(ctx)] == ["study"]
    assert [r["scope"] for r in V._report_lint_investigation(ctx)] == ["investigation"]
    # abstain when the heavy lint pass was not derived
    assert V._report_lint_study(V.ValidationContext()) == []
    assert V._report_lint_investigation(V.ValidationContext()) == []
    assert "structural.study.report_lint" in [v["id"] for v in V.registry()]
    assert "structural.investigation.report_lint" in [v["id"] for v in V.registry()]


def test_l0l5_adapter_groups_by_study_and_abstains():
    from bigraph_schema.contract_audit import Finding
    findings = [Finding(severity="error", code="L2_no_run", where="s1.runs", message="no runs", level="L2"),
                Finding(severity="warning", code="L4_seed", where="s2.robustness", message="single seed", level="L4")]

    class _Rep:
        findings = None
    _Rep.findings = findings
    recs = V.records_from_l0l5(_Rep())
    by = {r["target"]: r for r in recs}
    assert by["s1"]["result"]["execution_status"] == "failed"
    assert by["s2"]["result"]["execution_status"] == "inconclusive"
    assert all(r["result"]["scientific_judgment"] is None for r in recs)
    ctx = V.ValidationContext(workspace={"reproducibility": _Rep()})
    assert len(V._reproducibility(ctx)) == 2
    assert V._reproducibility(V.ValidationContext()) == []                     # abstain
    assert "structural.study.reproducibility" in [v["id"] for v in V.registry()]
