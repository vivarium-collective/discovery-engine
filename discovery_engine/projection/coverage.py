"""Proactive coverage-gap frontier (AI-drivable cycle, issue #2 Phase 1).

The rest of the frontier is REACTIVE — it surfaces anomalies, unassessed claims,
authored decisions, capability gaps. Nothing proposes a *new* fundamental
question the investigation has not yet asked. This module adds the deterministic
half of that: from a workspace-configured set of scientific dimensions
(``inv_spec["discovery"]["coverage_dimensions"]``), it emits one frontier item per
dimension that **no member study or derived claim covers yet**.

Boundary (honesty): the engine detects the GAP deterministically (keyword
coverage over the investigation's framing + derived claims). It never authors the
question or hypothesis itself — a template generator would emit filler. The actual
question/hypothesis is authored by an agent downstream (Phase 2: the
``propose_hypothesis`` routing). With no configured dimensions this returns ``[]``
— the engine does not invent a domain's science; the workspace declares the
dimensions its domain should cover.
"""
from __future__ import annotations

import re


def _corpus(cycle_result: dict, inv_spec: dict) -> str:
    """The lowercased text a dimension's keywords are matched against: the
    investigation's authored framing, its authored hypotheses, and the derived
    claim statements."""
    inv = inv_spec if isinstance(inv_spec, dict) else {}
    parts: list[str] = []
    for k in ("question", "hypothesis", "lead", "biological_story"):
        v = inv.get(k)
        if isinstance(v, str):
            parts.append(v)
    sa = inv.get("scientific_argument")
    if isinstance(sa, dict):
        parts.append(str(sa.get("main_claim") or ""))
        for key in ("evidence_for", "evidence_against", "caveats"):
            parts += [str(x) for x in (sa.get(key) or []) if x]
        # Consolidated claim ledger: `scientific_argument.claims` (replaces the
        # top-level `key_claims`). Each entry is {id, statement, cites, ...}.
        for c in (sa.get("claims") or []):
            parts.append(str(c.get("statement") if isinstance(c, dict) else c))
    parts += [str(x) for x in (inv.get("key_claims") or []) if x]  # legacy
    for ag in (inv.get("at_a_glance") or []):
        if isinstance(ag, dict) and ag.get("role"):
            parts.append(str(ag["role"]))
    # Authored hypotheses (the `hypotheses:` block) count as coverage: a dimension
    # ASKED as a hypothesis — its statement + mechanism — has been asked even before a
    # study derives the claim that names it, so it must not re-fire as an "unasked" gap.
    for h in (inv.get("hypotheses") or []):
        if isinstance(h, dict):
            for key in ("statement", "mechanism"):
                if h.get(key):
                    parts.append(str(h[key]))
    claims = ((cycle_result or {}).get("argument") or {}).get("claims") or {}
    if isinstance(claims, dict):
        for c in claims.values():
            if isinstance(c, dict):
                for key in ("statement", "claim", "summary"):
                    if c.get(key):
                        parts.append(str(c[key]))
            elif isinstance(c, str):
                parts.append(c)
    return " \n ".join(p for p in parts if p).lower()


def _slug(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(name).strip().lower()).strip("-")
    return s or "dimension"


def coverage_gap_items(cycle_result: dict, inv_spec: dict) -> list[tuple[str, str]]:
    """``[(target, rationale)]`` — one per configured dimension no study covers.

    A dimension under ``inv_spec["discovery"]["coverage_dimensions"]`` is
    ``{name, keywords?: [...], question?}``. It is COVERED when any of its keywords
    (case-insensitive substring; the ``name`` itself when no keywords are given)
    appears in :func:`_corpus`; otherwise a ``coverage:<slug>`` item is raised with
    the dimension's ``question`` (or a default) as its rationale. Deterministic and
    idempotent; returns ``[]`` when no dimensions are configured."""
    inv = inv_spec if isinstance(inv_spec, dict) else {}
    dims = (inv.get("discovery") or {}).get("coverage_dimensions") or []
    if not isinstance(dims, list) or not dims:
        return []
    corpus = _corpus(cycle_result, inv_spec)
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for d in dims:
        if not isinstance(d, dict):
            continue
        name = str(d.get("name") or "").strip()
        if not name:
            continue
        probes = [str(k).strip().lower() for k in (d.get("keywords") or []) if str(k).strip()] \
            or [name.lower()]
        if any(p in corpus for p in probes):
            continue
        target = f"coverage:{_slug(name)}"
        if target in seen:
            continue
        seen.add(target)
        q = str(d.get("question") or "").strip()
        out.append((target, q or f"no study probes '{name}' yet — a fundamental dimension not asked"))
    return out
