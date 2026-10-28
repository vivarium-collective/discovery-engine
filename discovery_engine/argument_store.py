"""Derive the argument graph — first-class Claims + Claim→Claim epistemic edges.

This is the derive-first half of RFC-0005's narrative/argument layer. A `Claim` is
the epistemic assertion (proposition, scope, qualifiers, warrants, evidence,
rebuttals) carrying the canonical Verdict computed by
`knowledge_store.canonical_verdict` — the Progress (execution) and Verdict
(scientific) axes stay independent there, and this module never lets a progress
field set a verdict.

The argument graph (`edges`) links Claim→Claim with epistemic relations
(`support · challenge · prerequisite · synthesis`). Edges derive from the study's
``parent_relations`` (the scientific edge key: builds-on/tests/refutes/motivates/
alternative-to) — **never** from ``parent_studies`` (the organizational gate,
``condition: tests-passed``) or ``upstream_state_consumed`` (computational
dataflow). Those stay in the organizational/computational relation set, not the
argument graph. See RFC-0005 §4 and §10.3 for the derivation spec.

The projection is read-only: it writes nothing to disk. An authored half (the
investigation ``argument`` block) is merged over it by ``merge_argument``
(authored wins, derived fills, empty never clobbers).

Public API
----------
canonical_claim(study_spec, slug) -> dict
    One Claim record for a study.
derive_argument(inv_spec, studies_by_slug) -> {"claims": {...}, "edges": [...]}
    Claims for every member (or every arm) + the derived argument graph.
merge_argument(authored, derived) -> dict
    Section-wise merge; claims entry-wise (authored-wins), edges unioned + deduped.
"""
from __future__ import annotations

from typing import Any

from .knowledge_store import _EMPTY, _merge_entry, canonical_verdict
from .relations import epistemic_relations

# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

#: canonical scientific verdicts a Claim may carry
_CANON_VERDICTS = {"supported", "qualified", "inconclusive", "refuted", "unassessed"}

#: finding statuses that read as counter-evidence (feed a Claim's rebuttals)
_REBUTTING = {"refutes", "refuted", "refute", "challenges", "challenge", "disconfirms"}

#: arm.gate_status -> canonical verdict (fallback when an arm has no verdict)
_GATE_TO_VERDICT = {"passed": "supported", "failed": "refuted"}


# ---------------------------------------------------------------------------
# Claim (per study)
# ---------------------------------------------------------------------------

def canonical_claim(study_spec: dict, slug: str) -> dict:
    """Derive one Claim from a study spec (RFC-0005 §10.3).

    Every field names its source keys in fallback order; a missing proposition
    yields a stub, never fabricated text.
    """
    proposition, is_stub = _proposition(study_spec, slug)
    cv = canonical_verdict(study_spec)

    claim: dict[str, Any] = {
        "proposition": proposition,
        "proposition_stub": is_stub,
        "verdict": cv["verdict"],
        "verdict_source": cv["verdict_source"],
        "diverges": cv["diverges"],
        "scope": study_spec.get("primary_limitation"),
        "qualifiers": _qualifiers(study_spec),
        "hypothesis": study_spec.get("hypothesis"),
        "warrants": _warrants(study_spec),
        "evidence_refs": _evidence_refs(study_spec),
        "rebuttals": _rebuttals(study_spec),
        "derived": True,
    }
    return claim


def _proposition(spec: dict, slug: str) -> tuple[str, bool]:
    for key in ("claim", "title", "question"):
        v = spec.get(key)
        if isinstance(v, str) and v.strip():
            return v, False
    return f"⟨no claim authored for {slug}⟩", True


def _qualifiers(spec: dict) -> dict:
    out: dict[str, Any] = {}
    for key in ("confidence", "evidence_tier"):
        v = spec.get(key)
        if v not in _EMPTY:
            out[key] = v
    return out


def _warrants(spec: dict) -> dict:
    prereg = spec.get("preregistered") or {}
    thresholds = prereg.get("thresholds") if isinstance(prereg, dict) else None
    primary = []
    for t in spec.get("behavior_tests") or []:
        if isinstance(t, dict) and t.get("classification") == "primary":
            primary.append({k: t[k] for k in ("name", "pass_if", "band_basis", "cites")
                            if k in t})
    return {"thresholds": thresholds or {}, "primary_tests": primary}


def _evidence_refs(spec: dict) -> list[str]:
    refs: list[str] = []

    def add(v):
        if isinstance(v, str) and v.strip() and v not in refs:
            refs.append(v)

    for f in spec.get("findings") or []:
        if isinstance(f, dict) and f.get("id"):
            add(str(f["id"]))
    for r in spec.get("runs") or []:
        if isinstance(r, dict) and r.get("name"):
            add(str(r["name"]))
    for t in spec.get("behavior_tests") or []:
        if isinstance(t, dict) and t.get("name"):
            add(str(t["name"]))
    for v in spec.get("visualizations") or []:
        add(str(v.get("name")) if isinstance(v, dict) else str(v))
    add(spec.get("primary_output") if isinstance(spec.get("primary_output"), str) else None)
    return refs


