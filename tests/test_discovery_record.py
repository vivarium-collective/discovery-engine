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
from discovery_engine.phases.discovery_record import build_discovery, detect_discoveries


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


def test_persist_writes_discovery_on_verdict_flip(tmp_path):
    inv, studies, ev = _quant_inputs()
    inv_path = tmp_path / "investigation.yaml"
    # prior persisted state: s2 was unresolved last cycle
    on_disk = {**inv, "cycle_state": {"evaluation_snapshot": {
        "by_study": {"s2": {"status": "unresolved", "evidence": None}}, "fingerprint": "old"}}}
    inv_path.write_text(yaml.safe_dump(on_disk), encoding="utf-8")

    result = run_cycle(inv, studies, evidence_state=ev)
    persist_cycle(inv_path, inv, result, studies=studies, evidence_state=ev)

    disk = yaml.safe_load(inv_path.read_text())
    discs = disk.get("discoveries") or []
    flip = [d for d in discs if d["study"] == "s2"]
    assert flip, f"expected a Discovery for s2's flip; got {discs}"
    assert flip[0]["from_verdict"] == "unresolved" and flip[0]["to_verdict"] == "supported"

    # idempotent: re-persisting the same state adds no duplicate
    persist_cycle(inv_path, inv, result, studies=studies, evidence_state=ev)
    disk2 = yaml.safe_load(inv_path.read_text())
    assert len([d for d in disk2.get("discoveries") or [] if d["study"] == "s2"]) == 1


def test_persist_writes_no_discovery_without_a_flip(tmp_path):
    inv, studies, ev = _quant_inputs()
    inv_path = tmp_path / "investigation.yaml"
    # prior state already supported/verified -> no flip this cycle
    on_disk = {**inv, "cycle_state": {"evaluation_snapshot": {
        "by_study": {"s2": {"status": "supported", "evidence": "verified"}}, "fingerprint": "old"}}}
    inv_path.write_text(yaml.safe_dump(on_disk), encoding="utf-8")
    result = run_cycle(inv, studies, evidence_state=ev)
    persist_cycle(inv_path, inv, result, studies=studies, evidence_state=ev)
    disk = yaml.safe_load(inv_path.read_text())
    assert not (disk.get("discoveries") or [])
