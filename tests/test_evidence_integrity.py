"""Evidence/run reconciliation (DE 2.0 Milestone 1 / Cycle N+1 Phase A).

Milestone 1 gate: every numeric finding has a resolvable run/provenance chain OR a
visible PROVISIONAL status; a contradiction (observed violates the gate it cites) is
not silently accepted; a prose-only finding is UNSUPPORTED.
"""
from __future__ import annotations

from discovery_engine import evidence_integrity as E


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
