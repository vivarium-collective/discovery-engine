"""The model-upgrade decision gate: a candidate change is justified only by
VERIFIED evidence; a CONTRADICTED required/guard finding blocks it; anything
not-yet-verified leaves it insufficient. Ranked, and recorded with the evidence
signature it rests on so drift is detectable."""
from discovery_engine.discover.upgrade_decision import (
    GO,
    INSUFFICIENT,
    NO_GO,
    decide_candidate,
    record_upgrade_decision,
    run_upgrade_decision,
)
from discovery_engine.phases.evidence_integrity import (
    CONTRADICTED,
    PROVISIONAL,
    UNSUPPORTED,
    VERIFIED,
)


def _ledger(**status_by_id):
    return {fid: {"finding": fid, "status": st, "observed": 1.0, "measured_value": 1.0,
                  "reason": f"{st}"} for fid, st in status_by_id.items()}


def test_go_when_all_required_verified():
    led = _ledger(f1=VERIFIED, f2=VERIFIED)
    d = decide_candidate({"id": "c", "change": "x", "requires": ["f1", "f2"]}, led)
    assert d["verdict"] == GO and d["n_verified"] == 2


def test_insufficient_when_a_required_is_unverified():
    led = _ledger(f1=VERIFIED, f2=PROVISIONAL)
    d = decide_candidate({"id": "c", "requires": ["f1", "f2"]}, led)
    assert d["verdict"] == INSUFFICIENT and "f2" in d["rationale"]


def test_insufficient_when_required_unsupported():
    led = _ledger(f1=UNSUPPORTED)
    d = decide_candidate({"id": "c", "requires": ["f1"]}, led)
    assert d["verdict"] == INSUFFICIENT


def test_no_go_when_required_contradicted():
    led = _ledger(f1=VERIFIED, f2=CONTRADICTED)
    d = decide_candidate({"id": "c", "requires": ["f1", "f2"]}, led)
    assert d["verdict"] == NO_GO and "CONTRADICT" in d["rationale"].upper()


def test_no_go_when_guard_contradicted():
    led = _ledger(f1=VERIFIED, g1=CONTRADICTED)
    d = decide_candidate({"id": "c", "requires": ["f1"], "contradicted_if": ["g1"]}, led)
    assert d["verdict"] == NO_GO


def test_unknown_finding_is_not_silently_passed():
    led = _ledger(f1=VERIFIED)
    d = decide_candidate({"id": "c", "requires": ["f1", "ghost"]}, led)
    assert d["verdict"] == INSUFFICIENT and "ghost" in d["rationale"]


def test_no_requires_is_insufficient_not_go():
    d = decide_candidate({"id": "c", "change": "x"}, _ledger())
    assert d["verdict"] == INSUFFICIENT


def test_signature_moves_with_evidence():
    a = decide_candidate({"id": "c", "requires": ["f1"]}, _ledger(f1=VERIFIED))
    b = decide_candidate({"id": "c", "requires": ["f1"]}, _ledger(f1=UNSUPPORTED))
    assert a["evidence_signature"] != b["evidence_signature"]


def _two_finding_study(slug, f_status_pairs):
    """A study whose findings reconcile to the requested statuses: a VERIFIED
    finding needs a code-graded registered run matching its observed value; an
    UNSUPPORTED one is prose-only (no observed)."""
    findings, runs = [], []
    tests = []
    for fid, st in f_status_pairs:
        if st == VERIFIED:
            findings.append({"id": fid, "tier": "observation", "summary": fid,
                             "evidence": {"from_test": f"t_{fid}", "observed": 5.0},
                             "provenance": {"run_ids": [f"r_{fid}"]}})
            tests.append({"name": f"t_{fid}", "pass_if": {"max": 10}, "evaluated_by": "code"})
            runs.append({"run_id": f"r_{fid}", "name": f"r_{fid}", "status": "complete",
                         "computed_outcomes": {f"t_{fid}": {"measured_value": 5.0, "evaluated_by": "code"}}})
        else:  # unsupported — prose only
            findings.append({"id": fid, "tier": "observation", "summary": fid})
    return (slug, {"name": slug, "behavior_tests": tests, "findings": findings, "runs": runs},
            {slug: runs})


def test_run_upgrade_decision_ranks_and_recommends(tmp_path):
    slug, study, runs_by_study = _two_finding_study("s1", [("fa", VERIFIED), ("fb", UNSUPPORTED)])
    inv = {
        "name": "inv", "studies": [slug],
        "upgrade_candidates": [
            {"id": "ready", "change": "A", "requires": ["fa"]},
            {"id": "blocked", "change": "B", "requires": ["fb"]},
        ],
    }
    result = run_upgrade_decision(inv, {slug: study}, runs_by_study)
    order = [c["id"] for c in result["candidates"]]
    assert order[0] == "ready"                 # GO ranks first
    assert result["recommended"] == "ready"
    verdicts = {c["id"]: c["verdict"] for c in result["candidates"]}
    assert verdicts == {"ready": GO, "blocked": INSUFFICIENT}


def test_record_persists_block_and_resolution(tmp_path):
    import yaml
    inv_path = tmp_path / "investigation.yaml"
    inv_path.write_text(yaml.safe_dump({"name": "inv", "studies": ["s1"]}))
    result = {"recommended": "ready", "evidence_signature": "deadbeef",
              "candidates": [{"id": "ready", "change": "A", "verdict": GO,
                              "rationale": "ok", "evidence_signature": "deadbeef"},
                             {"id": "open", "change": "B", "verdict": INSUFFICIENT,
                              "rationale": "nope", "evidence_signature": "cafe"}]}
    record_upgrade_decision(inv_path, result)
    saved = yaml.safe_load(inv_path.read_text())
    assert saved["upgrade_decision"]["recommended"] == "ready"
    res = saved.get("resolutions") or []
    # the GO candidate is discharged as an open_decision resolution; the insufficient one is not
    targets = {r["target"] for r in res}
    assert "upgrade:ready" in targets and "upgrade:open" not in targets
