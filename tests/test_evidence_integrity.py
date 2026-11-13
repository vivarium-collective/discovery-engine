"""Evidence/run reconciliation (DE 2.0 Milestone 1 / Cycle N+1 Phase A).

Milestone 1 gate: every numeric finding has a resolvable run/provenance chain OR a
visible PROVISIONAL status; a contradiction (observed violates the gate it cites) is
not silently accepted; a prose-only finding is UNSUPPORTED.
"""
from __future__ import annotations

from discovery_engine.phases import evidence_integrity as E
def _finding(fid, observed=None, from_test=None, run_ids=None):
    ev = {}
    if observed is not None:
        ev["observed"] = observed
    if from_test:
        ev["from_test"] = from_test
    prov = {"run_ids": run_ids} if run_ids else {}
    return {"id": fid, "evidence": ev, "provenance": prov}


def _study(slug, findings, tests=None, runs=None):
    return {"name": slug, "findings": findings, "behavior_tests": tests or [],
            "runs": runs or []}


def _row(measured, test="gate", evaluated_by="code", status="complete"):
    return {"status": status, "variant": "base",
            "computed_outcomes": {test: {"measured_value": measured,
                                         "evaluated_by": evaluated_by}}}


def test_resolvable_run_chain_is_verified():
    f = _finding("f1", observed=0.9, from_test="gate", run_ids=["r1"])
    r = E.classify_finding_evidence(f, _study("s", [f]), {"r1"}, {"r1": _row(0.9)})
    assert r["status"] == E.VERIFIED


def test_numeric_result_without_registered_run_is_provisional():
    f = _finding("f2", observed=0.9, from_test="gate")  # no run_ids
    r = E.classify_finding_evidence(f, _study("s", [f]), registered_run_ids=set(), run_rows={})
    assert r["status"] == E.PROVISIONAL
    assert "no resolvable registered run" in r["reason"]


def test_run_ids_that_do_not_resolve_are_provisional():
    f = _finding("f3", observed=0.9, from_test="gate", run_ids=["ghost"])
    r = E.classify_finding_evidence(f, _study("s", [f]), registered_run_ids={"r1"}, run_rows={})
    assert r["status"] == E.PROVISIONAL


def test_prose_only_finding_is_unsupported():
    f = {"id": "f4", "statement": "we believe it works", "evidence": {}}
    r = E.classify_finding_evidence(f, _study("s", [f]), registered_run_ids=set(), run_rows={})
    assert r["status"] == E.UNSUPPORTED


def test_observed_violating_its_pass_if_is_contradicted():
    tests = [{"name": "gate", "pass_if": {"op": ">", "value": 0.5}}]
    f = _finding("f5", observed=0.2, from_test="gate", run_ids=["r1"])
    r = E.classify_finding_evidence(f, _study("s", [f], tests), registered_run_ids={"r1"}, run_rows={})
    assert r["status"] == E.CONTRADICTED


def _finding_src(fid, observed, source_type, from_test=None, run_ids=None, uncertainty=None):
    f = _finding(fid, observed=observed, from_test=from_test, run_ids=run_ids)
    f["evidence"]["source_type"] = source_type
    if uncertainty is not None:
        f["evidence"]["uncertainty"] = uncertainty
    return f


def test_default_source_type_is_simulation_and_unchanged():
    # No source_type -> treated as simulation; the existing VERIFIED rule still applies,
    # and the record carries source_type=simulation.
    f = _finding("f1", observed=0.9, from_test="gate", run_ids=["r1"])
    r = E.classify_finding_evidence(f, _study("s", [f]), {"r1"}, {"r1": _row(0.9)})
    assert r["status"] == E.VERIFIED
    assert r["source_type"] == E.SIMULATION


def test_non_simulation_source_is_not_verified_by_the_sim_rule():
    # Honesty guard: even if a non-simulation finding cites a resolvable registered run,
    # the simulation VERIFIED rule must NOT apply — it is PROVISIONAL pending a source validator.
    f = _finding_src("f2", 0.9, "literature", from_test="gate", run_ids=["r1"])
    r = E.classify_finding_evidence(f, _study("s", [f]), {"r1"}, {"r1": _row(0.9)})
    assert r["status"] == E.PROVISIONAL
    assert r["source_type"] == "literature"
    assert "non-simulation" in r["reason"] and "literature" in r["reason"]


def test_lab_and_external_sources_are_provisional_with_source_reason():
    for st in ("lab", "external_dataset"):
        f = _finding_src(f"f-{st}", 0.7, st)
        r = E.classify_finding_evidence(f, _study("s", [f]), set(), {})
        assert r["status"] == E.PROVISIONAL and r["source_type"] == st
        assert "non-simulation" in r["reason"]


