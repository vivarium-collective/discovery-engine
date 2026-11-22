"""Propose study-graph edges that are latent in the prose: a study whose claim / findings text
explicitly names ANOTHER member study — by slug or title — without declaring a relation to it very
likely *builds on* it. Pure.

The engine PROPOSES; it never auto-wires. A study edge (``relations`` / legacy ``parent_studies``)
is an authored assertion a human accepts via ``discover relate`` — proposing one carries the
triggering cross-reference as evidence, consistent with the rule that the engine never promotes a
proposal to a fact. Only EXPLICIT references are used (not authoring order, which is correlation),
and ``edge_integrity`` is respected: no ``builds-on`` is proposed onto a refuted parent.
"""
from __future__ import annotations

import re

from discovery_engine.projection.relations import study_relations


def _study_text(spec: dict) -> str:
    """The prose the engine already has for a study: its claim, question, title, and findings."""
    parts = [spec.get("claim") or "", spec.get("question") or "", spec.get("title") or ""]
    for f in spec.get("findings") or []:
        if isinstance(f, dict):
            parts += [f.get("statement") or "", f.get("summary") or ""]
    return " ".join(str(p) for p in parts).lower()


def _mentions(text: str, token: str) -> bool:
    """``token`` appears as a standalone reference, not as a substring of a longer slug
    (so ``grc-0`` does not match inside ``grc-05``)."""
    return re.search(rf"(?<![\w-]){re.escape(token.lower())}(?![\w-])", text) is not None


def propose_study_edges(studies: dict, *, verdicts: dict | None = None) -> list[dict]:
    """Edges latent in the prose, as proposals ``{study, parent, relation, evidence, command}``.
    ``verdicts``: ``{slug: verdict}`` for the edge_integrity guard. Pure; nothing is written."""
    studies = studies or {}
    verdicts = verdicts or {}
    titles = {slug: str((spec or {}).get("title") or "").strip().lower()
              for slug, spec in studies.items() if isinstance(spec, dict)}
    out: list[dict] = []
    for slug, spec in studies.items():
        if not isinstance(spec, dict):
            continue
        declared = {r.get("study") for r in study_relations(spec)}
        text = _study_text(spec)
        for parent in studies:
            if parent == slug or parent in declared:
                continue
            if str(verdicts.get(parent) or "").lower() == "refuted":
                continue   # edge_integrity: never propose builds-on onto a refuted parent
            by = None
            if _mentions(text, parent):
                by = f"slug '{parent}'"
            else:
                t = titles.get(parent) or ""
                if len(t) >= 8 and t in text:   # title mention (guard short / ambiguous titles)
                    by = f"title '{(studies[parent] or {}).get('title')}'"
            if by:
                out.append({
                    "study": slug, "parent": parent, "relation": "builds-on",
                    "evidence": f"{slug}'s claim/findings reference {parent} by {by}",
                    "command": f"discover relate {slug} {parent} --relation builds-on"})
    return sorted(out, key=lambda p: (p["study"], p["parent"]))
