"""Synthesis spine + open calls — formalize Plan-progress and Decisions (RFC-0005).

A plan gate groups member studies under a criterion (a sub-question) and extends
a parent gate, forming the campaign's synthesis spine. Its verdict is rolled up
from its members' CANONICAL verdicts — one vocabulary across the Verdict,
Narrative and Plan-progress views, not a parallel MET/REFUTED scheme.

The open calls unify the authored ``executive.decisions_needed`` with the derived
open items the structure already produces — narrative-audit gaps (a gap becomes
an investigation request), open-question claims (unassessed/inconclusive), and
verdict divergences — each typed and traceable to its source, with an action.

Public API
----------
gate_verdict(member_verdicts) -> str
synthesize_gates(inv_spec, claims) -> list[dict]
open_calls(inv_spec, claims, gaps) -> list[dict]
"""
from __future__ import annotations


def gate_verdict(member_verdicts: list) -> str:
    """Roll up a gate's canonical verdict from its member studies' verdicts.

    ``unassessed``/empty members are dropped; a homogeneous set maps to itself
    (supported/refuted/qualified/inconclusive); any mix is ``qualified``. If
    nothing is assessed the gate is ``unassessed``.
    """
    assessed = {v for v in member_verdicts if v and v != "unassessed"}
    if not assessed:
        return "unassessed"
    if assessed == {"supported"}:
        return "supported"
    if assessed == {"refuted"}:
        return "refuted"
    if assessed == {"inconclusive"}:
        return "inconclusive"
    return "qualified"  # a lone {"qualified"} or any mixed set reads as qualified


def _verdict_of(claims: dict, slug: str):
    c = claims.get("claim:" + slug)
    return c.get("verdict") if isinstance(c, dict) else None


def synthesize_gates(inv_spec: dict, claims: dict) -> list[dict]:
    """The synthesis spine: each plan gate with a rolled-up canonical verdict,
    its criterion, its member studies (with their verdicts), and its spine link."""
    out: list[dict] = []
    for g in inv_spec.get("plan_gates") or []:
        if not isinstance(g, dict):
            continue
        members = [s for s in (g.get("member_studies") or []) if isinstance(s, str)]
        mv = [{"slug": s, "verdict": _verdict_of(claims, s)} for s in members]
        out.append({
            "id": g.get("id"),
            "name": g.get("name"),
            "criterion": g.get("criterion"),
            "extends": g.get("extends"),
            "off_spine": bool(g.get("off_spine")),
            "verdict": gate_verdict([m["verdict"] for m in mv]),
            "members": mv,
            "pending": [s for s in (g.get("pending_studies") or []) if isinstance(s, str)],
        })
    return out


def _label(cid) -> str:
    return str(cid or "").replace("claim:", "")


def open_calls(inv_spec: dict, claims: dict, gaps: list) -> list[dict]:
    """The unified Decisions list: authored decisions + derived open items, each
    ``{kind, title, detail, action, refs}`` and traceable to its source."""
    calls: list[dict] = []

    ex = inv_spec.get("executive") or {}
    for d in ex.get("decisions_needed") or []:
        if isinstance(d, dict):
            calls.append({"kind": "decision", "title": d.get("question") or "",
                          "detail": d.get("context") or "", "action": "resolve", "refs": []})
        elif isinstance(d, str) and d.strip():
            calls.append({"kind": "decision", "title": d, "detail": "", "action": "resolve", "refs": []})

    for g in gaps or []:
        fu = g.get("followup") or {}
        calls.append({
            "kind": "narrative_gap",
            "title": "Unsupported transition: " + _label(g.get("from")) + " → " + _label(g.get("to")),
            "detail": g.get("missing_evidence") or "",
            "action": "seed " + (fu.get("id") or "a follow-up study"),
            "refs": [g.get("from"), g.get("to")],
        })

    for cid, c in (claims or {}).items():
        if not isinstance(c, dict) or c.get("arm_of"):
            continue
        if c.get("verdict") in ("unassessed", "inconclusive"):
            calls.append({"kind": "open_question", "title": c.get("proposition") or cid,
                          "detail": "Claim verdict is " + str(c.get("verdict")) + ".",
                          "action": "assess", "refs": [cid]})
        elif c.get("diverges"):
            calls.append({"kind": "divergence", "title": c.get("proposition") or cid,
                          "detail": "Authored verdict diverges from the coded roll-up.",
                          "action": "reconcile", "refs": [cid]})

    return calls
