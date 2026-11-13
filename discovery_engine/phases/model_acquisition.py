"""ModelAcquisitionProposal — the typed contract for a *proposed* model/mechanism to acquire.

The engine does not invent mechanisms. It surfaces where one is *needed* (a
``model_revision`` candidate change ``{kind: mechanism, needs_author: true, question}``),
validates an agent-authored proposal against a strict contract, and records it as
``proposed`` with provenance. The mechanism itself is authored by an agent following
``skills/acquire_model/SKILL.md``; the engine owns the type and the gate.

A proposal is never built, promoted, or marked verified by this code. This module only types
and validates a proposal. Acceptance lives in
``discovery_engine.discover.actions.accept_model_acquisition_proposal``: an accepted proposal
whose mechanism is identifier-shaped is added to the investigation's ``required_mechanisms``,
and from there the capability-gap loop builds and runs it. Acceptance is still a decider
(human) step; there is no automatic accept for proposals yet. A prose (non-identifier)
mechanism is accepted but not fed to a capability gap.

Shape (``investigation.yaml::model_acquisition_proposals[]``)::

    - id: ma:ab12cd34ef56
      mechanism: "Distance-limited dispersal kernel for colonization."
      existing_model_ref: "composites/island_base"     # optional: model being extended
      change: {kind: mechanism, question: "what limits arrival?"}
      expected_behavioral_test:                         # what the acquired model must satisfy
        behavior_test: colonization_in_band
        pass_if: {low: 0.1, high: 0.3}
      assumptions: ["rescue effect negligible"]
      status: proposed                                  # proposed | accepted | declined
      provenance: {trigger: "needs_author", source: "...", by: "agent"}
      at: "2026-11-11T00:00:00+00:00"

All functions here are pure and AI-free.
"""
from __future__ import annotations

import datetime as _dt
import hashlib

PROPOSED = "proposed"
ACCEPTED = "accepted"
DECLINED = "declined"
_STATUSES = (PROPOSED, ACCEPTED, DECLINED)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _proposal_id(mechanism: str, trigger: str | None) -> str:
    h = hashlib.sha1(f"{(mechanism or '').strip()}|{trigger or ''}".encode()).hexdigest()[:12]
    return f"ma:{h}"


def _is_behavioral_test(t: object) -> bool:
    """A falsifiable test names a ``behavior_test`` and a ``pass_if`` band."""
    return (isinstance(t, dict) and bool(str(t.get("behavior_test") or "").strip())
            and bool(t.get("pass_if")))


def validate_model_acquisition_proposal(proposal: dict) -> dict:
    """Raise ``ValueError`` unless ``proposal`` satisfies the contract; else return it.

    Required: a ``mechanism`` (the proposed cause/capability), an
    ``expected_behavioral_test`` with ``behavior_test`` + ``pass_if`` (so the proposal is
    falsifiable), and ``provenance`` with a ``trigger``. This is the *honesty gate*."""
    if not isinstance(proposal, dict):
        raise ValueError("model acquisition proposal must be a mapping")
    if not str(proposal.get("mechanism") or "").strip():
        raise ValueError("model acquisition proposal needs a non-empty 'mechanism'")
    if not _is_behavioral_test(proposal.get("expected_behavioral_test")):
        raise ValueError("model acquisition proposal needs an 'expected_behavioral_test' "
                         "with behavior_test + pass_if")
    prov = proposal.get("provenance")
    if not isinstance(prov, dict) or not str(prov.get("trigger") or "").strip():
        raise ValueError("model acquisition proposal needs provenance with a 'trigger'")
    status = proposal.get("status", PROPOSED)
    if status not in _STATUSES:
        raise ValueError(f"status must be one of {_STATUSES}, got {status!r}")
    return proposal


def build_model_acquisition_proposal(mechanism: str, *, expected_behavioral_test: dict,
                                     existing_model_ref: str | None = None,
                                     change: dict | None = None,
                                     assumptions: list | None = None,
                                     provenance: dict | None = None, status: str = PROPOSED,
                                     at: str | None = None) -> dict:
    """Assemble + validate a ModelAcquisitionProposal. Pure."""
    prov = dict(provenance or {})
    prov.setdefault("trigger", "manual")
    prov.setdefault("by", "agent")
    proposal = {
        "id": _proposal_id(mechanism, prov.get("trigger")),
        "mechanism": str(mechanism).strip(),
        "existing_model_ref": existing_model_ref,
        "change": dict(change or {}),
        "expected_behavioral_test": expected_behavioral_test,
        "assumptions": list(assumptions or []),
        "status": status,
        "provenance": prov,
        "at": at or _now(),
    }
    return validate_model_acquisition_proposal(proposal)


def coerce_model_acquisition_proposal(proposal: dict) -> dict:
    """Validate an agent-authored proposal, then fill ``id`` (content-addressed),
    ``status`` and ``at`` if absent. Pure."""
    validate_model_acquisition_proposal(proposal)
    prov = proposal.get("provenance") or {}
    out = {**proposal}
    out.setdefault("assumptions", [])
    out.setdefault("status", PROPOSED)
    out.setdefault("at", _now())
    out["id"] = proposal.get("id") or _proposal_id(proposal.get("mechanism"), prov.get("trigger"))
    return out
