"""Workspace profile — a read-only audit of the knowledge/claim structure across a
whole workspace.

Surfaces what the knowledge-layer migration touches: the claim/verdict distribution,
the Concept projection, cross-investigation claim sharing, and the model/viz/composite
surface. The headline signal is the split of shared-claim candidates into

- **shared-membership** — the *same* study (one ``claim:<slug>``) is a member of N
  investigations, so N investigation-local claim nodes describe one study; and
- **independent** — *distinct* studies in different investigations assert the same
  proposition.

That distinction separates a real dedup (collapse N membership copies to one shared
identity) from the harder independent-sharing case the release objective targets.

Built on the argument + knowledge projections and the shared-claim audit; read-only.
The relation breakdown's unknown-dialect flag lights up when the relation registry is present.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from discovery_engine.investigation.import_ import investigations_dir
from discovery_engine.projection.argument import derive_argument
from discovery_engine.projection.knowledge import derive_knowledge
from discovery_engine.projection.shared_claims import (
    _load_yaml,
    _studies_by_slug,
    shared_claim_candidates,
)

_BOILERPLATE = re.compile(r"^(not yet run|this study exists|placeholder|tbd|todo)", re.I)


def _relations_of(study: dict) -> list[str]:
    vals: list[str] = []
    for key in ("parent_studies", "parent_relations"):
        for e in (study.get(key) or []):
            if isinstance(e, dict) and e.get("relation"):
                vals.append(e["relation"])
    for e in ((study.get("pipeline_gate") or {}).get("prerequisites") or []):
        if isinstance(e, dict) and e.get("relation"):
            vals.append(e["relation"])
    return vals


def _genuine(c: dict) -> bool:
    return (not _BOILERPLATE.match((c["proposition"] or "").strip())
            and any(o.get("verdict") in ("supported", "refuted", "qualified")
                    for o in c["occurrences"]))


def profile_workspace(ws_root: Path | str) -> dict:
    """Profile every investigation in a workspace. Returns a plain dict."""
    ws = Path(ws_root)
    inv_root = investigations_dir(ws)
    prof: dict = {
        "workspace": ws.name,
        "investigations": 0, "studies": 0,
        "claims": 0, "stub_claims": 0, "concepts": 0,
        "verdicts": Counter(),
        "models": 0, "viz": 0,
        "studies_with_composite": 0, "studies_with_viz": 0,
        "relations": Counter(), "relation_unknown": [],
    }
    graphs: dict[str, dict] = {}
    if inv_root.is_dir():
        for inv_dir in sorted(inv_root.iterdir()):
            iy = inv_dir / "investigation.yaml"
            if not iy.is_file():
                continue
            spec = _load_yaml(iy)
            prof["investigations"] += 1
            prof["models"] += len(spec.get("model_versions") or [])
            prof["viz"] += len(spec.get("viz_proposals") or spec.get("visualizations") or [])
            studies = _studies_by_slug(inv_dir)
            prof["studies"] += len(studies)
            for s in studies.values():
                base = s.get("baseline") or []
                if any(isinstance(b, dict) and (b.get("composite") or b.get("process") or b.get("step"))
                       for b in base):
                    prof["studies_with_composite"] += 1
                if s.get("visualizations") or s.get("viz") or s.get("viz_specs"):
                    prof["studies_with_viz"] += 1
                for r in _relations_of(s):
                    prof["relations"][r] += 1
            g = derive_argument(spec, studies)
            graphs[inv_dir.name] = g
            prof["concepts"] += len(derive_knowledge(spec, studies).get("concepts") or {})
            for c in (g.get("claims") or {}).values():
                if c.get("proposition_stub"):
                    prof["stub_claims"] += 1
                else:
                    prof["claims"] += 1
                    prof["verdicts"][c.get("verdict") or "unassessed"] += 1

    cands = shared_claim_candidates(graphs)
    membership = [c for c in cands if len({o["claim_id"] for o in c["occurrences"]}) == 1]
    independent = [c for c in cands if len({o["claim_id"] for o in c["occurrences"]}) >= 2]
    prof["shared_claims"] = {
        "total": len(cands),
        "membership": len(membership),      # same study referenced by N investigations
        "independent": len(independent),    # distinct studies, same proposition
        "genuine": sum(1 for c in cands if _genuine(c)),
    }

    try:  # optional — flags brand-new relation dialects when the registry is present
        from discovery_engine.projection import relation_registry as rr
        prof["relation_unknown"] = rr.unknown_relations(list(prof["relations"]))
    except Exception:
        pass

    prof["verdicts"] = dict(prof["verdicts"])
    prof["relations"] = dict(prof["relations"].most_common())
    return prof


def render_profile(prof: dict) -> str:
    sc = prof.get("shared_claims", {})
    lines = [
        f"workspace: {prof['workspace']}",
        f"  investigations {prof['investigations']}   studies {prof['studies']}"
        f"   composites {prof['studies_with_composite']}   with-viz {prof['studies_with_viz']}",
        f"  claims {prof['claims']} (+{prof['stub_claims']} stub)   concepts {prof['concepts']}"
        f"   models {prof['models']}   investigation-viz {prof['viz']}",
        f"  verdicts: {prof['verdicts']}",
        f"  relations used: {prof['relations'] or 'none declared'}"
        + (f"   UNKNOWN dialects: {prof['relation_unknown']}" if prof['relation_unknown'] else ""),
        f"  shared-claim candidates: {sc.get('total', 0)}"
        f"  (membership {sc.get('membership', 0)} / independent {sc.get('independent', 0)}"
        f" / genuine {sc.get('genuine', 0)})",
    ]
    return "\n".join(lines)
