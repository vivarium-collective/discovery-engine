"""Narrate + Audit — the ordered projection of the argument graph and its gap
audit (RFC-0005 §5, §7.3).

A Narrative is an *ordered presentation derived from the argument graph*, never a
requirement that the graph be linear. `narrate` orders the study-level Claims by a
deterministic topological pass over the `prerequisite` and `support` edges (ties
broken by claim id), so the same evidence always regenerates the same reading
(the determinism invariant, RFC-0005 §8). Arm claims are evidence/detail, not
narrative units.

`audit` is the investigate<->narrate loop-closer: an adjacent transition in the
reading with no `support`/`prerequisite` path between the two claims is an
**unsupported transition**; each one names the missing evidence and a candidate
follow-up study (the `/viva-study propose-followup` shape), rather than silently
inventing the missing link.

Public API
----------
narrate(argument_graph, question=None, audience=None) -> dict
audit(narrative, argument_graph) -> list[dict]
"""
from __future__ import annotations


#: ordering/support edge classes (challenge and synthesis do not order the reading)
_SUPPORTING = ("prerequisite", "support")

#: verdict strength for escalation detection (monotonic; refuted is a settled weak)
_STRENGTH = {"unassessed": 0, "refuted": 1, "inconclusive": 1, "qualified": 2, "supported": 3}


# ---------------------------------------------------------------------------
# narrate
# ---------------------------------------------------------------------------

def narrate(argument_graph: dict, question: str | None = None,
            audience: str | None = None) -> dict:
    """Project an ordered Narrative from the argument graph.

    Units are the study-level claims (those without ``arm_of``), ordered
    topologically over prerequisite+support, ties by id. Each unit preserves its
    links to source evidence.
    """
    claims: dict[str, dict] = argument_graph.get("claims") or {}
    edges: list[dict] = argument_graph.get("edges") or []

    unit_ids = [cid for cid, c in claims.items() if not c.get("arm_of")]
    unit_set = set(unit_ids)
    order_edges = [(e["from"], e["to"]) for e in edges
                   if e.get("type") in _SUPPORTING
                   and e.get("from") in unit_set and e.get("to") in unit_set]
    ordered = _toposort(unit_ids, order_edges)

    units = [{
        "claim_id": cid,
        "proposition": claims[cid].get("proposition"),
        "verdict": claims[cid].get("verdict"),
        "evidence_refs": claims[cid].get("evidence_refs", []),
        "order": i,
    } for i, cid in enumerate(ordered)]

    limitations: list[str] = []
    for cid in ordered:
        for r in claims[cid].get("rebuttals") or []:
            if r not in limitations:
                limitations.append(r)

    open_questions = [
        {"claim_id": cid, "proposition": claims[cid].get("proposition"),
         "verdict": claims[cid].get("verdict")}
        for cid in ordered
        if claims[cid].get("verdict") in ("unassessed", "inconclusive")
    ]

    return {
        "question": question,
        "audience": audience,
        "units": units,
        "limitations": limitations,
        "open_questions": open_questions,
    }


def _toposort(nodes: list[str], edges: list[tuple[str, str]]) -> list[str]:
    """Kahn's algorithm with a deterministic (sorted-id) tie-break; a cycle's
    remaining nodes are appended in id order so the result is always total."""
    nodes = sorted(set(nodes))
    succ: dict[str, set] = {n: set() for n in nodes}
    indeg: dict[str, int] = {n: 0 for n in nodes}
    for a, b in edges:
        if a in succ and b in indeg and b not in succ[a]:
            succ[a].add(b)
            indeg[b] += 1

    ready = sorted(n for n in nodes if indeg[n] == 0)
    out: list[str] = []
    while ready:
        n = ready.pop(0)
        out.append(n)
        for m in sorted(succ[n]):
            indeg[m] -= 1
            if indeg[m] == 0:
                ready.append(m)
        ready.sort()

    for n in nodes:  # cycle fallback
        if n not in out:
            out.append(n)
    return out


