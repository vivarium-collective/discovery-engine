"""PR 1 — one unified AutonomyPolicy for every consequential cycle op.

Pins the policy object's gate, its back-compat with the legacy ``autonomous`` bool, the
per-decision provenance record, and that ``select_for_launch`` is now a thin shim over the
same policy (so "which proposals may launch" and "may this op run" are one decision).
"""
from __future__ import annotations

from discovery_engine.discover._policy import (
    AUTONOMOUS,
    HUMAN_IN_THE_LOOP,
    AutonomyPolicy,
    decision_record,
)


# --- resolve: the legacy (policy | autonomous bool) pair -> one policy -------------------

def test_resolve_bool_backcompat():
    assert AutonomyPolicy.resolve(autonomous=True).is_autonomous
    assert not AutonomyPolicy.resolve(autonomous=False).is_autonomous
    # default (no args) is human-in-the-loop
    assert AutonomyPolicy.resolve().mode == HUMAN_IN_THE_LOOP


def test_resolve_explicit_policy_wins_over_bool():
    pol = AutonomyPolicy(mode=AUTONOMOUS)
    # an explicit policy overrides a conflicting bool
    assert AutonomyPolicy.resolve(pol, autonomous=False).is_autonomous


def test_resolve_merges_extra_approved_ids_into_policy():
    pol = AutonomyPolicy(approved_ids=frozenset({"a"}))
    merged = AutonomyPolicy.resolve(pol, approved_ids=["b"])
    assert merged.approved_ids == frozenset({"a", "b"})


# --- allows: the single gate every consequential op passes through ----------------------

def test_autonomous_allows_any_consequence():
    pol = AutonomyPolicy(mode=AUTONOMOUS)
    assert pol.allows(consequence="simulation", target="s1")
    assert pol.allows(consequence="authoring", target=None)   # no target needed in autonomous


def test_hitl_requires_per_item_approval():
    pol = AutonomyPolicy(mode=HUMAN_IN_THE_LOOP, approved_ids=frozenset({"ok"}))
    assert pol.allows(consequence="simulation", target="ok")       # approved -> runs
    assert not pol.allows(consequence="simulation", target="nope")  # unapproved -> held
    assert not pol.allows(consequence="simulation", target=None)    # nothing to approve


def test_allowlist_blocks_a_tier_even_when_autonomous():
    pol = AutonomyPolicy(mode=AUTONOMOUS, allowlist=frozenset({"render"}))
    assert pol.allows(consequence="render", target="v1")
    assert not pol.allows(consequence="simulation", target="s1")   # not on the allowlist


def test_decision_record_has_the_roadmap_shape():
    pol = AutonomyPolicy(mode=AUTONOMOUS)
    rec = decision_record(pol, actor="autonomous", consequence="simulation",
                          target="open_question:s1", rationale="ran the gate", result="executed")
    assert set(rec) >= {"policy_version", "actor", "input_hashes", "target",
                        "consequence", "rationale", "result"}
    assert rec["policy_version"] == pol.policy_version
    assert rec["result"] == "executed"


# --- select_for_launch is now a shim over the same policy (back-compat preserved) -------

def _sugg(**kw):
    return {"id": kw.get("id", "seed-x"), **kw}


def test_select_for_launch_autonomous_launches_all_non_awaiting():
    out = __import__("discovery_engine.growth.seeding", fromlist=["select_for_launch"]).select_for_launch(
        [_sugg(id="a"), _sugg(id="b")], autonomy=AUTONOMOUS)
    assert {s["id"] for s in out["launch"]} == {"a", "b"}
    assert out["held"] == []


def test_select_for_launch_hitl_holds_unapproved_launches_approved():
    from discovery_engine.growth.seeding import select_for_launch
    out = select_for_launch([_sugg(id="a"), _sugg(id="b")],
                            autonomy=HUMAN_IN_THE_LOOP, approved_ids=["a"])
    assert [s["id"] for s in out["launch"]] == ["a"]
    assert [h["suggestion"]["id"] for h in out["held"]] == ["b"]


def test_select_for_launch_always_holds_awaiting_expert():
    from discovery_engine.growth.seeding import select_for_launch
    out = select_for_launch([_sugg(id="a", awaiting_expert=True)], autonomy=AUTONOMOUS)
    assert out["launch"] == []
    assert "awaiting expert" in out["held"][0]["reason"]
