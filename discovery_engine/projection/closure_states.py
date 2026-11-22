"""R4 — explicit closure states (§5).

``engine.closure`` collapses "is the investigation settled?" into one boolean (no unmet criterion,
no narrative gap, no open challenge). §5 replaces that single bit with five explicit states, so a
reviewer sees *which* kind of done an investigation is — and the boolean remains a compatibility
view:

  - ``workflow_complete``       — no open operational items (criteria met, gaps filled).
  - ``evidence_sufficient``     — no claim rests on provisional-only evidence.
  - ``model_adequate``          — no claim fails its model-adequacy gate.
  - ``scientifically_resolved`` — no open challenge and no inadequate claim.
  - ``discovery_active``        — a discovery is in progress (a pending FindingCandidate).

Pure; derives from ``engine.closure``'s result plus a few refining signals the caller supplies.
Domain-neutral.
"""
from __future__ import annotations

CLOSURE_STATES = ("workflow_complete", "evidence_sufficient", "model_adequate",
                  "scientifically_resolved", "discovery_active")


def closure_states(closure_result, *, provisional_claims=None, inadequate_claims=None,
                   active_discoveries=None) -> dict:
    """Project ``engine.closure``'s dict into the five explicit §5 states.

    ``closure_result``: ``{closed, unaddressed_criteria, open_gaps, open_challenges, …}``.
    ``provisional_claims`` / ``inadequate_claims`` / ``active_discoveries``: the refining signals
    (claims backed only by provisional evidence; claims that fail the adequacy gate; discoveries in
    progress). Returns ``{states, reasons, closed}`` where ``closed`` is the compatibility boolean."""
    cr = closure_result or {}
    unaddressed = cr.get("unaddressed_criteria") or []
    gaps = cr.get("open_gaps", 0) or 0
    challenges = cr.get("open_challenges", 0) or 0
    provisional = list(provisional_claims or [])
    inadequate = list(inadequate_claims or [])
    active = list(active_discoveries or [])

    states = {
        "workflow_complete": not unaddressed and not gaps,
        "evidence_sufficient": not provisional,
        "model_adequate": not inadequate,
        "scientifically_resolved": not challenges and not inadequate,
        "discovery_active": bool(active),
    }
    reasons = {
        "workflow_complete": ("no open operational items" if states["workflow_complete"]
                              else f"{len(unaddressed)} unmet criteria, {gaps} open gap(s)"),
        "evidence_sufficient": ("all claims rest on verified evidence" if states["evidence_sufficient"]
                                else f"{len(provisional)} claim(s) rest on provisional-only evidence"),
        "model_adequate": ("no claim fails its adequacy gate" if states["model_adequate"]
                           else f"{len(inadequate)} claim(s) overreach the model (missing mechanism)"),
        "scientifically_resolved": ("no open challenge or inadequate claim"
                                    if states["scientifically_resolved"]
                                    else f"{challenges} open challenge(s)"
                                         + (f", {len(inadequate)} inadequate claim(s)" if inadequate else "")),
        "discovery_active": (f"{len(active)} discovery/ies in progress" if active
                             else "no discovery in progress"),
    }

    closed = cr.get("closed")
    if closed is None:
        closed = states["workflow_complete"] and not challenges
    return {"states": states, "reasons": reasons, "closed": bool(closed)}
