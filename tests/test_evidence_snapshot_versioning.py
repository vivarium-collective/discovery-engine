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


# 2. run-data change is sensed by the hash, but is NOT stale science ----------
def test_run_data_change_moves_hash_but_is_not_stale_under_same_evaluator():
    # The measured number moving changes the provenance hash (still useful for
    # reproducibility) ...
    h1 = evidence_inputs_hash(_EV)
    changed = {"ledger": [{**_LEDGER[0], "measured_value": 0.9}]}  # the measured number moved
    h2 = evidence_inputs_hash(changed)
    assert h1 != h2
    # ... but with the SAME grader/resolver that is new evidence, not method drift:
    # a verdict flip diffed against it is a genuine Discovery and must surface, so the
    # stale gate must NOT fire on an inputs_hash change alone.
    prev = {"versions": {"grader": GRADER_VERSION, "resolver": RESOLVER_VERSION}, "inputs_hash": h1}
    assert snapshot_is_stale(prev, grader=GRADER_VERSION, resolver=RESOLVER_VERSION, inputs_hash=h2) is False
    assert snapshot_is_stale(prev, grader=GRADER_VERSION, resolver=RESOLVER_VERSION, inputs_hash=h1) is False


# 3. grader / resolver version bump invalidates ------------------------------
def test_version_bump_invalidates():
    prev_g = {"versions": {"grader": "stale", "resolver": RESOLVER_VERSION}, "inputs_hash": "h"}
    prev_r = {"versions": {"grader": GRADER_VERSION, "resolver": "stale"}, "inputs_hash": "h"}
    assert snapshot_is_stale(prev_g, grader=GRADER_VERSION, resolver=RESOLVER_VERSION, inputs_hash="h")
    assert snapshot_is_stale(prev_r, grader=GRADER_VERSION, resolver=RESOLVER_VERSION, inputs_hash="h")


# 4. method drift under a real snapshot IS flagged; new evidence is not -------
def test_evaluator_drift_is_flagged_but_new_evidence_is_not():
    # a persisted snapshot that claimed VERIFIED under some provenance
    prev = snapshot_evaluation(_CMP, _EV, [])
    drifted_hash = evidence_inputs_hash({"ledger": [{**_LEDGER[0], "measured_value": 1.5}]})
    # new evidence, SAME evaluator -> NOT stale (the flip is a genuine Discovery)
    assert snapshot_is_stale(prev, grader=GRADER_VERSION, resolver=RESOLVER_VERSION,
                             inputs_hash=drifted_hash) is False
    # grader bumped under us -> stale regardless of inputs (methodological transition)
    assert snapshot_is_stale(prev, grader="bumped", resolver=RESOLVER_VERSION,
                             inputs_hash=prev["inputs_hash"]) is True
    # same provenance -> not stale
    assert snapshot_is_stale(prev, grader=GRADER_VERSION, resolver=RESOLVER_VERSION,
                             inputs_hash=prev["inputs_hash"]) is False


# 5. back-compat: pre-versioning (0.4.x) snapshot + None ---------------------
def test_pre_versioning_snapshot_is_not_stale():
    old = {"fingerprint": "abc", "by_study": {"s1": {"status": "supported", "evidence": "verified"}}}
    assert snapshot_is_stale(old, grader=GRADER_VERSION, resolver=RESOLVER_VERSION, inputs_hash="x") is False
    assert snapshot_is_stale(None, grader=GRADER_VERSION, resolver=RESOLVER_VERSION, inputs_hash="x") is False


def test_empty_ledger_hashes_stably():
    assert evidence_inputs_hash({"ledger": []}) == evidence_inputs_hash(None) == evidence_inputs_hash({})


# ---------------------------------------------------------------------------
# DE-01 — snapshot-comparison truth table
#
# Exercises the exact gate persist_cycle applies — ``[] if snapshot_is_stale(...)
# else detect_discoveries(...)`` — over the comparison cases in the revised 0.6
# snapshot-semantics review. The invariant under test: a methodological change
# (grader/resolver version) re-baselines a flip; a change in measured inputs under
# the SAME evaluator does NOT, so a genuine new-evidence Discovery surfaces.
# ---------------------------------------------------------------------------
import pytest

from discovery_engine.phases.discovery_record import detect_discoveries

