"""Versioned EvidenceSnapshot + stale-evidence gate (0.5-plan upgrades 02/03, det. slice).

The evaluation snapshot now records the grader/resolver versions + an inputs_hash over the
measured evidence, so identical inputs recompute byte-equivalently and any drift (run data,
grader, or resolver version) is detectable as stale rather than silently carried forward.
"""
from __future__ import annotations

from discovery_engine.phases.evaluation import (
    SNAPSHOT_SCHEMA_VERSION,
    evidence_inputs_hash,
    snapshot_evaluation,
    snapshot_is_stale,
)
from discovery_engine.study.evaluator import GRADER_VERSION, RESOLVER_VERSION

_CMP = {"s1": {"status": "supported", "favored": "h1", "model_version": "mv1"}}
_LEDGER = [{"study": "s1", "finding": "f1", "from_test": "t1", "run_ids": ["r1"],
            "observed": 0.3, "measured_value": 0.3, "status": "verified"}]
_EV = {"by_study": {"s1": {"status": "verified"}}, "ledger": _LEDGER}


# 1. byte-equivalence --------------------------------------------------------
def test_snapshot_is_byte_equivalent_modulo_timestamp():
    a = snapshot_evaluation(_CMP, _EV, [])
    b = snapshot_evaluation(_CMP, _EV, [])
    assert a.pop("computed_at") and b.pop("computed_at")  # present + truthy
    assert a == b                                         # identical once the stamp is removed
    assert a["schema_version"] == SNAPSHOT_SCHEMA_VERSION
    assert a["versions"] == {"grader": GRADER_VERSION, "resolver": RESOLVER_VERSION}
    assert a["inputs_hash"] == evidence_inputs_hash(_EV)
    # fingerprint/by_study/model_versions unchanged shape (cycle + discovery_record consumers)
    assert set(a) >= {"fingerprint", "by_study", "model_versions", "proposals"}


# 2. run-data change invalidates --------------------------------------------
def test_run_data_change_invalidates_inputs_hash():
    h1 = evidence_inputs_hash(_EV)
    changed = {"ledger": [{**_LEDGER[0], "measured_value": 0.9}]}  # the measured number moved
    h2 = evidence_inputs_hash(changed)
    assert h1 != h2
    prev = {"versions": {"grader": GRADER_VERSION, "resolver": RESOLVER_VERSION}, "inputs_hash": h1}
    assert snapshot_is_stale(prev, grader=GRADER_VERSION, resolver=RESOLVER_VERSION, inputs_hash=h2) is True
    assert snapshot_is_stale(prev, grader=GRADER_VERSION, resolver=RESOLVER_VERSION, inputs_hash=h1) is False


# 3. grader / resolver version bump invalidates ------------------------------
def test_version_bump_invalidates():
    prev_g = {"versions": {"grader": "stale", "resolver": RESOLVER_VERSION}, "inputs_hash": "h"}
    prev_r = {"versions": {"grader": GRADER_VERSION, "resolver": "stale"}, "inputs_hash": "h"}
    assert snapshot_is_stale(prev_g, grader=GRADER_VERSION, resolver=RESOLVER_VERSION, inputs_hash="h")
    assert snapshot_is_stale(prev_r, grader=GRADER_VERSION, resolver=RESOLVER_VERSION, inputs_hash="h")


# 4. stale -> not trusted (never silently carried as still-verified) ---------
def test_stale_verified_snapshot_is_flagged_not_silently_trusted():
    # a persisted snapshot that claimed VERIFIED under old provenance
    prev = snapshot_evaluation(_CMP, _EV, [])
    drifted_hash = evidence_inputs_hash({"ledger": [{**_LEDGER[0], "measured_value": 1.5}]})
    assert snapshot_is_stale(prev, grader=GRADER_VERSION, resolver=RESOLVER_VERSION,
                             inputs_hash=drifted_hash) is True
    # same provenance -> trusted (not stale)
    assert snapshot_is_stale(prev, grader=GRADER_VERSION, resolver=RESOLVER_VERSION,
                             inputs_hash=prev["inputs_hash"]) is False


# 5. back-compat: pre-versioning (0.4.x) snapshot + None ---------------------
def test_pre_versioning_snapshot_is_not_stale():
    old = {"fingerprint": "abc", "by_study": {"s1": {"status": "supported", "evidence": "verified"}}}
    assert snapshot_is_stale(old, grader=GRADER_VERSION, resolver=RESOLVER_VERSION, inputs_hash="x") is False
    assert snapshot_is_stale(None, grader=GRADER_VERSION, resolver=RESOLVER_VERSION, inputs_hash="x") is False


def test_empty_ledger_hashes_stably():
    assert evidence_inputs_hash({"ledger": []}) == evidence_inputs_hash(None) == evidence_inputs_hash({})
