"""Task 9: revision proposals / anomalies / model disagreements on the frontier
(non-blocking), the REVISE planner kind, and idempotent proposal persistence."""
from __future__ import annotations

from discovery_engine import discover
from discovery_engine import discovery_engine as de
from discovery_engine.candidate_action import plan_candidate_actions
from discovery_engine.study_io import load_yaml, save_yaml_atomic

C2D = "viva_x.composites.membrane_2d.build"


def _prop(pid="rp:s3:mv1:abcd1234", status="proposed", study="s3", obs="enclosure_probability"):
    return {"id": pid, "status": status, "approval": "review",
            "source": {"kind": "model_disagreement", "study": study, "finding": "f", "observable": obs},
            "affected_model_version": "mv1", "history": [{"at": "t", "event": "proposed", "by": "engine"}]}


def _kinds(items):
    return {i["kind"] for i in items}


def test_proposal_becomes_revision_proposed_item():
    items = de.frontier({"revision_proposals": [_prop()]}, {})
    (it,) = [i for i in items if i["kind"] == "revision_proposed"]
    assert it["target"] == "rp:s3:mv1:abcd1234" and it["op"] == "revise_model" and it["priority"] == 58
    ex = [p for p in de.execute_plan({"revision_proposals": [_prop()]}, {}) if p["action_type"] == "revise_model"][0]
    assert ex["consequence"] == "review" and ex["auto_safe"] is False
    assert ex["executor"] == "discover revise"
    assert ex["command"] == "discover revise <inv> rp:s3:mv1:abcd1234 --apply"


def test_executor_for_revise_model():
    assert de._executor_for("revise_model", "rp:x") == {
        "executor": "discover revise", "command": "discover revise <inv> rp:x --apply",
        "consequence": "review", "auto_safe": False}


def test_terminal_and_resolved_proposals_excluded():
    for st in ("rejected", "applied", "evaluated"):
        assert "revision_proposed" not in _kinds(de.frontier({"revision_proposals": [_prop(status=st)]}, {}))
    inv = {"resolutions": [{"kind": "revision_proposed", "target": "rp:s3:mv1:abcd1234", "rationale": "x"}]}
    assert "revision_proposed" not in _kinds(de.frontier({"revision_proposals": [_prop()]}, inv))
    # approved stays open; on-disk status wins over the freshly-proposed copy
    assert "revision_proposed" in _kinds(de.frontier({"revision_proposals": [_prop(status="approved")]}, {}))
    disk = {"revision_proposals": [_prop(status="rejected")]}
    assert "revision_proposed" not in _kinds(de.frontier({"revision_proposals": [_prop()]}, disk))


def test_new_kinds_do_not_block_closure():
    assert not {"revision_proposed", "anomaly", "model_disagreement"} & set(de._CLOSURE_BLOCKING)
    cyc = {"revision_proposals": [_prop()],
           "signals": {"anomalies": [{"study": "a", "observable": "o", "observed": 1.0, "proposed_question": "?"}],
                       "model_disagreements": [{"study": "b", "observable": "p", "disagreement": True,
                                                "arms": {"x": 1, "y": 2}, "spread": 1}]}}
    assert de.closure({}, {})["closed"] is True
    clo = de.closure(cyc, {})
    assert clo["closed"] is True and clo["open_items"] == 3