def test_source_type_and_uncertainty_carried_into_record():
    f = _finding_src("f3", 0.5, "lab", uncertainty={"sd": 0.05})
    r = E.classify_finding_evidence(f, _study("s", [f]), set(), {})
    assert r["source_type"] == "lab" and r["uncertainty"] == {"sd": 0.05}


def test_non_sim_prose_only_still_unsupported():
    f = {"id": "f4", "evidence": {"source_type": "literature"}}
    r = E.classify_finding_evidence(f, _study("s", [f]), set(), {})
    assert r["status"] == E.UNSUPPORTED


def test_blank_or_missing_or_mixedcase_source_type_defaults_to_simulation():
    for st in (None, "", "   ", "Simulation"):
        f = _finding("fs", observed=0.9, from_test="gate", run_ids=["r1"])
        if st is not None:
            f["evidence"]["source_type"] = st
        r = E.classify_finding_evidence(f, _study("s", [f]), {"r1"}, {"r1": _row(0.9)})
        assert r["status"] == E.VERIFIED and r["source_type"] == E.SIMULATION, st


def test_falsy_nonstring_source_type_fails_closed_not_verified():
    # A YAML typo like `source_type: false` must NOT fall back to simulation and verify.
    f = _finding_src("ff", 0.9, False, from_test="gate", run_ids=["r1"])
    r = E.classify_finding_evidence(f, _study("s", [f]), {"r1"}, {"r1": _row(0.9)})
    assert r["status"] == E.PROVISIONAL and r["source_type"] == "false"


def test_unknown_source_type_is_provisional_with_unknown_reason():
    f = _finding_src("fu", 0.5, "labb")   # typo
    r = E.classify_finding_evidence(f, _study("s", [f]), set(), {})
    assert r["status"] == E.PROVISIONAL and "unknown source_type" in r["reason"]


def test_reconcile_rollup_with_non_simulation_finding_is_provisional():
    inv = {"studies": ["s1"]}
    f_sim = _finding("a", 0.9, "g", ["r1"])
    f_lit = _finding_src("b", 0.9, "literature")
    studies = {"s1": _study("s1", [f_sim, f_lit], runs=[{"name": "r1", **_row(0.9, "g")}])}
    out = E.reconcile_evidence_state(inv, studies, {"s1": [{"run_id": "r1"}]})
    assert out["summary"][E.VERIFIED] == 1 and out["summary"][E.PROVISIONAL] == 1
    assert out["by_study"]["s1"]["status"] == E.PROVISIONAL  # the literature finding is the weakest


def test_non_sim_observed_violating_pass_if_is_contradicted():
    # A contradiction is source-agnostic: a cited value that violates a stated band argues
    # against the claim regardless of where it came from.
    tests = [{"name": "gate", "pass_if": {"op": ">", "value": 0.5}}]
    f = _finding_src("f5", 0.2, "literature", from_test="gate")
    r = E.classify_finding_evidence(f, _study("s", [f], tests), set(), {})
    assert r["status"] == E.CONTRADICTED


def test_reconcile_rolls_up_study_to_its_weakest_numeric_finding():
    inv = {"studies": ["s1", "s2"]}
    studies = {
        "s1": _study("s1", [_finding("a", 0.9, "g", ["r1"]), _finding("b", 0.1, "g")],  # verified + provisional
                     runs=[{"name": "r1", **_row(0.9, "g")}]),
        "s2": _study("s2", [_finding("c", 0.7, "g", ["r2"])],                            # verified
                     runs=[{"name": "r2", **_row(0.7, "g")}]),
    }
    runs = {"s1": [{"run_id": "r1"}], "s2": [{"run_id": "r2"}]}
    out = E.reconcile_evidence_state(inv, studies, runs)
    assert out["summary"][E.VERIFIED] == 2 and out["summary"][E.PROVISIONAL] == 1
    assert out["by_study"]["s1"]["status"] == E.PROVISIONAL  # weakest wins
    assert out["by_study"]["s2"]["status"] == E.VERIFIED


def test_trace_claim_to_runs_names_missing_links():
    studies = {"s": _study("s", [_finding("f", 0.9, from_test="g")])}  # no run_ids
    t = E.trace_claim_to_runs("f", studies, {"s": []})
    assert t["found"] and t["resolvable"] is False
    assert "provenance.run_ids" in t["missing"]


