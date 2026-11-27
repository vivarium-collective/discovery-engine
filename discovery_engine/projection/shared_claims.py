"""Shared-claim candidate audit — read-only.

Groups claims across a workspace's investigations by their content-addressed key
(:func:`claim_identity.claim_key`) and surfaces propositions asserted in more than
one investigation. These are **candidates** for cross-investigation sharing — the
capability the knowledge-layer plan makes first-class. The audit answers, from real
data, whether investigations already assert the same claim, and hands you the first
concrete pair for the flagship demo. It mutates nothing and asserts nothing; a human
confirms a share.
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path

from discovery_engine.projection.argument import derive_argument
from discovery_engine.projection.claim_identity import claim_key


def shared_claim_candidates(by_investigation: dict[str, dict]) -> list[dict]:
    """``by_investigation``: ``{inv_slug: argument_graph}``. Returns candidate groups,
    each a proposition asserted in **>= 2 distinct investigations**, most-shared
    first. Each group:
    ``{content_key, proposition, n_investigations, occurrences:[{investigation,
    claim_id, proposition, scope, verdict}]}``. Stub (text-less) claims are skipped."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for inv, graph in by_investigation.items():
        for cid, claim in ((graph or {}).get("claims") or {}).items():
            if claim.get("proposition_stub"):
                continue
            key = claim_key(claim.get("proposition"), claim.get("scope"))
            if not key:
                continue
            groups[key].append(
                {
                    "investigation": inv,
                    "claim_id": cid,
                    "proposition": claim.get("proposition"),
                    "scope": claim.get("scope"),
                    "verdict": claim.get("verdict"),
                }
            )
    out: list[dict] = []
    for key, occ in groups.items():
        investigations = {o["investigation"] for o in occ}
        if len(investigations) >= 2:
            out.append(
                {
                    "content_key": key,
                    "proposition": occ[0]["proposition"],
                    "n_investigations": len(investigations),
                    "occurrences": sorted(occ, key=lambda o: (o["investigation"], o["claim_id"])),
                }
            )
    out.sort(key=lambda g: (-g["n_investigations"], g["content_key"]))
    return out


# --- runnable over a real workspace ----------------------------------------

def _load_yaml(path: Path) -> dict:
    import yaml

    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}


def _studies_by_slug(inv_dir: Path) -> dict[str, dict]:
    """Load the member studies of one investigation directory, layout-tolerant:
    nested ``<inv>/studies/<slug>/study.yaml`` first, then a sibling flat
    ``studies/<slug>/study.yaml``."""
    out: dict[str, dict] = {}
    nested = inv_dir / "studies"
    if nested.is_dir():
        for sd in sorted(nested.iterdir()):
            sf = sd / "study.yaml"
            if sf.is_file():
                out[sd.name] = _load_yaml(sf)
    # Flat studies: load ONLY this investigation's declared members, not every flat
    # study in the workspace. Loading all of them makes every investigation see all
    # flat studies, which inflates per-investigation counts N-fold. Use the member
    # key derive_argument uses (`studies`), falling back to `members`.
    flat = inv_dir.parent.parent / "studies"
    if flat.is_dir():
        spec = _load_yaml(inv_dir / "investigation.yaml")
        for entry in (spec.get("studies") or spec.get("members") or []):
            slug = (entry.get("study") or entry.get("slug")) if isinstance(entry, dict) else entry
            if slug and str(slug) not in out:
                sf = flat / str(slug) / "study.yaml"
                if sf.is_file():
                    out[str(slug)] = _load_yaml(sf)
    return out


def arguments_by_investigation(ws_root: Path | str) -> dict[str, dict]:
    """Derive one argument graph per investigation present in a workspace."""
    from discovery_engine.investigation.import_ import investigations_dir

    ws_root = Path(ws_root)
    inv_root = investigations_dir(ws_root)
    graphs: dict[str, dict] = {}
    if not inv_root.is_dir():
        return graphs
    for inv_dir in sorted(inv_root.iterdir()):
        inv_yaml = inv_dir / "investigation.yaml"
        if not inv_yaml.is_file():
            continue
        inv_spec = _load_yaml(inv_yaml)
        studies = _studies_by_slug(inv_dir)
        graphs[inv_dir.name] = derive_argument(inv_spec, studies)
    return graphs


def audit_workspace(ws_root: Path | str) -> dict:
    """Load every investigation in a workspace and run :func:`shared_claim_candidates`.
    Returns ``{investigations:[slugs], n_claims, candidates:[...]}``."""
    graphs = arguments_by_investigation(ws_root)
    n_claims = sum(
        1
        for g in graphs.values()
        for c in (g.get("claims") or {}).values()
        if not c.get("proposition_stub")
    )
    return {
        "investigations": sorted(graphs),
        "n_claims": n_claims,
        "candidates": shared_claim_candidates(graphs),
    }
