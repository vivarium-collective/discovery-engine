"""HypothesisProposal — the typed contract for a *proposed* competing hypothesis (DE-06).

The engine does not invent hypotheses. It (a) surfaces where one is *needed* — a measured
surprise the current hypotheses did not anticipate — as a typed trigger, (b) validates an
agent-authored proposal against a strict contract, and (c) records it as ``proposed`` with
provenance. The creative content (mechanism, predictions, falsifiers) is authored by an
agent following ``skills/hypothesize/SKILL.md``; the engine owns the type and the gate.

This keeps the guiding principle intact: agents propose science, deterministic code
validates it. A proposal is never scored as evidence and never marked verified; only an
*accepted* proposal is promoted into the authored ``hypotheses[]`` the evaluator reads.

Shapes
------
HypothesisProposal (``investigation.yaml::hypothesis_proposals[]``)::

    - id: hp:ab12cd34ef56
      statement: "Colonization rate falls with isolation, not area."
      mechanism: "Dispersal is distance-limited; area sets extinction, not arrival."
      assumptions: ["propagule supply is distance-limited", "rescue effect negligible"]
      predictions:
        - observable: colonization_rate
          expected: {op: "<", value: 0.2}       # same band grammar as authored hypotheses
          falsifier: "colonization_rate >= 0.2 on the far island"
      alternatives: ["area-controlled colonization (null)"]
      status: proposed                           # proposed | accepted | declined
      provenance: {trigger: "unexplained_anomaly", source: "study-3#far-island", by: "agent"}
      at: "2026-11-11T00:00:00+00:00"

All functions here are pure and AI-free.
"""
from __future__ import annotations

import datetime as _dt
import hashlib

from discovery_engine.phases.hypotheses import anomalies

PROPOSED = "proposed"
ACCEPTED = "accepted"
DECLINED = "declined"
_STATUSES = (PROPOSED, ACCEPTED, DECLINED)


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _proposal_id(statement: str, trigger: str | None) -> str:
    h = hashlib.sha1(f"{(statement or '').strip()}|{trigger or ''}".encode()).hexdigest()[:12]
    return f"hp:{h}"


def _is_prediction(p: object) -> bool:
    """A prediction needs an ``observable``, an ``expected`` band, and a ``falsifier``
    (what result would overturn it). The band grammar matches the authored
    ``hypotheses[].predictions`` the evaluator already reads."""
    return (isinstance(p, dict) and bool(str(p.get("observable") or "").strip())
            and isinstance(p.get("expected"), dict) and bool(p.get("expected"))
            and bool(str(p.get("falsifier") or "").strip()))


def validate_hypothesis_proposal(proposal: dict) -> dict:
    """Raise ``ValueError`` unless ``proposal`` satisfies the contract; else return it.

    Required: a ``statement``, a ``mechanism`` (the proposed cause, not just a restatement),
    at least one ``prediction`` with an ``observable`` + ``expected`` band + ``falsifier``,
    and a ``provenance`` naming the trigger/source. This is the *honesty gate*: a proposal
    with no falsifiable prediction, or no mechanism, is not a hypothesis."""
    if not isinstance(proposal, dict):
        raise ValueError("hypothesis proposal must be a mapping")
    if not str(proposal.get("statement") or "").strip():
        raise ValueError("hypothesis proposal needs a non-empty 'statement'")
    if not str(proposal.get("mechanism") or "").strip():
        raise ValueError("hypothesis proposal needs a 'mechanism' (the proposed cause)")
    preds = proposal.get("predictions")
    if not isinstance(preds, list) or not any(_is_prediction(p) for p in preds):
        raise ValueError("hypothesis proposal needs >=1 prediction with "
                         "observable + expected band + falsifier")
    bad = [i for i, p in enumerate(preds) if not _is_prediction(p)]
    if bad:
        raise ValueError(f"predictions at {bad} are missing observable/expected/falsifier")
    prov = proposal.get("provenance")
    if not isinstance(prov, dict) or not str(prov.get("trigger") or "").strip():
        raise ValueError("hypothesis proposal needs provenance with a 'trigger'")
    status = proposal.get("status", PROPOSED)
    if status not in _STATUSES:
        raise ValueError(f"status must be one of {_STATUSES}, got {status!r}")
    return proposal


