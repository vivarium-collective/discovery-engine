"""Task 10: `discover revise --approve|--reject` and the AI-decider seam. The only mutation
is the proposal's status + history."""
from __future__ import annotations

import pytest

from discovery_engine import discover
from discovery_engine import discovery_engine as de
from discovery_engine.study_io import load_yaml, save_yaml_atomic

PID = "rp:s3:mv1:abcd1234"


def _prop(pid=PID, status="proposed"):
    return {"id": pid, "status": status, "approval": "review",
            "source": {"kind": "model_disagreement", "study": "s3", "finding": "f", "observable": "o"},
            "affected_model_version": "mv1", "history": [{"at": "t", "event": "proposed", "by": "engine"}]}


def _ws(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    inv = tmp_path / "investigations" / "og"
    inv.mkdir(parents=True)
    save_yaml_atomic(inv / "investigation.yaml", {"studies": [], "revision_proposals": [_prop()]})
    return inv / "investigation.yaml"


def _run(tmp_path, *argv):
    return discover.main(["--workspace", str(tmp_path), "revise", "og", PID, *argv])


def test_approve_appends_history_and_flips_status(tmp_path):
    p = _ws(tmp_path)
    assert _run(tmp_path, "--approve", "--rationale", "looks right") == 0
    (prop,) = load_yaml(p)["revision_proposals"]
    assert prop["status"] == "approved"
    ev = prop["history"][-1]
    assert ev["event"] == "approved" and ev["by"] == "human" and ev["rationale"] == "looks right" and ev["at"]
    assert len(prop["history"]) == 2 and prop["source"]["study"] == "s3"


def test_reject_discharges_from_frontier(tmp_path):
    p = _ws(tmp_path)
    assert _run(tmp_path, "--reject", "--rationale", "artefact") == 0
    spec = load_yaml(p)
    (prop,) = spec["revision_proposals"]
    assert prop["status"] == "rejected" and prop["history"][-1]["event"] == "rejected"
    items = de.frontier({"revision_proposals": [_prop()]}, spec)
    assert "revision_proposed" not in {i["kind"] for i in items}


def test_missing_rationale_errors(tmp_path):
    p = _ws(tmp_path)
    with pytest.raises(SystemExit):
        _run(tmp_path, "--approve")
    with pytest.raises(ValueError):
        discover.review_revision(p, PID, "approved", "  ")
    assert load_yaml(p)["revision_proposals"][0]["status"] == "proposed"


def test_unknown_id_errors(tmp_path):
    p = _ws(tmp_path)
    with pytest.raises(ValueError, match="unknown"):
        discover.review_revision(p, "rp:nope", "approved", "x")
    with pytest.raises(SystemExit):
        discover.main(["--workspace", str(tmp_path), "revise", "og", "rp:nope", "--approve", "--rationale", "x"])


def test_decider_seam_records_discovery_ai(tmp_path, monkeypatch):
    p = _ws(tmp_path)
    monkeypatch.setattr(discover, "_cycle_with_evidence",
                        lambda *a, **k: {"revision_proposals": [_prop()]})
    gated = discover.execute_action(tmp_path, "og", autonomous=True, target=PID)
    assert gated["status"] == "gated-human"
    assert load_yaml(p)["revision_proposals"][0]["status"] == "proposed"

    def decider(target, detail):
        assert target == PID
        return {"choice": "approve", "rationale": "closes the 2D/3D gap"}

    rec = discover.execute_action(tmp_path, "og", autonomous=True, decider=decider, target=PID)
    assert rec["status"] == "executed"
    (prop,) = load_yaml(p)["revision_proposals"]
    assert prop["status"] == "approved"
    ev = prop["history"][-1]
    assert ev["event"] == "approved" and ev["by"] == "discovery-ai" and "closes the 2D/3D gap" in ev["rationale"]


def _seam(tmp_path, monkeypatch, decider, status="proposed", pid=PID, top=None):
    p = _ws(tmp_path)
    if status != "proposed":
        spec = load_yaml(p)
        spec["revision_proposals"][0]["status"] = status
        save_yaml_atomic(p, spec)
    monkeypatch.setattr(discover, "_cycle_with_evidence",
                        lambda *a, **k: {"revision_proposals": [_prop(status=status)]})
    rec = discover.execute_action(tmp_path, "og", autonomous=True, decider=decider, target=pid)
    return p, rec


def test_decider_reject_rejects(tmp_path, monkeypatch):
    p, rec = _seam(tmp_path, monkeypatch, lambda t, d: {"choice": "reject", "rationale": "artefact"})
    assert rec["status"] == "executed"
    (prop,) = load_yaml(p)["revision_proposals"]
    assert prop["status"] == "rejected" and prop["history"][-1]["by"] == "discovery-ai"
    assert "artefact" in prop["history"][-1]["rationale"]


@pytest.mark.parametrize("ret", [None, {}, {"choice": "maybe", "rationale": "x"},
                                 {"choice": "approve"}, {"choice": "approve", "rationale": "  "}])
def test_decider_unusable_answer_is_gated_and_unchanged(tmp_path, monkeypatch, ret):
    p, rec = _seam(tmp_path, monkeypatch, lambda t, d: ret)
    assert rec["status"] == "gated-human"
    (prop,) = load_yaml(p)["revision_proposals"]
    assert prop["status"] == "proposed" and len(prop["history"]) == 1


def test_non_rp_revise_model_stays_gated(tmp_path, monkeypatch):
    p = _ws(tmp_path)
    cyc = {"signals": {"anomalies": [{"study": "a", "observable": "o", "observed": 1.0,
                                      "proposed_question": "?"}]}}
    monkeypatch.setattr(discover, "_cycle_with_evidence", lambda *a, **k: cyc)
    called = []
    rec = discover.execute_action(tmp_path, "og", autonomous=True,
                                  decider=lambda t, d: called.append(t) or {"choice": "approve", "rationale": "x"},
                                  target="a:o")
    assert rec["status"] == "gated-human" and not called
    assert load_yaml(p)["revision_proposals"][0]["status"] == "proposed"


def test_blank_rationale_via_cli_errors(tmp_path):
    p = _ws(tmp_path)
    with pytest.raises(SystemExit):
        _run(tmp_path, "--approve", "--rationale", "   ")
    assert load_yaml(p)["revision_proposals"][0]["status"] == "proposed"


@pytest.mark.parametrize("st", ["approved", "rejected", "applied", "evaluated"])
def test_redeciding_non_proposed_is_blocked_without_duplicate_history(tmp_path, monkeypatch, st):
    p = _ws(tmp_path)
    spec = load_yaml(p)
    spec["revision_proposals"][0]["status"] = st
    save_yaml_atomic(p, spec)
    with pytest.raises(ValueError, match="already"):
        discover.review_revision(p, PID, "approved", "again")
    with pytest.raises(SystemExit):
        _run(tmp_path, "--reject", "--rationale", "again")
    monkeypatch.setattr(discover, "_cycle_with_evidence",
                        lambda *a, **k: {"revision_proposals": [_prop(status=st)]})
    if st in ("approved",):  # still on the frontier -> decider must no-op
        rec = discover.execute_action(tmp_path, "og", autonomous=True, target=PID,
                                      decider=lambda t, d: {"choice": "approve", "rationale": "y"})
        assert rec["status"] == "gated-human"
    (prop,) = load_yaml(p)["revision_proposals"]
    assert prop["status"] == st and len(prop["history"]) == 1