# ---------------------------------------------------------------------------
# audit
# ---------------------------------------------------------------------------

def audit(narrative: dict, argument_graph: dict) -> list[dict]:
    """Flag unsupported transitions the reading actually ASSERTS.

    Units are placed in a topological *total* order, so two consecutive units are a
    transition the reading asserts only when a declared edge connects them. Two
    independent claims that merely land adjacent in the linearization are NOT a
    transition — flagging them would demand a bridging study for a link the argument
    never claimed (the over-reporting this detector used to produce on a wide DAG).

    So a gap requires a declared dependency between the pair that is:
      • not justified — no support/prerequisite path connects them (either direction); and
      • not a `challenge` — a challenge edge is tracked on the open-challenge axis
        (an unadjudicated alternative/refutation), not re-raised as a narrative gap.
    What remains is a genuine unsupported transition: a declared dependency the reading
    leans on without a justifying link.
    """
    claims: dict[str, dict] = argument_graph.get("claims") or {}
    edges: list[dict] = argument_graph.get("edges") or []

    support: dict[str, set] = {}
    linked: set = set()       # frozenset{a, b}: a declared edge connects the pair (any type)
    challenged: set = set()   # frozenset{a, b}: the pair is joined by a challenge edge
    for e in edges:
        f, t, typ = e.get("from"), e.get("to"), e.get("type")
        if not f or not t:
            continue
        if typ in _SUPPORTING:
            support.setdefault(f, set()).add(t)
        pair = frozenset((f, t))
        linked.add(pair)
        if typ == "challenge":
            challenged.add(pair)

    units = narrative.get("units") or []
    gaps: list[dict] = []
    for u1, u2 in zip(units, units[1:]):
        a, b = u1["claim_id"], u2["claim_id"]
        pair = frozenset((a, b))
        if pair not in linked:                 # independent adjacency — not an asserted transition
            continue
        if pair in challenged:                 # tracked on the open-challenge axis, not a narrative gap
            continue
        if _reachable(a, b, support) or _reachable(b, a, support):  # a supporting path justifies it
            continue
        gaps.append(_gap(u1, u2, claims))
    return gaps


def _reachable(src: str, dst: str, adj: dict[str, set]) -> bool:
    """True if dst is reachable from src along supporting edges (direct or
    transitive). A node is never considered to reach itself."""
    seen: set[str] = set()
    stack = list(adj.get(src, ()))
    while stack:
        n = stack.pop()
        if n == dst:
            return True
        if n not in seen:
            seen.add(n)
            stack.extend(adj.get(n, ()))
    return False


def _gap(u1: dict, u2: dict, claims: dict) -> dict:
    a, b = u1["claim_id"], u2["claim_id"]
    a_prop = u1.get("proposition") or a
    b_prop = u2.get("proposition") or b
    b_slug = b.split(":", 1)[1].split(":", 1)[0] if ":" in b else b
    escalation = _STRENGTH.get(u2.get("verdict"), 0) > _STRENGTH.get(u1.get("verdict"), 0)

    short = (b_prop[:60] + "…") if len(b_prop) > 60 else b_prop
    return {
        "from": a,
        "to": b,
        "verdict_escalation": escalation,
        "reason": ("unsupported transition: verdict escalates with no supporting link"
                   if escalation else
                   "unsupported transition: no support/prerequisite path"),
        "missing_evidence": f"evidence linking “{a_prop}” to “{b_prop}”",
        "followup": {
            "id": f"discriminate-{b_slug}",
            "title": f"Discriminate: {short}",
            "motivation": (f"The narrative moves from “{a_prop}” to "
                           f"“{b_prop}” without a supporting or prerequisite "
                           f"link. Design a study that supplies the missing evidence "
                           f"or rules out the leap."),
            "parent_study": b_slug,
        },
    }