def build_hypothesis_proposal(statement: str, *, mechanism: str, predictions: list,
                              assumptions: list | None = None, alternatives: list | None = None,
                              provenance: dict | None = None, status: str = PROPOSED,
                              at: str | None = None) -> dict:
    """Assemble + validate a HypothesisProposal. Pure. ``provenance`` must carry a
    ``trigger`` (what surfaced the need); ``source`` / ``by`` are recommended."""
    prov = dict(provenance or {})
    prov.setdefault("trigger", "manual")
    prov.setdefault("by", "agent")
    proposal = {
        "id": _proposal_id(statement, prov.get("trigger")),
        "statement": str(statement).strip(),
        "mechanism": str(mechanism).strip(),
        "assumptions": list(assumptions or []),
        "predictions": list(predictions or []),
        "alternatives": list(alternatives or []),
        "status": status,
        "provenance": prov,
        "at": at or _now(),
    }
    return validate_hypothesis_proposal(proposal)


def coerce_hypothesis_proposal(proposal: dict) -> dict:
    """Normalize a raw (agent-authored) proposal into a valid HypothesisProposal: validate
    the authored content, then fill ``id`` (content-addressed), ``status`` and ``at`` if
    absent. Pure. The agent authors statement/mechanism/predictions/provenance; the engine
    assigns the identity and defaults — the agent never has to supply an id."""
    validate_hypothesis_proposal(proposal)
    prov = proposal.get("provenance") or {}
    out = {**proposal}
    out.setdefault("assumptions", [])
    out.setdefault("alternatives", [])
    out.setdefault("status", PROPOSED)
    out.setdefault("at", _now())
    out["id"] = proposal.get("id") or _proposal_id(proposal.get("statement"), prov.get("trigger"))
    return out


def hypothesis_triggers(inv_spec: dict, study_specs: list[dict]) -> list[dict]:
    """Where a new hypothesis is *needed*: measured surprises none of the live hypotheses
    predicted. Pure wrapper over :func:`anomalies` that frames each as a typed trigger
    ``{id, kind, study, observable, observed, note}`` an agent can answer with a proposal.

    AI-free: the engine points at the gap; the agent authors the explanation."""
    out: list[dict] = []
    for a in anomalies(inv_spec, study_specs):
        if not isinstance(a, dict):
            continue
        key = f"{a.get('study')}#{a.get('observable')}"
        out.append({
            "id": f"ht:{hashlib.sha1(key.encode()).hexdigest()[:10]}",
            "kind": "unexplained_anomaly",
            "study": a.get("study"),
            "observable": a.get("observable"),
            "observed": a.get("observed"),
            "note": a.get("note") or a.get("proposed_question"),
        })
    return out


def promote_to_hypothesis(proposal: dict) -> dict:
    """The authored ``hypotheses[]`` entry an *accepted* proposal becomes. Pure.

    Strips the proposal-only fields (mechanism/assumptions/alternatives/provenance stay as
    context) and hands the evaluator the ``{id, statement, predictions, status: open}`` it
    reads — the falsifier is kept per prediction for provenance but is not required by the
    evaluator's band grammar."""
    return {
        "id": proposal["id"],
        "statement": proposal.get("statement"),
        "mechanism": proposal.get("mechanism"),
        "predictions": [{"observable": p.get("observable"), "expected": p.get("expected"),
                         "falsifier": p.get("falsifier")}
                        for p in proposal.get("predictions") or [] if isinstance(p, dict)],
        "status": "open",
        "origin": {"kind": "proposed", "from": proposal["id"]},
    }
