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


def _study(slug, findings, tests=None):
    return {"name": slug, "findings": findings, "behavior_tests": tests or []}


def test_resolvable_run_chain_is_verified():
    f = _finding("f1", observed=0.9, from_test="gate", run_ids=["r1"])
    r = E.classify_finding_evidence(f, _study("s", [f]), registered_run_ids={"r1"})
    assert r["status"] == E.VERIFIED


def test_numeric_result_without_registered_run_is_provisional():
    f = _finding("f2", observed=0.9, from_test="gate")  # no run_ids
    r = E.classify_finding_evidence(f, _study("s", [f]), registered_run_ids=set())
    assert r["status"] == E.PROVISIONAL
    assert "no resolvable registered run" in r["reason"]


def test_run_ids_that_do_not_resolve_are_provisional():
    f = _finding("f3", observed=0.9, from_test="gate", run_ids=["ghost"])
    r = E.classify_finding_evidence(f, _study("s", [f]), registered_run_ids={"r1"})
    assert r["status"] == E.PROVISIONAL


def test_prose_only_finding_is_unsupported():
    f = {"id": "f4", "statement": "we believe it works", "evidence": {}}
    r = E.classify_finding_evidence(f, _study("s", [f]), registered_run_ids=set())
    assert r["status"] == E.UNSUPPORTED


def test_observed_violating_its_pass_if_is_contradicted():
    tests = [{"name": "gate", "pass_if": {"op": ">", "value": 0.5}}]
    f = _finding("f5", observed=0.2, from_test="gate", run_ids=["r1"])
    r = E.classify_finding_evidence(f, _study("s", [f], tests), registered_run_ids={"r1"})
    assert r["status"] == E.CONTRADICTED


def test_reconcile_rolls_up_study_to_its_weakest_numeric_finding():
    inv = {"studies": ["s1", "s2"]}
    studies = {
        "s1": _study("s1", [_finding("a", 0.9, "g", ["r1"]), _finding("b", 0.1, "g")]),  # verified + provisional
        "s2": _study("s2", [_finding("c", 0.7, "g", ["r2"])]),                            # verified
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
    r = E.classify_finding_evidence(f, _study("s", [f], tests), registered_run_ids=set())
    assert r["status"] == E.PROVISIONAL