def test_raw_signals_and_dedup_against_proposal():
    sig = {"anomalies": [{"study": "a", "observable": "o", "observed": 1.0, "proposed_question": "?"}],
           "model_disagreements": [{"study": "s3", "observable": "enclosure_probability", "disagreement": True,
                                    "arms": {"x": 1, "y": 2}, "spread": 1},
                                   {"study": "q", "observable": "z", "disagreement": False,
                                    "arms": {"x": 1, "y": 1}, "spread": 0}]}
    items = de.frontier({"signals": sig, "revision_proposals": [_prop()]}, {})
    by = {i["kind"]: i for i in items}
    assert by["anomaly"]["priority"] == 48 and by["anomaly"]["op"] == "revise_model"
    # s3/enclosure_probability is covered by the proposal -> no redundant raw item; q is not live
    assert "model_disagreement" not in by
    items = de.frontier({"signals": sig}, {})
    md = [i for i in items if i["kind"] == "model_disagreement"]
    assert len(md) == 1 and md[0]["priority"] == 48 and md[0]["op"] == "revise_model"
    # a rejected persisted proposal still covers its signal (no endless re-raise)
    disk = {"revision_proposals": [_prop(status="rejected")]}
    assert "model_disagreement" not in _kinds(de.frontier({"signals": sig}, disk))


def test_plan_emits_revise_between_repair_and_resolve_with_consequence():
    cyc = {"revision_proposals": [_prop()]}
    acts = plan_candidate_actions(cyc, {}, {"summary": {"provisional": 1}, "by_study": {}})
    kinds = [a["kind"] for a in acts]
    assert "revise" in kinds and "repair" in kinds
    rev = [a for a in acts if a["kind"] == "revise"][0]
    assert rev["consequence"] == "review" and rev["target"] == "rp:s3:mv1:abcd1234"
    assert all("consequence" in a for a in acts)
    from discovery_engine.candidate_action import select_portfolio, _KIND_RANK
    assert _KIND_RANK["repair"] < _KIND_RANK["revise"] < _KIND_RANK["resolve"]
    assert [a["kind"] for a in select_portfolio(acts)][0] == "repair"


def _studies():
    return {"s3": {
        "name": "s3", "topic": "membrane", "fidelity": "reduced", "observables": ["enclosure_probability"],
        "baseline": [{"name": "baseline", "composite": C2D, "params": {"membrane_bending": 1.0}}],
        "behavior_tests": [{"name": "t", "pass_if": {"low": 0.3, "high": 0.8}}],
        "findings": [{"id": "f-s3", "evidence": {"measure": "enclosure_probability",
                                                  "by_topology": {"2d": 0.12, "3d": 0.71}}}]}}


def test_run_cycle_includes_proposals_pure_and_closure_unchanged(tmp_path):
    inv = {"studies": ["s3"], "hypotheses": []}
    res = discover.run_cycle(inv, _studies())
    assert len(res["revision_proposals"]) == 1
    assert res["revision_proposals"][0]["source"]["study"] == "s3"
    assert "revision_proposed" in _kinds(de.frontier(res, inv))
    assert "model_disagreement" not in _kinds(de.frontier(res, inv))  # covered by the proposal
    assert inv == {"studies": ["s3"], "hypotheses": []}
    base = de.closure({**res, "revision_proposals": [], "signals": {}}, inv)
    assert de.closure(res, inv)["closed"] == base["closed"]


def test_persist_appends_idempotently_and_preserves_status(tmp_path):
    p = tmp_path / "investigation.yaml"
    inv = {"question": "Q?", "studies": ["s3"], "hypotheses": []}
    save_yaml_atomic(p, inv)
    studies = _studies()
    res = discover.run_cycle(inv, studies)
    discover.persist_cycle(p, inv, res, studies=studies)
    rows = load_yaml(p)["revision_proposals"]
    assert len(rows) == 1 and rows[0]["id"] == res["revision_proposals"][0]["id"]
    # human approves on disk; the next cycle must not duplicate or reset it
    disk = load_yaml(p)
    disk["revision_proposals"][0]["status"] = "approved"
    disk["revision_proposals"][0]["history"].append({"at": "t2", "event": "approved", "by": "me"})
    save_yaml_atomic(p, disk)
    res2 = discover.run_cycle(load_yaml(p), studies)
    discover.persist_cycle(p, inv, res2, studies=studies)
    rows = load_yaml(p)["revision_proposals"]
    assert len(rows) == 1 and rows[0]["status"] == "approved" and len(rows[0]["history"]) == 2