def _rebuttals(spec: dict) -> list[str]:
    out: list[str] = []
    for f in spec.get("findings") or []:
        if isinstance(f, dict) and f.get("status") in _REBUTTING:
            stmt = f.get("statement") or f.get("id")
            if stmt and stmt not in out:
                out.append(str(stmt))
    for c in spec.get("proposed_controls") or []:
        if isinstance(c, str) and c not in out:
            out.append(c)
    return out


# ---------------------------------------------------------------------------
# Derive the argument graph for an investigation
# ---------------------------------------------------------------------------

def derive_argument(inv_spec: dict, studies_by_slug: dict[str, dict]) -> dict:
    """Project Claims + the argument graph from canonical membership.

    One Claim per member study (per ``arms[]`` entry when present, with a
    ``synthesis`` edge from each arm claim to the study-level synthesis claim).
    Edges derive from ``parent_relations`` only; ``parent_studies`` and
    ``upstream_state_consumed`` are organizational/computational, never edges here.
    """
    member_slugs = inv_spec.get("studies") or list(studies_by_slug.keys())

    claims: dict[str, dict] = {}
    edges: list[dict] = []

    # Pass 1: claims (so edge classification can read child verdicts).
    for slug in member_slugs:
        spec = studies_by_slug.get(slug)
        if spec is None:
            continue
        cid = f"claim:{slug}"
        claims[cid] = canonical_claim(spec, slug)
        for arm in spec.get("arms") or []:
            if not isinstance(arm, dict) or not arm.get("id"):
                continue
            acid = f"claim:{slug}:{arm['id']}"
            claims[acid] = _arm_claim(claims[cid], arm, cid)
            edges.append(_edge(acid, cid, "synthesis", "arm"))

    # Pass 2: epistemic edges from the unified relations (RFC-0006 Tier 2 — reads
    # the enriched parent_studies entries, falling back to legacy parent_relations).
    for slug in member_slugs:
        spec = studies_by_slug.get(slug)
        if spec is None:
            continue
        cid = f"claim:{slug}"
        child_verdict = claims.get(cid, {}).get("verdict")
        for rel in epistemic_relations(spec):
            parent, relation = rel.get("study"), rel.get("relation")
            if not parent or not relation:
                continue
            etype = _edge_type(relation, child_verdict)
            if etype is None:
                continue
            edges.append(_edge(f"claim:{parent}", cid, etype, relation))

    return {"claims": claims, "edges": edges}


def _arm_claim(parent_claim: dict, arm: dict, parent_cid: str) -> dict:
    proposition = arm.get("claim") or f"{parent_claim['proposition']} — arm {arm['id']}"
    rebuttals = []
    if arm.get("matched_null"):
        rebuttals.append(str(arm["matched_null"]))
    return {
        "proposition": proposition,
        "proposition_stub": not bool(arm.get("claim")),
        "verdict": _arm_verdict(arm),
        "scope": parent_claim.get("scope"),
        "qualifiers": {k: arm[k] for k in ("fidelity", "concept_role") if k in arm},
        "evidence_refs": [str(arm["composite"])] if arm.get("composite") else [],
        "rebuttals": rebuttals,
        "arm_of": parent_cid,
        "derived": True,
    }


def _arm_verdict(arm: dict) -> str:
    v = arm.get("verdict")
    if v in _CANON_VERDICTS:
        return v
    return _GATE_TO_VERDICT.get(arm.get("gate_status"), "unassessed")


def _edge_type(relation: str, child_verdict: str | None) -> str | None:
    if relation == "builds-on":
        return "prerequisite"
    if relation in ("refutes", "alternative-to"):
        return "challenge"
    if relation == "tests":
        return "support" if child_verdict == "supported" else "challenge"
    # motivates is a study-graph (E_sci) edge, not a claim edge; unknowns skip.
    return None


def _edge(src: str, dst: str, etype: str, relation: str) -> dict:
    return {"from": src, "to": dst, "type": etype, "relation": relation, "derived": True}


# ---------------------------------------------------------------------------
# Merge authored over derived
# ---------------------------------------------------------------------------

def merge_argument(authored: dict | None, derived: dict | None) -> dict:
    """Merge the authored argument half over the derived projection.

    Claims merge entry-wise (authored wins, derived fills, empty never clobbers).
    Edges are unioned and deduped by (from, to, type) — authored edges are added.
    """
    a_claims = (authored or {}).get("claims") or {}
    d_claims = (derived or {}).get("claims") or {}
    merged_claims = {k: dict(v) for k, v in d_claims.items()}
    for key, a_entry in a_claims.items():
        merged_claims[key] = _merge_entry(merged_claims.get(key), a_entry)

    seen: set[tuple] = set()
    merged_edges: list[dict] = []
    for e in list((derived or {}).get("edges") or []) + list((authored or {}).get("edges") or []):
        key = (e.get("from"), e.get("to"), e.get("type"))
        if key in seen:
            continue
        seen.add(key)
        merged_edges.append(e)

    return {"claims": merged_claims, "edges": merged_edges}
