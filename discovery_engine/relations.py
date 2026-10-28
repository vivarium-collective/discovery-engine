"""Unified typed relations for a study (RFC-0006 Tier 2).

Four keys named the same parent studies with different facets — `parent_studies`
(organizational gate: condition), `parent_relations` (epistemic: relation),
`builds_on` (lineage), `upstream_state_consumed` (computational: provides). This
collapses them into one list of `{study, relation?, condition?, rationale?,
provides?}` entries, merged by study.

Derive-first / back-compat: an enriched `parent_studies` entry (one that already
carries `relation`/`provides`) wins; the legacy keys are merged in only where the
enriched entry is silent, so readers work before *and* after the data migration.
"""
from __future__ import annotations

#: the scientific (epistemic) edge vocabulary — what the argument graph uses
_EPISTEMIC = {"builds-on", "tests", "refutes", "motivates", "alternative-to"}

_FACETS = ("condition", "relation", "rationale", "provides", "artifact")


def study_relations(spec: dict) -> list[dict]:
    """One unified relations list for a study, merged by parent study slug."""
    out: dict[str, dict] = {}

    def ent(slug: str) -> dict:
        return out.setdefault(slug, {"study": slug})

    # 0. pipeline_gate.prerequisites — the CANONICAL dependency field (Section 2).
    #    Wins over the legacy keys below (RFC-0006 Tier 2b).
    pg = spec.get("pipeline_gate") or {}
    for e in (pg.get("prerequisites") if isinstance(pg, dict) else None) or []:
        if isinstance(e, str) and e:
            ent(e)
        elif isinstance(e, dict) and e.get("study"):
            d = ent(e["study"])
            for k in _FACETS:
                if e.get(k) is not None:
                    d.setdefault(k, e[k])
            if e.get("outputs_used") is not None:      # pipeline_gate's name for provides
                d.setdefault("provides", e["outputs_used"])

    # 1. parent_studies — the legacy backbone (may carry relation/provides).
    for e in spec.get("parent_studies") or []:
        if isinstance(e, dict) and e.get("study"):
            d = ent(e["study"])
            for k in _FACETS:
                if e.get(k) is not None:
                    d.setdefault(k, e[k])

    # 2. parent_relations (epistemic) — back-compat.
    for e in spec.get("parent_relations") or []:
        if isinstance(e, dict) and e.get("study"):
            d = ent(e["study"])
            if e.get("relation") is not None:
                d.setdefault("relation", e["relation"])
            if e.get("rationale") is not None:
                d.setdefault("rationale", e["rationale"])

    # 3. builds_on (lineage) — back-compat.
    for s in spec.get("builds_on") or []:
        if isinstance(s, str) and s:
            ent(s).setdefault("relation", "builds-on")

    # 4. upstream_state_consumed (computational) — back-compat.
    for e in spec.get("upstream_state_consumed") or []:
        if isinstance(e, dict) and e.get("from"):
            d = ent(e["from"])
            if e.get("provides") is not None:
                d.setdefault("provides", e["provides"])

    return list(out.values())


def epistemic_relations(spec: dict) -> list[dict]:
    """Only the relations whose `relation` is a scientific edge (for the argument
    graph): builds-on / tests / refutes / motivates / alternative-to."""
    return [r for r in study_relations(spec) if r.get("relation") in _EPISTEMIC]
