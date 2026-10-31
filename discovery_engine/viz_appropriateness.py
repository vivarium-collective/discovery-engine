"""Visualization appropriateness (PROTOTYPE, review-only).

Beside ``viz_freshness`` (is a chart current?) and ``viz_proposals`` (is an
observable uncovered?), this answers the third viz question: is a DECLARED viz
APPROPRIATE where it is shown? Two derived, non-blocking signals, plus a
cross-study roll-up:

  * **scope mismatch** — a cross-arm comparison viz (one whose input ports include
    the full ``arms``/``labels`` set) declared on a single member study depicts the
    WHOLE investigation, not that study. It is investigation-scope but study-placed.
  * **untied** — a viz with no ``covers``/``requires`` tie is not bound to any study
    observable or finding, so neither the report nor a reader can say what it is
    showing or which claim it supports ("the right information tied to the viz").
  * **duplicated cross-arm** — the same investigation-scope viz declared on more
    than one member study is the reader-facing ambiguity (the identical cross-arm
    chart appearing under several studies).

Pure: classifies, never renders, no I/O, no ``scientific_judgment``. Derived from
the viz entry + the viz class's input ports (data the engine passes in via
``scope_of``). Non-blocking (never in ``_CLOSURE_BLOCKING``). Mirrors
viz_freshness / viz_proposals exactly.
"""
from __future__ import annotations

from typing import Callable

OK, MISPLACED_SCOPE, UNTIED = "ok", "misplaced_scope", "untied"
STUDY, INVESTIGATION, UNKNOWN = "study", "investigation", "unknown"

# A viz whose input ports include the full arm set is a cross-arm comparison
# (investigation-scope), not a view of one study's own trajectory.
_CROSS_ARM_PORTS = {"arms", "labels"}


def viz_scope(inputs_schema: dict | None) -> str:
    """Derive a viz's scope from its declared input ports. ``None`` (uninspectable
    class) → UNKNOWN, so an unresolved viz never raises a false scope alarm."""
    if inputs_schema is None:
        return UNKNOWN
    ports = {str(p).strip().lower() for p in inputs_schema.keys()}
    return INVESTIGATION if (ports & _CROSS_ARM_PORTS) else STUDY


def _covers(entry: dict) -> list:
    from .viz_freshness import normalize_entry

    ns = normalize_entry(entry)
    return sorted(set(ns["covers"]) | set(ns["requires"]))


def classify_viz(entry: dict, inputs_schema: dict | None, *, is_member_study: bool = True) -> dict:
    """Pure per-viz verdict: ``ok`` or a list of issues (``misplaced_scope`` / ``untied``)."""
    entry = entry or {}
    scope = viz_scope(inputs_schema)
    covers = _covers(entry)
    issues = []
    if scope == INVESTIGATION and is_member_study:
        issues.append({"kind": MISPLACED_SCOPE,
                       "detail": "cross-arm comparison shown under a single study — it depicts the "
                                 "whole investigation, not this study (move to the investigation level "
                                 "or label it cross-study)"})
    if not covers:
        issues.append({"kind": UNTIED,
                       "detail": "no covers/requires tie — not bound to any study observable or finding"})
    return {"name": entry.get("name"), "address": entry.get("address"),
            "scope": scope, "covers": covers, "issues": issues, "ok": not issues}


def study_viz_appropriateness(study_spec: dict,
                              scope_of: Callable[[str | None], dict | None]) -> list[dict]:
    """Per-viz verdicts for one study's ``visualizations:`` list. ``scope_of`` maps a viz
    address to its class's input-port schema (or None if unresolved)."""
    out = []
    for e in (study_spec or {}).get("visualizations") or []:
        if isinstance(e, dict):
            out.append(classify_viz(e, scope_of(e.get("address"))))
    return out


def duplicated_cross_arm(studies: dict,
                         scope_of: Callable[[str | None], dict | None]) -> list[dict]:
    """Investigation-scope viz declared on more than one member study — the reader-facing
    ambiguity (the same cross-arm chart under several studies)."""
    seen: dict[str, list[str]] = {}
    for slug, sp in (studies or {}).items():
        if not isinstance(sp, dict):
            continue
        for e in sp.get("visualizations") or []:
            if isinstance(e, dict) and viz_scope(scope_of(e.get("address"))) == INVESTIGATION:
                key = e.get("address") or e.get("name")
                if key:
                    seen.setdefault(key, []).append(slug)
    return [{"address": addr, "studies": sorted(sl), "count": len(sl)}
            for addr, sl in sorted(seen.items()) if len(sl) > 1]


def investigation_viz_appropriateness(studies: dict,
                                      scope_of: Callable[[str | None], dict | None]) -> dict:
    """Investigation roll-up for the viz tab: per-study verdicts + the duplicated
    cross-arm list + a single ``any_issue`` flag. Pure + non-blocking."""
    per_study = {slug: study_viz_appropriateness(sp, scope_of)
                 for slug, sp in (studies or {}).items() if isinstance(sp, dict)}
    dupes = duplicated_cross_arm(studies, scope_of)
    any_issue = bool(dupes) or any(not v["ok"] for rows in per_study.values() for v in rows)
    return {"per_study": per_study, "duplicated_cross_arm": dupes, "any_issue": any_issue}