_STUDIES = {"s1": {"claim": "s1 holds at steady state",
                   "findings": [{"id": "f1", "status": "verified"}]}}


def _snap(by_study, *, grader, resolver, inputs_hash, fingerprint):
    return {"by_study": by_study, "versions": {"grader": grader, "resolver": resolver},
            "inputs_hash": inputs_hash, "fingerprint": fingerprint}


def _gate(prev, new, studies):
    """Verbatim composition from persist_cycle's discovery block."""
    nv = new.get("versions") or {}
    stale = snapshot_is_stale(prev, grader=nv.get("grader"), resolver=nv.get("resolver"),
                              inputs_hash=new.get("inputs_hash"))
    return [] if stale else detect_discoveries(prev, new, studies, fingerprint=new.get("fingerprint"))


_UNRESOLVED = {"s1": {"status": "unresolved", "evidence": "provisional"}}
_SUPPORTED = {"s1": {"status": "supported", "evidence": "verified"}}
_GV, _RV = GRADER_VERSION, RESOLVER_VERSION


@pytest.mark.parametrize("case,prev,new,expect_discoveries", [
    # new run appended, same evaluator -> genuine Discovery surfaces (the fix)
    ("new_run_same_evaluator",
     _snap(_UNRESOLVED, grader=_GV, resolver=_RV, inputs_hash="H1", fingerprint="fp1"),
     _snap(_SUPPORTED, grader=_GV, resolver=_RV, inputs_hash="H2", fingerprint="fp2"), 1),
    # same evidence, grader version changed -> methodological, re-baseline (suppress)
    ("grader_version_drift",
     _snap(_UNRESOLVED, grader="g-old", resolver=_RV, inputs_hash="H1", fingerprint="fp1"),
     _snap(_SUPPORTED, grader=_GV, resolver=_RV, inputs_hash="H1", fingerprint="fp2"), 0),
    # resolver mapping changed -> methodological, re-baseline (suppress)
    ("resolver_version_drift",
     _snap(_UNRESOLVED, grader=_GV, resolver="r-old", inputs_hash="H1", fingerprint="fp1"),
     _snap(_SUPPORTED, grader=_GV, resolver=_RV, inputs_hash="H1", fingerprint="fp2"), 0),
    # new evidence PLUS evaluator upgrade -> cannot infer a clean discovery (suppress)
    ("new_evidence_and_evaluator_upgrade",
     _snap(_UNRESOLVED, grader="g-old", resolver=_RV, inputs_hash="H1", fingerprint="fp1"),
     _snap(_SUPPORTED, grader=_GV, resolver=_RV, inputs_hash="H2", fingerprint="fp2"), 0),
    # pre-versioning (0.4.x) prev snapshot -> back-compat, detector runs normally
    ("pre_versioning_prev",
     {"by_study": _UNRESOLVED, "fingerprint": "fp1"},
     _snap(_SUPPORTED, grader=_GV, resolver=_RV, inputs_hash="H2", fingerprint="fp2"), 1),
    # identical snapshot, no verdict change -> nothing to discover
    ("no_flip",
     _snap(_SUPPORTED, grader=_GV, resolver=_RV, inputs_hash="H1", fingerprint="fp1"),
     _snap(_SUPPORTED, grader=_GV, resolver=_RV, inputs_hash="H1", fingerprint="fp1"), 0),
])
def test_snapshot_comparison_truth_table(case, prev, new, expect_discoveries):
    found = _gate(prev, new, _STUDIES)
    assert len(found) == expect_discoveries, case
    if expect_discoveries:
        assert found[0]["study"] == "s1"
        assert found[0]["to_verdict"] == "supported"


def test_regression_new_evidence_flip_is_not_rebaselined_away():
    """The 0.6.0 bug this fixes: a verdict flip caused by a NEW run (inputs_hash moved)
    under the same grader/resolver was treated as stale and the Discovery was suppressed
    forever. It must surface."""
    prev = _snap(_UNRESOLVED, grader=_GV, resolver=_RV, inputs_hash="H_before", fingerprint="fp1")
    new = _snap(_SUPPORTED, grader=_GV, resolver=_RV, inputs_hash="H_after", fingerprint="fp2")
    found = _gate(prev, new, _STUDIES)
    assert [d["study"] for d in found] == ["s1"]
    assert found[0]["from_verdict"] == "unresolved" and found[0]["to_verdict"] == "supported"
