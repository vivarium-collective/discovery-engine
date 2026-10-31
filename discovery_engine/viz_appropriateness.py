"""Visualization appropriateness (PROTOTYPE, review-only).

Beside ``viz_freshness`` (is a chart current?) and ``viz_proposals`` (is an
observable uncovered?), this answers the third viz question: is a DECLARED viz
APPROPRIATE where it is shown? Two derived, non-blocking signals, plus a
cross-study roll-up:

  * **scope mismatch** — a cross-arm comparison viz (one whose input ports include
    ``arms``, or that declares an explicit ``scope: investigation``) declared on a single member study depicts the
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

OK, MISPLACED_SCOPE, UNTIED, NOT_ASSESSED = "ok", "misplaced_scope", "untied", "not_assessed"
STUDY, INVESTIGATION, UNKNOWN = "study", "investigation", "unknown"

# A viz whose input ports include the full arm set is a cross-arm comparison
# (investigation-scope), not a view of one study's own trajectory. Only ``arms`` is a
# reliable signal: ``labels`` alone is carried by per-study multi-curve viz too (a
# convergence sweep labels its intervals), so it would false-positive on them.
_CROSS_ARM_PORTS = {"arms"}


def viz_scope(inputs_schema: dict | None, declared: str | None = None) -> str:
    """A viz's scope. An explicit ``scope:`` on the entry wins (the escape hatch for
    cross-arm viz fed PRE-AGGREGATED data — e.g. a tradeoff map taking ``points``, a
    decision table taking ``rows`` — whose ports carry no ``arms`` to derive from).
    Otherwise derive from the input ports; ``None`` (uninspectable class) → UNKNOWN, so
    an unresolved viz never raises a false scope alarm."""
    d = str(declared or "").strip().lower()
    if d in (STUDY, INVESTIGATION):
        return d
    if inputs_schema is None:
        return UNKNOWN
    ports = {str(p).strip().lower() for p in inputs_schema.keys()}
    return INVESTIGATION if (ports & _CROSS_ARM_PORTS) else STUDY


def _strs(v) -> list[str]:
    """Safely coerce a user-authored ``covers``/``requires`` value to clean strings: a
    list keeps its string items, a bare string is one tie, anything else is empty."""
    if isinstance(v, str):
        v = [v]
    if not isinstance(v, (list, tuple)):
        return []
    return [x.strip() for x in v if isinstance(x, str) and x.strip()]


def _covers(entry: dict) -> list:
    """The viz's ties: ``covers`` U ``requires`` U ``config.inputs_map`` values — the same
    definition as ``viz_proposals.covered_observables``. Never raises on malformed YAML."""
    entry = entry if isinstance(entry, dict) else {}
    cov = set(_strs(entry.get("covers"))) | set(_strs(entry.get("requires")))
    cfg = entry.get("config") if isinstance(entry.get("config"), dict) else {}
    im = cfg.get("inputs_map") or entry.get("inputs_map")
    if isinstance(im, dict):
        cov |= {v.strip() for v in im.values() if isinstance(v, str) and v.strip()}
    return sorted(cov)


def _builds_on_parents(study_spec: dict) -> set:
    """Distinct parent studies this study ``builds-on``, gathered from every place an edge
    can live: canonical ``pipeline_gate.prerequisites``, ``parent_relations``, and the legacy
    ``parent_studies`` backbone (``relations.study_relations`` shapes). Deduped by study."""
    spec = study_spec if isinstance(study_spec, dict) else {}
    pg = spec.get("pipeline_gate")
    lists = [pg.get("prerequisites") if isinstance(pg, dict) else None,
             spec.get("parent_relations"), spec.get("parent_studies")]
    parents = set()
    for lst in lists:
        for e in (lst if isinstance(lst, list) else []):
            if not isinstance(e, dict) or not isinstance(e.get("study"), str):
                continue
            rel = str(e.get("relation") or "").strip().lower().replace("_", "-")
            if rel == "builds-on" and e["study"].strip():
                parents.add(e["study"].strip())
    return parents


def is_synthesis_study(study_spec: dict) -> bool:
    """A study that builds-on two or more DISTINCT parents is an aggregation / synthesis
    node — the legitimate home for investigation-scope (cross-arm) viz, which are therefore
    NOT misplaced there."""
    return len(_builds_on_parents(study_spec)) >= 2


def classify_viz(entry: dict, inputs_schema: dict | None, *, on_synthesis: bool = False) -> dict:
    """Pure per-viz verdict. ``state`` is ``ok`` | ``issues`` | ``not_assessed`` (scope
    unresolvable — distinct from appropriate; never an issue). ``ok`` is True only for
    ``state == "ok"``. ``on_synthesis`` exempts investigation-scope viz from
    ``misplaced_scope`` (a synthesis study is where cross-arm comparisons belong)."""
    entry = entry if isinstance(entry, dict) else {}
    scope = viz_scope(inputs_schema, entry.get("scope"))
    covers = _covers(entry)
    issues = []
    if scope == INVESTIGATION and not on_synthesis:
        issues.append({"kind": MISPLACED_SCOPE,
                       "detail": "cross-arm comparison shown under a single study — it depicts the "
                                 "whole investigation, not this study (move to the investigation level "
                                 "or label it cross-study)"})
    if not covers:
        issues.append({"kind": UNTIED,
                       "detail": "no covers/requires tie — not bound to any study observable or finding"})
    state = "issues" if issues else (NOT_ASSESSED if scope == UNKNOWN else OK)
    return {"name": entry.get("name"), "address": entry.get("address"),
            "scope": scope, "covers": covers, "issues": issues,
            "state": state, "ok": state == OK}


def study_viz_appropriateness(study_spec: dict,
                              scope_of: Callable[[str | None], dict | None]) -> list[dict]:
    """Per-viz verdicts for one study's ``visualizations:`` list. ``scope_of`` maps a viz
    address to its class's input-port schema (or None if unresolved)."""
    out = []
    on_synthesis = is_synthesis_study(study_spec)
    vizs = (study_spec or {}).get("visualizations") if isinstance(study_spec, dict) else None
    for e in vizs if isinstance(vizs, list) else []:
        if isinstance(e, dict):
            out.append(classify_viz(e, scope_of(e.get("address")), on_synthesis=on_synthesis))
    return out