def test_conditioned_control_finding_is_not_auto_contradicted():
    # a regime-scoped finding (evidence.conditions) that fails the positive gate is a
    # control / alternate-regime result, judged by its hypothesis prediction — not contradicted.
    tests = [{"name": "gate", "pass_if": {"op": ">", "value": 0.5}}]
    f = {"id": "ctrl", "provenance": {},
         "evidence": {"observed": 0.2, "from_test": "gate", "conditions": {"regime": "control"}}}
    r = E.classify_finding_evidence(f, _study("s", [f], tests), registered_run_ids=set(), run_rows={})
    assert r["status"] == E.PROVISIONAL


# --- value-linked VERIFIED (closed-loop upgrade, task 1) ---------------------

def _cls(observed, row, run="r1", tests=None, test="T"):
    f = _finding("f", observed=observed, from_test=test, run_ids=[run])
    return E.classify_finding_evidence(f, _study("s", [f], tests), {run}, {run: row})


def test_verified_requires_value_match():
    r = _cls(22.1, _row(22.0, "T"))
    assert r["status"] == E.VERIFIED
    assert "r1" in r["reason"] and "22.0" in r["reason"]
    assert r["measured_value"] == 22.0 and r["evaluated_by"] == "code"


def test_hand_typed_value_is_provisional():
    r = _cls(22.1, _row(3.4, "T"))
    assert r["status"] == E.PROVISIONAL
    assert "observed 22.1 \u2260 computed 3.4 for 'T' in run r1" in r["reason"]


def test_not_code_graded_is_provisional():
    r = _cls(22.1, _row(22.0, "T", evaluated_by="agent"))
    assert r["status"] == E.PROVISIONAL
    assert "graded by agent, not code" in r["reason"]


def test_incomplete_run_is_provisional():
    r = _cls(22.1, _row(22.0, "T", status="failed"))
    assert r["status"] == E.PROVISIONAL
    assert "run r1 status=failed" in r["reason"]


def test_missing_run_row_or_outcome_is_provisional():
    assert _cls(22.1, {})["status"] == E.PROVISIONAL
    assert _cls(22.1, _row(22.0, "other"))["status"] == E.PROVISIONAL


def test_int_exact_and_prob_near_zero():
    assert _cls(5, _row(5, "T"))["status"] == E.VERIFIED
    assert _cls(5, _row(6, "T"))["status"] == E.PROVISIONAL
    assert _cls(1e-12, _row(0.0, "T"))["status"] == E.VERIFIED
    assert _cls(1e-3, _row(0.0, "T"))["status"] == E.PROVISIONAL


def test_pass_if_tolerance_override():
    assert _cls(24.0, _row(22.0, "T"))["status"] == E.PROVISIONAL  # 9% > 5%
    tests = [{"name": "T", "pass_if": {"op": ">", "value": 1, "tolerance": 0.2}}]
    assert _cls(24.0, _row(22.0, "T"), tests=tests)["status"] == E.VERIFIED


# --- consistent_with_zero op (floppy / null-centred findings) ------------------
# Regression: _evaluate had no branch for op:consistent_with_zero, so once such a
# finding had a numeric observed it fell through to False and was FALSE-CONTRADICTED.

def _zero_test(name="floppy", **pass_if):
    return {"name": name, "pass_if": {"op": "consistent_with_zero", **pass_if}}


def test_consistent_with_zero_within_tolerance_is_verified():
    f = _finding("fz", observed=-0.0225, from_test="floppy", run_ids=["r1"])
    study = _study("s", [f], tests=[_zero_test(tolerance=0.13)])
    r = E.classify_finding_evidence(f, study, {"r1"}, {"r1": _row(-0.0225, test="floppy")})
    assert r["status"] == E.VERIFIED, r


def test_consistent_with_zero_outside_tolerance_is_contradicted():
    f = _finding("fz", observed=5.0, from_test="floppy", run_ids=["r1"])
    study = _study("s", [f], tests=[_zero_test(tolerance=0.13)])
    r = E.classify_finding_evidence(f, study, {"r1"}, {"r1": _row(5.0, test="floppy")})
    assert r["status"] == E.CONTRADICTED, r


def test_consistent_with_zero_without_tolerance_is_not_false_contradicted():
    # No tolerance: unbounded "consistent with zero" cannot be refuted numerically,
    # so a run-backed finding must still VERIFY (not CONTRADICT).
    f = _finding("fz", observed=-0.0225, from_test="floppy", run_ids=["r1"])
    study = _study("s", [f], tests=[_zero_test()])
    r = E.classify_finding_evidence(f, study, {"r1"}, {"r1": _row(-0.0225, test="floppy")})
    assert r["status"] == E.VERIFIED, r
