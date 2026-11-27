"""Within-investigation evidence routing — the ``EvidenceStep`` seam (WP2).

A ``TestStep`` / ``ReportCardStep`` grades a run into a verdict. This routes that
verdict to the claims the study *references* (its ``knowledge_inputs``) as
**Evidence** records — evidence *about* a claim the study does not own — rather than
folding it only into the study's own verdict. That is the structural rewiring the
knowledge layer makes within an investigation.

Read-only: it produces records and writes nothing. The durable commit is WP4, at
the authority boundary (see ``docs/adr-0001-knowledge-authority.md``).
"""
from __future__ import annotations

import hashlib

from discovery_engine.projection.knowledge import canonical_verdict

#: verdict -> the E_evid edge polarity (``Evidence --polarity--> Claim``).
_POLARITY = {"supported": "supports", "refuted": "challenges", "qualified": "qualifies"}


def referenced_claims(study_spec: dict) -> list[str]:
    """The claim references a study declares under ``knowledge_inputs.claims`` — the
    claims it tests but does not own. Empty when it declares none."""
    ki = study_spec.get("knowledge_inputs") or {}
    return [c for c in (ki.get("claims") or []) if c]


def study_evidence(study_spec: dict, slug: str, provenance: dict | None = None) -> list[dict]:
    """Evidence records routing this study's verdict to each referenced claim.

    A study that references claims emits evidence *about* them; a legacy study that
    references none falls back to self-evidence about its own derived claim
    (``claim:<slug>``), so nothing is lost in migration. Read-only."""
    cv = canonical_verdict(study_spec)
    verdict = cv.get("verdict")
    polarity = _POLARITY.get(verdict, "bears-on")
    refs = referenced_claims(study_spec) or [f"claim:{slug}"]
    out: list[dict] = []
    for ref in refs:
        eid = "ev-" + hashlib.sha1(f"{ref}|{slug}".encode("utf-8")).hexdigest()[:10]
        out.append({
            "id": eid,
            "claim_ref": ref,
            "polarity": polarity,
            "from_study": slug,
            "verdict": verdict,
            "verdict_source": cv.get("verdict_source"),
            "provenance": provenance or {},
        })
    return out
