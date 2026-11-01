"""G3 from code-21's trial: the 3-part 'registered run' contract (evidence.from_test +
provenance.run_ids resolving to a runs.db row + a computed outcome) was enforced but scattered
inside classify_finding_evidence with no single validator/doc. `registered_run_contract()` is now
that one source of truth — it reports exactly which parts are missing."""
from __future__ import annotations

from discovery_engine.evidence_integrity import registered_run_contract, REGISTERED_RUN_CONTRACT


def _finding(from_test="t1", run_ids=("R1",)):
    return {"id": "f1", "evidence": {"from_test": from_test} if from_test else {},
            "provenance": {"run_ids": list(run_ids)} if run_ids else {}}


def test_contract_doc_lists_three_parts():
    assert len(REGISTERED_RUN_CONTRACT) == 3


def test_satisfied_when_all_three_hold():
    rows = {"R1": {"status": "complete", "computed_outcomes": {"t1": {"measured_value": 0.4}}}}
    c = registered_run_contract(_finding(), {"R1"}, rows)
    assert c["satisfied"] and c["missing"] == []
    assert c["parts"] == {"from_test": True, "run_ids_resolve": True, "computed_outcome": True}


def test_missing_from_test():
    c = registered_run_contract(_finding(from_test=None), {"R1"}, {})
    assert not c["satisfied"] and "evidence.from_test" in c["missing"]


def test_missing_run_ids():
    c = registered_run_contract(_finding(run_ids=()), {"R1"}, {})
    assert "provenance.run_ids" in c["missing"]


def test_run_ids_do_not_resolve():
    c = registered_run_contract(_finding(run_ids=("GHOST",)), {"R1"}, {})
    assert not c["parts"]["run_ids_resolve"]
    assert any("resolve" in m for m in c["missing"])


def test_missing_computed_outcome():
    rows = {"R1": {"status": "complete", "computed_outcomes": {}}}  # no outcome for t1
    c = registered_run_contract(_finding(), {"R1"}, rows)
    assert not c["satisfied"] and not c["parts"]["computed_outcome"]
    assert any("computed outcome" in m for m in c["missing"])
