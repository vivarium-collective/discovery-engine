"""PR 5 commit 1 — a verdict flip on verified evidence is a first-class Discovery.

Passing a gate is not a discovery. `detect_discoveries` diffs the prior evaluation
snapshot against the new one and records only a claim verdict that FLIPPED to a settled
outcome (`supported`/`contested`) on VERIFIED evidence; `persist_cycle` appends those to
`investigation.yaml:discoveries[]`, idempotently.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from discovery_engine.discover.cycle import persist_cycle, run_cycle
from discovery_engine.phases.discovery_record import (
    build_discovery,
    build_finding_candidate,
    detect_discoveries,
)


def _snap(by_study: dict, fingerprint: str = "fp") -> dict:
    return {"by_study": by_study, "fingerprint": fingerprint}


# ---- the pure detector ----------------------------------------------------

def test_flip_to_supported_on_verified_is_a_discovery():
    prev = _snap({"s1": {"status": "unresolved", "evidence": None}})
    new = _snap({"s1": {"status": "supported", "evidence": "verified"}}, "fp2")
    found = detect_discoveries(prev, new, {"s1": {"claim": "X holds"}})
    assert len(found) == 1
    d = found[0]
    assert d["study"] == "s1" and d["from_verdict"] == "unresolved" and d["to_verdict"] == "supported"
    assert d["scope"] == "X holds" and "established" in d["novelty"]
    assert d["fingerprint"] == "fp2"


def test_flip_to_contested_records_counterevidence():
    prev = _snap({"s1": {"status": "supported", "evidence": "verified"}})
    new = _snap({"s1": {"status": "contested", "evidence": "verified"}}, "fp2")
    studies = {"s1": {"claim": "X holds", "findings": [
        {"id": "f1", "status": "contradicts", "statement": "X fails under flow",
         "evidence": {"observed": -2.0}}]}}
    d = detect_discoveries(prev, new, studies)[0]
    assert d["to_verdict"] == "contested" and "overturned" in d["novelty"]
    assert d["counterevidence"] == [{"finding": "f1", "statement": "X fails under flow", "observed": -2.0}]


def test_passing_a_gate_is_not_a_discovery():
    # settled verdict but UNVERIFIED evidence -> not a discovery
    prev = _snap({"s1": {"status": "unresolved", "evidence": None}})
    new = _snap({"s1": {"status": "supported", "evidence": "provisional"}})
    assert detect_discoveries(prev, new, {}) == []
    # a flip to unresolved/gated is not a discovery either
    new2 = _snap({"s1": {"status": "gated", "evidence": "verified"}})
    assert detect_discoveries(prev, new2, {}) == []


def test_unchanged_verdict_is_not_a_discovery():
    prev = _snap({"s1": {"status": "supported", "evidence": "verified"}})
    new = _snap({"s1": {"status": "supported", "evidence": "verified"}}, "fp2")
    assert detect_discoveries(prev, new, {}) == []


def test_discovery_id_is_stable_for_idempotency():
    a = build_discovery("s1", "unresolved", "supported", {"claim": "c"}, fingerprint="fp")
    b = build_discovery("s1", "unresolved", "supported", {"claim": "c"}, fingerprint="fp")
    assert a["id"] == b["id"]


# ---- the persist_cycle write-back -----------------------------------------

def _quant_inputs():
    """Minimal inputs that compare_hypotheses scores to a `supported` verdict (mirrors
    tests/test_evaluation.py::test_supported_needs_verified_and_quantitative)."""
    inv = {"name": "inv", "studies": ["s2"], "hypotheses": [
        {"id": "h-q", "study": "s2", "status": "open",
         "predictions": [{"observable": "flux", "expected": {"op": ">=", "value": 1e-22}}]}]}
    studies = {"s2": {"name": "s2", "findings": [
        {"id": "f", "evidence": {"measure": "flux", "observed": 1e-22}}]}}
    evidence_state = {"by_study": {"s2": {"status": "verified"}}}
    return inv, studies, evidence_state


def test_persist_writes_finding_candidate_not_discovery_on_flip(tmp_path):
    inv, studies, ev = _quant_inputs()
    inv_path = tmp_path / "investigation.yaml"
    # prior persisted state: s2 was unresolved last cycle
    on_disk = {**inv, "cycle_state": {"evaluation_snapshot": {
        "by_study": {"s2": {"status": "unresolved", "evidence": None}}, "fingerprint": "old"}}}
    inv_path.write_text(yaml.safe_dump(on_disk), encoding="utf-8")

    result = run_cycle(inv, studies, evidence_state=ev)
    persist_cycle(inv_path, inv, result, studies=studies, evidence_state=ev)

    disk = yaml.safe_load(inv_path.read_text())
    # the flip is recorded as a FindingCandidate, NOT an (unreviewed) Discovery
    assert not (disk.get("discoveries") or []), "a flip must not auto-write a Discovery"
    cands = [c for c in disk.get("finding_candidates") or [] if c["study"] == "s2"]
    assert cands, f"expected a FindingCandidate for s2's flip; got {disk.get('finding_candidates')}"
    assert cands[0]["status"] == "candidate"
    assert cands[0]["from_verdict"] == "unresolved" and cands[0]["to_verdict"] == "supported"

    # idempotent: re-persisting the same state adds no duplicate candidate
    persist_cycle(inv_path, inv, result, studies=studies, evidence_state=ev)
    disk2 = yaml.safe_load(inv_path.read_text())
    assert len([c for c in disk2.get("finding_candidates") or [] if c["study"] == "s2"]) == 1


def test_persist_writes_no_candidate_without_a_flip(tmp_path):
    inv, studies, ev = _quant_inputs()
    inv_path = tmp_path / "investigation.yaml"
    # prior state already supported/verified -> no flip this cycle
    on_disk = {**inv, "cycle_state": {"evaluation_snapshot": {
        "by_study": {"s2": {"status": "supported", "evidence": "verified"}}, "fingerprint": "old"}}}
    inv_path.write_text(yaml.safe_dump(on_disk), encoding="utf-8")
    result = run_cycle(inv, studies, evidence_state=ev)
    persist_cycle(inv_path, inv, result, studies=studies, evidence_state=ev)
    disk = yaml.safe_load(inv_path.read_text())
    assert not (disk.get("finding_candidates") or [])
    assert not (disk.get("discoveries") or [])


def test_review_promotes_candidate_to_discovery(tmp_path):
    from discovery_engine.discover.actions import review_findings
    inv, studies, ev = _quant_inputs()
    inv_path = tmp_path / "investigation.yaml"
    on_disk = {**inv, "cycle_state": {"evaluation_snapshot": {
        "by_study": {"s2": {"status": "unresolved", "evidence": None}}, "fingerprint": "old"}}}
    inv_path.write_text(yaml.safe_dump(on_disk), encoding="utf-8")
    result = run_cycle(inv, studies, evidence_state=ev)
    persist_cycle(inv_path, inv, result, studies=studies, evidence_state=ev)

    fc_id = yaml.safe_load(inv_path.read_text())["finding_candidates"][0]["id"]
    rep = review_findings(inv_path, promote=[fc_id])          # explicit human review
    assert rep["promoted"] == [fc_id]

    disk = yaml.safe_load(inv_path.read_text())
    discs = disk["discoveries"]
    assert len(discs) == 1 and discs[0]["study"] == "s2"
    assert discs[0]["finding_candidate_id"] == fc_id
    assert discs[0]["assessment"]["review_decision"] == "promote"
    assert discs[0]["assessment"]["novelty"] == "established"
    # the candidate is marked promoted + linked; a provenance record is written
    assert disk["finding_candidates"][0]["status"] == "promoted"
    assert disk["finding_candidates"][0]["discovery_id"] == discs[0]["id"]
    assert disk["review_decisions"][0]["consequence"] == "discovery"

    # idempotent: re-promoting does not duplicate the Discovery
    review_findings(inv_path, promote=[fc_id])
    assert len(yaml.safe_load(inv_path.read_text())["discoveries"]) == 1


def test_review_holds_redundant_candidate(tmp_path):
    """A candidate whose flip is already recorded as a Discovery is held, not duplicated."""
    from discovery_engine.discover.actions import review_findings
    inv_path = tmp_path / "investigation.yaml"
    cand = build_finding_candidate("s1", "unresolved", "supported", {"claim": "X"}, fingerprint="fp")
    existing = {"name": "inv", "studies": ["s1"],
                "discoveries": [{"id": "disc:s1:x", "study": "s1", "to_verdict": "supported"}],
                "finding_candidates": [cand]}
    inv_path.write_text(yaml.safe_dump(existing), encoding="utf-8")
    rep = review_findings(inv_path, promote=[cand["id"]])
    assert rep["held"] == [cand["id"]] and rep["promoted"] == []
    disk = yaml.safe_load(inv_path.read_text())
    assert len(disk["discoveries"]) == 1                      # no duplicate minted
    assert disk["finding_candidates"][0]["status"] == "held"


def test_hitl_all_promotes_nothing_unapproved(tmp_path):
    """--all under a human-in-the-loop policy promotes only pre-approved targets."""
    from discovery_engine.discover.actions import review_findings
    from discovery_engine.discover._policy import AutonomyPolicy
    inv_path = tmp_path / "investigation.yaml"
    cand = build_finding_candidate("s1", "unresolved", "supported", {"claim": "X"}, fingerprint="fp")
    inv_path.write_text(yaml.safe_dump({"name": "inv", "finding_candidates": [cand]}), encoding="utf-8")
    rep = review_findings(inv_path, promote_all=True, policy=AutonomyPolicy())  # HITL, nothing approved
    assert rep["promoted"] == [] and len(rep["skipped"]) == 1
    # autonomous policy promotes it
    rep2 = review_findings(inv_path, promote_all=True,
                           policy=AutonomyPolicy(mode="autonomous"), reviewer="engine")
    assert rep2["promoted"] == [cand["id"]]


# ---- FindingCandidate pure layer ------------------------------------------

def test_detect_finding_candidates_mirrors_flip_detection():
    from discovery_engine.phases.discovery_record import detect_finding_candidates
    prev = _snap({"s1": {"status": "unresolved", "evidence": None}})
    new = _snap({"s1": {"status": "supported", "evidence": "verified"}}, "fp2")
    cands = detect_finding_candidates(prev, new, {"s1": {"claim": "X holds"}})
    assert len(cands) == 1
    c = cands[0]
    assert c["kind"] == "finding_candidate" and c["status"] == "candidate"
    assert c["study"] == "s1" and c["to_verdict"] == "supported" and c["claim"] == "X holds"
    assert c["validation_status"] == "verified" and c["fingerprint"] == "fp2"


def test_assess_marks_novelty_and_decision():
    from discovery_engine.phases.discovery_record import (
        build_finding_candidate, assess_finding_candidate)
    c = build_finding_candidate("s1", "unresolved", "supported", {"claim": "X"}, fingerprint="fp")
    assert assess_finding_candidate(c, [])["review_decision"] == "promote"
    dup = [{"study": "s1", "to_verdict": "supported"}]
    a = assess_finding_candidate(c, dup)
    assert a["novelty"] == "redundant" and a["review_decision"] == "hold"
