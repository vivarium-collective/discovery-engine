"""PR 5 commit 3 — fold reconcile_and_reopen into a first-class `reopen` advance op.

Human feedback used to re-enter the loop only through the side `ingest-feedback` CLI.
Now staged feedback (a CompiledRequest with a reopen action, on
`investigation.yaml:pending_feedback[]`) surfaces as a `reopen` op in the frontier and
`execute_plan`, and `advance` applies it under the autonomy policy — then records its id
in `applied_feedback[]` so it is not re-applied.
"""
from __future__ import annotations

import yaml

from discovery_engine.discover import advance_cycle
from discovery_engine.projection import engine as de


def _staged_reopen(fid: str = "fb1") -> dict:
    return {"id": fid, "summary": {}, "feedback": {"human_feedback_to_engine": "reconsider the verdict"},
            "actions": [{"type": "reopen", "status": "open", "verdict_status": "reopened",
                         "reason": "a new counter-result refutes the closed verdict"}]}


def _inv(pending=True, applied=False) -> dict:
    inv = {"name": "inv", "studies": ["s1"], "status": "closed",
           "executive": {"verdict_status": "supported"}}
    if pending:
        inv["pending_feedback"] = [_staged_reopen()]
    if applied:
        inv["applied_feedback"] = [{"feedback": "fb1"}]
    return inv


# ---- derivation: reopen appears in the frontier + execute_plan ------------

def test_pending_feedback_raises_a_reopen_item():
    fr = de.frontier({}, _inv())
    reopen = [i for i in fr if i["kind"] == "pending_feedback"]
    assert reopen and reopen[0]["op"] == "reopen" and reopen[0]["target"] == "fb1"


def test_reopen_appears_in_execute_plan():
    plan = de.execute_plan({}, _inv())
    rp = [p for p in plan if p["action_type"] == "reopen"]
    assert rp, "reopen must appear in execute_plan"
    assert rp[0]["consequence"] == "reopen" and rp[0]["auto_safe"] is False


def test_applied_feedback_is_not_re_raised():
    fr = de.frontier({}, _inv(applied=True))
    assert not [i for i in fr if i["kind"] == "pending_feedback"]


def test_executor_for_reopen():
    ex = de._executor_for("reopen", "fb1")
    assert ex["consequence"] == "reopen" and ex["executor"] == "reconcile-and-reopen"


# ---- advance applies it under the policy ----------------------------------

def _noop(command, ws_root, *, worktree=None, **kw):
    return {"ok": True, "returncode": 0, "run_id": "stub", "tail": ""}


def _ws(tmp_path, **inv_kw):
    ws = tmp_path / "ws"
    (ws / "investigations" / "inv").mkdir(parents=True)
    (ws / "studies" / "s1").mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: w\n", encoding="utf-8")
    (ws / "investigations" / "inv" / "investigation.yaml").write_text(
        yaml.safe_dump(_inv(**inv_kw), sort_keys=False), encoding="utf-8")
    (ws / "studies" / "s1" / "study.yaml").write_text(
        yaml.safe_dump({"schema_version": 4, "name": "s1", "investigation": "inv",
                        "claim": "X holds"}, sort_keys=False), encoding="utf-8")
    return ws


def test_advance_applies_the_reopen_and_is_idempotent(tmp_path):
    ws = _ws(tmp_path)
    inv_path = ws / "investigations" / "inv" / "investigation.yaml"
    advance_cycle(ws, "inv", autonomous=True, runner=_noop, max_runs=4)

    disk = yaml.safe_load(inv_path.read_text())
    assert disk["status"] == "open"                                  # reopened
    assert disk["executive"]["verdict_status"] == "reopened"
    assert any(a.get("feedback") == "fb1" for a in disk.get("applied_feedback") or [])

    # second advance: the feedback is applied, so it is not re-applied (one record only)
    advance_cycle(ws, "inv", autonomous=True, runner=_noop, max_runs=4)
    disk2 = yaml.safe_load(inv_path.read_text())
    assert len([a for a in disk2.get("applied_feedback") or [] if a.get("feedback") == "fb1"]) == 1


def test_non_autonomous_advance_does_not_reopen(tmp_path):
    ws = _ws(tmp_path)
    inv_path = ws / "investigations" / "inv" / "investigation.yaml"
    advance_cycle(ws, "inv", autonomous=False, runner=_noop, max_runs=4)
    disk = yaml.safe_load(inv_path.read_text())
    assert disk["status"] == "closed"                                # still gated
    assert not (disk.get("applied_feedback") or [])
