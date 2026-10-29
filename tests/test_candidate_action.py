"""CandidateAction planner (DE 2.0 Milestone 2 / Cycle N+1 Phase B).

Milestone 2 gate: the planner classifies REPAIR / RESOLVE / EXTEND, ranks a
hypothesis-discriminating study above a generic task, and schedules the evidence
repair as a prerequisite of the discriminating study built on it.
"""
from __future__ import annotations

from discovery_engine import candidate_action as CA
from discovery_engine import evidence_integrity as E


def _cycle(open_calls=None, edges=None):
    return {"knowledge": {"verdicts": {}, "behaviors": {}},
            "argument": {"claims": {}, "edges": edges or []},
            "narrative": {"units": []}, "audit": [], "gates": [],
            "open_calls": open_calls or [], "validations": []}


def _inv_discriminable(slug="s"):
    return {"studies": [slug], "hypotheses": [
        {"id": "hi", "study": slug, "status": "open",
         "predictions": [{"observable": "x", "expected": {"op": ">=", "value": 0.5}}]},
        {"id": "lo", "study": slug, "status": "open",
         "predictions": [{"observable": "x", "expected": {"op": "<=", "value": 0.5}}]},
    ]}


def test_classify_discriminable_test_is_resolve():
    op = {"source": "claim:s", "action_type": "test"}
    disc = {"s": {"observable": "x", "discriminable": True,
                  "competing": [{"hypothesis": "hi"}, {"hypothesis": "lo"}]}}
    kind, why = CA.classify_action(op, {}, disc)
    assert kind == CA.RESOLVE and "separates" in why


def test_classify_unverified_nondiscriminable_is_repair():
    op = {"source": "s", "action_type": "test"}
    ev = {"s": {"status": E.PROVISIONAL}}
    kind, _ = CA.classify_action(op, ev, {})
    assert kind == CA.REPAIR


def test_plan_emits_r0_and_resolve_depends_on_it():
    cyc = _cycle(open_calls=[{"kind": "open_question", "title": "Q", "refs": ["claim:s"]}])
    inv = _inv_discriminable("s")
    ev_state = {"summary": {E.PROVISIONAL: 1}, "by_study": {"s": {"status": E.PROVISIONAL}}}
    actions = CA.plan_candidate_actions(cyc, inv, ev_state)
    r0 = next(a for a in actions if a["id"] == "R0")
    assert r0["kind"] == CA.REPAIR
    resolves = [a for a in actions if a["kind"] == CA.RESOLVE]
    assert resolves and all("R0" in a["depends_on"] for a in resolves)
    assert resolves[0]["discriminates"] == ["hi", "lo"] or set(resolves[0]["discriminates"]) == {"hi", "lo"}


def test_portfolio_orders_repair_first_then_resolve_before_extend():
    actions = [
        {"id": "e1", "kind": CA.EXTEND, "value": 0.9, "discrimination": 0.0, "depends_on": []},
        {"id": "x", "kind": CA.RESOLVE, "value": 0.4, "discrimination": 1.0, "depends_on": ["R0"]},
        {"id": "R0", "kind": CA.REPAIR, "value": 1.0, "discrimination": 0.0, "depends_on": []},
    ]
    order = [a["id"] for a in CA.select_portfolio(actions)]
    assert order.index("R0") < order.index("x") < order.index("e1")  # repair, resolve, extend


def test_portfolio_keeps_a_prerequisite_even_under_budget():
    actions = [
        {"id": "R0", "kind": CA.REPAIR, "value": 1.0, "discrimination": 0.0, "depends_on": []},
        {"id": "x", "kind": CA.RESOLVE, "value": 0.4, "discrimination": 1.0, "depends_on": ["R0"]},
    ]
    out = {a["id"] for a in CA.select_portfolio(actions, budget=1)}
    assert "R0" in out  # prerequisite retained
