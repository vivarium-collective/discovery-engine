"""Read-only legacy lift — the WP1 migration safety gate.

Decompose a study's existing claim / verdict / evidence fields into separate
**Claim / Evidence / Assessment** records (the shared object model the knowledge
layer moves toward), then re-project those records back into the argument-graph
claim shape. The **parity** between that re-projection and ``derive_argument``
proves the decomposition is lossless — so the migration can introduce records
without changing any scientific view.

Read-only and additive: this writes nothing and reuses the exact claim builders
``derive_argument`` uses (``canonical_claim`` / ``_arm_claim``), so a parity break
means a real field was dropped in the record split, not a divergent derivation.
"""
from __future__ import annotations

from discovery_engine.projection.argument import (
    _arm_claim,
    canonical_claim,
    derive_argument,
)
from discovery_engine.projection.claim_identity import claim_key

#: The claim fields the record decomposition must preserve round-trip.
PARITY_FIELDS = (
    "proposition", "proposition_stub", "verdict", "verdict_source", "diverges", "scope",
)


def _decompose(cid: str, claim: dict) -> tuple[dict, dict, list[dict]]:
    """Split one argument-graph claim dict into (Claim, Assessment, [Evidence])."""
    content = (
        None if claim.get("proposition_stub")
        else claim_key(claim.get("proposition"), claim.get("scope"))
    )
    claim_rec = {
        "id": cid,
        "content_key": content,
        "proposition": claim.get("proposition"),
        "proposition_stub": claim.get("proposition_stub"),
        "scope": claim.get("scope"),
        "qualifiers": claim.get("qualifiers"),
        "hypothesis": claim.get("hypothesis"),
    }
    assessment = {
        "claim_id": cid,
        "verdict": claim.get("verdict"),
        "verdict_source": claim.get("verdict_source"),
        "diverges": claim.get("diverges"),
    }
    evidence = [
        {"claim_id": cid, "ref": r, "polarity": "supports"}
        for r in (claim.get("evidence_refs") or [])
    ]
    evidence += [
        {"claim_id": cid, "ref": r, "polarity": "challenges"}
        for r in (claim.get("rebuttals") or [])
    ]
    return claim_rec, assessment, evidence


def lift_knowledge(inv_spec: dict, studies_by_slug: dict[str, dict]) -> dict:
    """All Claim / Evidence / Assessment records for one investigation (read-only).
    Covers top-level claims and arm claims, exactly as ``derive_argument`` does."""
    member_slugs = inv_spec.get("studies") or list(studies_by_slug.keys())
    claims: list[dict] = []
    assessments: list[dict] = []
    evidence: list[dict] = []
    for slug in member_slugs:
        spec = studies_by_slug.get(slug)
        if spec is None:
            continue
        cid = f"claim:{slug}"
        cc = canonical_claim(spec, slug)
        cr, a, e = _decompose(cid, cc)
        claims.append(cr)
        assessments.append(a)
        evidence.extend(e)
        for arm in (spec.get("arms") or []):
            if not isinstance(arm, dict) or not arm.get("id"):
                continue
            acid = f"{cid}:{arm['id']}"
            acr, aa, ae = _decompose(acid, _arm_claim(cc, arm, cid))
            claims.append(acr)
            assessments.append(aa)
            evidence.extend(ae)
    return {"claims": claims, "assessments": assessments, "evidence": evidence}


def project_claims_from_records(records: dict) -> dict:
    """Re-project the argument-graph claim shape from the lifted records — the
    inverse of :func:`lift_knowledge` for the claim fields."""
    asmt_by = {a["claim_id"]: a for a in (records.get("assessments") or [])}
    supports: dict[str, list[str]] = {}
    for e in (records.get("evidence") or []):
        if e.get("polarity") == "supports":
            supports.setdefault(e["claim_id"], []).append(e["ref"])
    out: dict[str, dict] = {}
    for cl in (records.get("claims") or []):
        cid = cl["id"]
        a = asmt_by.get(cid, {})
        out[cid] = {
            "proposition": cl.get("proposition"),
            "proposition_stub": cl.get("proposition_stub"),
            "verdict": a.get("verdict"),
            "verdict_source": a.get("verdict_source"),
            "diverges": a.get("diverges"),
            "scope": cl.get("scope"),
            "evidence_refs": supports.get(cid, []),
        }
    return out


def parity(inv_spec: dict, studies_by_slug: dict[str, dict]) -> list[dict]:
    """Fields where the re-projection from lifted records differs from
    ``derive_argument``. **Empty means lossless parity** — the WP1 gate."""
    reprojected = project_claims_from_records(lift_knowledge(inv_spec, studies_by_slug))
    original = (derive_argument(inv_spec, studies_by_slug).get("claims") or {})
    diffs: list[dict] = []
    for cid, oc in original.items():
        rc = reprojected.get(cid)
        if rc is None:
            diffs.append({"claim": cid, "field": "<missing from records>"})
            continue
        for f in PARITY_FIELDS:
            if oc.get(f) != rc.get(f):
                diffs.append({"claim": cid, "field": f, "original": oc.get(f), "lifted": rc.get(f)})
        if set(oc.get("evidence_refs") or []) != set(rc.get("evidence_refs") or []):
            diffs.append({"claim": cid, "field": "evidence_refs",
                          "original": oc.get("evidence_refs"), "lifted": rc.get("evidence_refs")})
    return diffs