def duplicated_cross_arm(studies: dict,
                         scope_of: Callable[[str | None], dict | None]) -> list[dict]:
    """Investigation-scope viz declared on more than one PLAIN member study — the
    reader-facing ambiguity. Hosting studies are deduped by slug; synthesis hosts are
    excluded (a synthesis re-host is legitimate). Keyed on address; name is the fallback
    only for address-less entries, in its own ``name:`` namespace so it can't collide with
    an address."""
    seen: dict[str, set] = {}
    for slug, sp in (studies or {}).items():
        if not isinstance(sp, dict) or is_synthesis_study(sp):
            continue
        vizs = sp.get("visualizations")
        for e in vizs if isinstance(vizs, list) else []:
            if not isinstance(e, dict):
                continue
            if viz_scope(scope_of(e.get("address")), e.get("scope")) != INVESTIGATION:
                continue
            addr = e.get("address")
            name = e.get("name")
            if isinstance(addr, str) and addr.strip():
                key = addr.strip()
            elif isinstance(name, str) and name.strip():
                key = "name:" + name.strip().lower()
            else:
                continue
            seen.setdefault(key, set()).add(slug)
    return [{"address": k, "studies": sorted(sl), "count": len(sl)}
            for k, sl in sorted(seen.items()) if len(sl) > 1]


def investigation_viz_appropriateness(studies: dict,
                                      scope_of: Callable[[str | None], dict | None]) -> dict:
    """Investigation roll-up for the viz tab: per-study verdicts + the duplicated
    cross-arm list + a single ``any_issue`` flag. Pure + non-blocking."""
    per_study = {slug: study_viz_appropriateness(sp, scope_of)
                 for slug, sp in (studies or {}).items() if isinstance(sp, dict)}
    dupes = duplicated_cross_arm(studies, scope_of)
    rows_all = [v for rows in per_study.values() for v in rows]
    any_issue = bool(dupes) or any(v["issues"] for v in rows_all)
    return {"per_study": per_study, "duplicated_cross_arm": dupes, "any_issue": any_issue,
            "not_assessed": sum(1 for v in rows_all if v["state"] == NOT_ASSESSED)}
