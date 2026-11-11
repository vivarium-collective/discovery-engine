"""CritiqueReport — the typed contract for an adversarial review of a result (DE-06).

The counterpart to ``hypothesize``: where ``hypothesize`` proposes explanations for a
surprise, ``critique`` challenges a *result* a skeptical reviewer should not accept at face
value — a verdict that outruns its evidence, a confidence badge that reads as spin, a claim
without backing. As everywhere, the engine surfaces *where* critique is warranted and
validates the contract; an agent authors the critique content (``skills/critique/SKILL.md``).

A CritiqueReport is **advisory**: it is recorded against a target and a human addresses or
dismisses it. It never changes a verdict on its own and never fabricates evidence.

Shapes
------
CritiqueReport (``investigation.yaml::critique_reports[]``)::

    - id: cr:ab12cd34ef56
      target: mw-equilibrium-reproduction          # the study/claim under review
      alternative_diagnoses: ["the gain is plain movement-to-resources, not sensing"]
      unsupported_claims:
        - {claim: "equilibrium reached", why: "gate_status=passed but no verified run"}
      suggested_discriminations:
        - {observable: colonization_rate, test: "non-sensing motile control"}
      status: open                                  # open | addressed | dismissed
      provenance: {trigger: verdict_divergence, source: mw-equilibrium-reproduction, by: agent}
      at: "2026-11-11T00:00:00+00:00"

All functions here are pure and AI-free.
"""
from __future__ import annotations

import datetime as _dt
import hashlib

from discovery_engine.study.verdict import (
    confidence_diverges_from_derived,
    diverges_from_authored,
    roll_up_verdict,
)

OPEN = "open"
ADDRESSED = "addressed"
DISMISSED = "dismissed"
_STATUSES = (OPEN, ADDRESSED, DISMISSED)
_SECTIONS = ("alternative_diagnoses", "unsupported_claims", "suggested_discriminations")


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _critique_id(target: str, trigger: str | None) -> str:
    h = hashlib.sha1(f"{(target or '').strip()}|{trigger or ''}".encode()).hexdigest()[:12]
    return f"cr:{h}"


def _study_slug(spec: dict) -> str:
    s = spec or {}
    return s.get("name") or s.get("slug") or s.get("id") or "study"


def _nonempty_sections(report: dict) -> list[str]:
    return [k for k in _SECTIONS if isinstance(report.get(k), list) and report.get(k)]


def validate_critique_report(report: dict) -> dict:
    """Raise ``ValueError`` unless ``report`` satisfies the contract; else return it.

    Required: a ``target`` (what is being critiqued), a ``provenance`` naming the trigger,
    and **at least one non-empty** substantive section (alternative diagnoses, unsupported
    claims, or suggested discriminations). A critique with nothing to say is not a critique."""
    if not isinstance(report, dict):
        raise ValueError("critique report must be a mapping")
    if not str(report.get("target") or "").strip():
        raise ValueError("critique report needs a 'target' (the study/claim under review)")
    if not _nonempty_sections(report):
        raise ValueError("critique report needs >=1 non-empty section "
                         "(alternative_diagnoses / unsupported_claims / suggested_discriminations)")
    prov = report.get("provenance")
    if not isinstance(prov, dict) or not str(prov.get("trigger") or "").strip():
        raise ValueError("critique report needs provenance with a 'trigger'")
    status = report.get("status", OPEN)
    if status not in _STATUSES:
        raise ValueError(f"status must be one of {_STATUSES}, got {status!r}")
    return report


def build_critique_report(target: str, *, alternative_diagnoses=None, unsupported_claims=None,
                          suggested_discriminations=None, provenance: dict | None = None,
                          status: str = OPEN, at: str | None = None) -> dict:
    """Assemble + validate a CritiqueReport. Pure."""
    prov = dict(provenance or {})
    prov.setdefault("trigger", "manual")
    prov.setdefault("by", "agent")
    report = {
        "id": _critique_id(target, prov.get("trigger")),
        "target": str(target).strip(),
        "alternative_diagnoses": list(alternative_diagnoses or []),
        "unsupported_claims": list(unsupported_claims or []),
        "suggested_discriminations": list(suggested_discriminations or []),
        "status": status,
        "provenance": prov,
        "at": at or _now(),
    }
    return validate_critique_report(report)


def coerce_critique_report(report: dict) -> dict:
    """Normalize a raw (agent-authored) critique into a valid CritiqueReport: validate, then
    fill ``id`` (content-addressed), ``status`` and ``at`` if absent. Pure. The agent never
    has to supply an id."""
    validate_critique_report(report)
    prov = report.get("provenance") or {}
    out = {**report}
    for k in _SECTIONS:
        out.setdefault(k, [])
    out.setdefault("status", OPEN)
    out.setdefault("at", _now())
    out["id"] = report.get("id") or _critique_id(report.get("target"), prov.get("trigger"))
    return out


def critique_triggers(study_specs: list[dict]) -> list[dict]:
    """Where adversarial critique is warranted: a study whose authored claim outruns its
    evidence. Pure. Two deterministic signals, both already canonical:

    - ``verdict_divergence`` — authored ``gate_status`` disagrees with the computed verdict
      (a result asserted the evidence does not support);
    - ``confidence_divergence`` — the authored ``confidence`` badge disagrees with the
      derived confidence (reads as spin).

    Returns typed triggers ``{id, kind, study, note, authored, derived}`` an agent answers
    with a CritiqueReport. AI-free: the engine points at the overclaim; the agent argues it."""
    out: list[dict] = []
    for spec in study_specs or []:
        if not isinstance(spec, dict):
            continue
        slug = _study_slug(spec)
        if diverges_from_authored(spec):
            out.append({
                "id": _critique_id(slug, "verdict_divergence").replace("cr:", "ct:"),
                "kind": "verdict_divergence",
                "study": slug,
                "authored": str(spec.get("gate_status") or "").strip(),
                "derived": roll_up_verdict(spec)["result"],
                "note": "authored gate_status disagrees with the computed verdict — the "
                        "claimed result outruns the evidence",
            })
        if confidence_diverges_from_derived(spec):
            out.append({
                "id": _critique_id(slug, "confidence_divergence").replace("cr:", "ct:"),
                "kind": "confidence_divergence",
                "study": slug,
                "authored": str(spec.get("confidence") or "").strip(),
                "derived": None,
                "note": "authored confidence badge disagrees with the derived confidence — "
                        "reads as spin",
            })
    return out
