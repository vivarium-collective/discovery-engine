"""CandidateAction planner — Discovery Engine 2.0, Milestone 2 (Cycle N+1 Phase B).

Reconciles the overlapping ``frontier`` / ``opportunities`` / ``suggest_studies`` /
``next_action`` surfaces behind one CandidateAction record, classified **REPAIR**,
**RESOLVE**, or **EXTEND**, and ordered by scientific value with prerequisite
dependencies. The engine can then explain why a hypothesis-discriminating study
outranks a generic missing-test task, *while still scheduling the prerequisite
evidence repairs first*.

- ``repair``   fix the evidence/run/provenance mismatch — a preliminary number has
               no resolvable registered run yet (Phase A found it provisional).
- ``resolve``  settle a scientific disagreement — a study whose run would separate
               competing hypotheses (the discriminating experiments).
- ``extend``   open or enable further work that neither repairs evidence nor
               discriminates a live disagreement.

Derive-first: ``plan_candidate_actions`` is a pure projection over the cycle result,
the investigation spec, and the Phase-A evidence state. It performs no I/O.
"""
from __future__ import annotations


from .discovery_engine import opportunities
from .hypotheses import discriminating_hypotheses
from .evidence_integrity import CONTRADICTED, PROVISIONAL, UNSUPPORTED

REPAIR = "repair"
RESOLVE = "resolve"
EXTEND = "extend"

_UNVERIFIED = (PROVISIONAL, UNSUPPORTED, CONTRADICTED)
_KIND_RANK = {REPAIR: 0, RESOLVE: 1, EXTEND: 2}


def _slug(target: str) -> str:
    t = str(target or "")
    return t[len("claim:"):] if t.startswith("claim:") else t


def classify_action(op: dict, evidence_by_study: dict, disc: dict) -> tuple[str, str]:
    """Classify one opportunity/frontier item. Returns ``(kind, rationale)``."""
    slug = _slug(op.get("source"))
    d = disc.get(slug)
    discriminable = bool(d and d.get("discriminable"))
    ev_status = (evidence_by_study.get(slug) or {}).get("status")
    op_type = op.get("action_type")

    if op_type in ("test", "branch") and discriminable:
        tail = (f" (evidence currently {ev_status}; needs a registered run)"
                if ev_status in _UNVERIFIED else "")
        return RESOLVE, (f"a run separates {len(d['competing'])} competing hypotheses on "
                         f"{d['observable']}{tail}")
    if ev_status in _UNVERIFIED:
        return REPAIR, (f"evidence is {ev_status}: register a real run through the gated "
                        "pipeline before this counts as support")
    if op_type in ("test", "branch"):
        return EXTEND, "a new study that opens or enables further work"
    return EXTEND, (op.get("question") or "extends the investigation")


def plan_candidate_actions(cycle_result: dict, inv_spec: dict, evidence_state: dict) -> list[dict]:
    """Project the frontier into classified CandidateActions (the shared planner).

    Emits a single ``R0`` REPAIR when Phase A found any unverified findings, then one
    CandidateAction per ranked opportunity. A RESOLVE on a study whose evidence is
    unverified depends on ``R0`` — you cannot trust a discrimination result built on
    unregistered evidence."""
    evidence_by_study = (evidence_state or {}).get("by_study", {})
    summary = (evidence_state or {}).get("summary", {})
    disc = discriminating_hypotheses(inv_spec or {})
    actions: list[dict] = []

    n_unverified = sum(summary.get(s, 0) for s in _UNVERIFIED)
    r0 = None
    if n_unverified:
        r0 = {"id": "R0", "kind": REPAIR, "target": "investigation",
              "question": "register runs + provenance for the preliminary findings",
              "discriminates": [], "value": 1.0, "discrimination": 0.0, "depends_on": [],
              "rationale": f"{n_unverified} finding(s) lack a resolvable registered run "
                           "(Phase A): repair execution/provenance before promoting any to evidence"}
        actions.append(r0)

    for op in opportunities(cycle_result, inv_spec):
        kind, rationale = classify_action(op, evidence_by_study, disc)
        slug = _slug(op.get("source"))
        d = disc.get(slug) or {}
        ev_status = (evidence_by_study.get(slug) or {}).get("status")
        depends = [r0["id"]] if (r0 and kind == RESOLVE and ev_status in _UNVERIFIED) else []
        actions.append({
            "id": op.get("id"), "kind": kind, "target": op.get("source"),
            "question": op.get("question") or "",
            "discriminates": list(dict.fromkeys(
                c.get("hypothesis") for c in d.get("competing", []))) if kind == RESOLVE else [],
            "value": (op.get("value") or {}).get("score", 0.0),
            "discrimination": (op.get("value") or {}).get("discriminate", 0.0),
            "depends_on": depends, "rationale": rationale,
        })
    return actions


def select_portfolio(actions: list[dict], budget: int | None = None) -> list[dict]:
    """Order the CandidateActions for execution: REPAIR prerequisites first, then
    RESOLVE by discrimination then value, then EXTEND — never scheduling an action
    before an action it depends on. ``budget`` caps the count (prerequisites of a
    kept action are always retained)."""
    ordered = sorted(actions, key=lambda a: (_KIND_RANK.get(a["kind"], 9),
                                             -a.get("discrimination", 0.0), -a.get("value", 0.0),
                                             str(a.get("id"))))
    # stable topological nudge: ensure each action's dependencies precede it
    by_id = {a["id"]: a for a in ordered}
    out: list[dict] = []
    seen: set = set()

    def emit(a):
        if a["id"] in seen:
            return
        for dep in a.get("depends_on", []):
            if dep in by_id:
                emit(by_id[dep])
        seen.add(a["id"])
        out.append(a)

    for a in ordered:
        emit(a)
    if budget is not None:
        kept, keep_ids = [], set()
        for a in out:
            if len(keep_ids) >= budget and a["id"] not in keep_ids:
                continue
            kept.append(a); keep_ids.add(a["id"])
        out = kept
    return out
