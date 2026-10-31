"""Capability gaps as a first-class part of the discovery cycle (PROTOTYPE).

The model-building analog of ``viz_proposals``: when ``model_architect`` resolves a
required mechanism to the **BUILD** route (no catalog component provides it), that
is a *capability gap* — a typed, content-addressed ``CapabilityGap`` the cycle emits
as a ``capability_gap`` frontier item (op ``build_process``), routes (on accept) to an injected ``build_hook``,
and closes once a process providing the mechanism appears in the re-catalog.

Mirrors viz_proposals exactly:
  * content-addressed ``cg:<study>:<hash8>`` ids (same gap never re-proposes);
  * status lifecycle ``proposed -> accepted -> built`` (+ ``needs_author`` when the
    hook returns needs_implementation, ``rejected``/``failed`` terminal);
  * rationale-gated accept (author or AI-decider — the engine NEVER auto-builds);
  * close-on-fresh: a gap whose mechanism is provided in the catalog flips to built;
  * non-blocking throughout — a gap is actionable, not a closure failure.

The executor is the workspace-injected ``build_hook`` (``build_capability(gap,
ws_root) -> {status, process, detail, gap_id}``), the model-building analog of the
viz-loop's ``render_hook``.

PROTOTYPE / review-only: the self-contained propose/accept/build/close logic below
is ready to wire into ``discover.run_cycle`` beside ``propose_visualizations`` and
into ``execute_action(..., build_hook=…)`` beside the ``render_hook`` path.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any, Callable

from .model_architect import BUILD, ModelBuildRequest, choose_acquisition_route
from .model_architect import provides  # noqa: F401  (public routing predicate, re-exported)

CAP = 2  # per study per cycle (mirrors viz_proposals.CAP)
TERMINAL = ("rejected", "built")
KIND = "capability_gap"
OP = "build_process"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash8(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:8]


def _slug(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-") or "investigation"


def _tokens(text: Any) -> set:
    return {t for t in re.split(r"[^a-z0-9]+", str(text or "").lower()) if t}


def process_present(catalog: list | None, mechanism: str, process: str | None = None) -> str | None:
    """EXACT presence check (not the fuzzy routing ``provides``): the composite ref of a
    catalog component that is the named ``process`` (composite or id equal), or, with no
    process named, whose capability tokens contain ALL of the mechanism's tokens. None if
    absent (including an empty/None catalog)."""
    mt = _tokens(mechanism)
    for c in sorted((c for c in catalog or [] if isinstance(c, dict)),
                    key=lambda c: str(c.get("composite") or c.get("id"))):
        ref = c.get("composite") or c.get("id")
        if not ref:
            continue
        if process:
            if process in (c.get("composite"), c.get("id")):
                return str(ref)
        elif mt and mt <= {t for cap in c.get("capability") or [] for t in _tokens(cap)}:
            return str(ref)
    return None


def _derivable(mechanism: str, catalog: list) -> bool:
    """The catalog can build the mechanism: some component declares it in ``builds`` (a
    generator/template process able to derive it), so the build hook has a path that needs no
    human author. Token overlap is NOT used: any shared token already routes to wrap, never
    BUILD. Otherwise ``needs_author`` (mirrors viz derivable/needs_author)."""
    slug = _slug(mechanism)
    return any(slug in {_slug(m) for m in c.get("builds") or []}
               for c in catalog if isinstance(c, dict))


def _build_gap(mechanism: str, inv_slug: str, route_info: dict, study: str | None,
               derivable: bool, now: str) -> dict:
    # Content id: a pure function of (mechanism, inv) - no now()/uuid.
    return {
        "id": f"cg:{inv_slug}:{_hash8({'mechanism': mechanism, 'inv': inv_slug})}",
        "status": "proposed",
        "kind": KIND,
        "op": OP,
        "mechanism": mechanism,
        "study": study,
        "source": {"kind": "required_mechanism", "mechanism": mechanism,
                   "study": study, "inv": inv_slug},
        "suggested": {
            "route": BUILD,
            "rationale": route_info.get("rationale", ""),
            "needs_author": not derivable,
            "effort": "derivable" if derivable else "needs-author",
        },
        "approval": "review",            # rationale-gated accept; never auto-build
        "history": [{"at": now, "event": "proposed", "by": "engine"}],
        "built": None,
        "derivable": derivable,
    }


def required_mechanisms_from_spec(inv_spec: dict, studies: dict | None = None) -> list[str]:
    """Domain-general source of required mechanisms — the investigation CONTRACT, not
    a hardcoded tool list (mirrors how viz_proposals draws observables from the spec's
    gates/hypotheses). Union of ``inv_spec['required_mechanisms']`` and each study's
    ``required_mechanisms``, de-duplicated in declaration order. A biological-composite
    workspace gets the same loop for free — it declares its mechanisms in the spec and
    they resolve against whatever catalog is injected."""
    reqs = list((inv_spec or {}).get("required_mechanisms") or [])
    for s in (studies or {}).values():
        if isinstance(s, dict):
            reqs += list(s.get("required_mechanisms") or [])
    seen: set = set()
    out: list[str] = []
    for m in reqs:
        if m and m not in seen:
            seen.add(m)
            out.append(m)
    return out


def propose_capability_gaps(inv_spec: dict, studies: dict | None, catalog: list[dict] | None, *,
                            required_mechanisms: list[str] | None = None,
                            now: str | None = None) -> list[dict]:
    """Emit ``CapabilityGap`` records for every required mechanism whose acquisition route is
    BUILD. Mechanisms come from the spec contract (``required_mechanisms_from_spec``);
    ``required_mechanisms`` overrides it, and an explicit ``[]`` behaves like ``None``
    (falls back to the spec). Empty/None catalog -> NO proposals (nothing to resolve
    against). Each gap carries ``study`` (first sorted study requiring it, else None) and
    ``source``. Content-addressed ids; an id already on ``inv_spec['capability_gaps']``
    with a non-``proposed`` status is not re-emitted. At most ``CAP`` per study per cycle."""
    inv_spec = inv_spec or {}
    studies = studies or {}
    if not catalog:
        return []
    now = now or _now()
    inv_slug = _slug(inv_spec.get("name"))
    mechs = list(required_mechanisms) if required_mechanisms else required_mechanisms_from_spec(inv_spec, studies)
    done = {p.get("id") for p in inv_spec.get("capability_gaps") or []
            if isinstance(p, dict) and p.get("status", "proposed") != "proposed"}
    by_study: dict = {}
    for sname in sorted(studies):
        s = studies[sname]
        if isinstance(s, dict):
            for m in s.get("required_mechanisms") or []:
                by_study.setdefault(m, sname)

    routes = choose_acquisition_route(
        ModelBuildRequest(question_id=inv_slug, required_mechanisms=list(mechs)), catalog)
    cands = []
    for mech in sorted(routes):
        if routes[mech].get("route") != BUILD:
            continue  # reuse / wrap - no gap
        g = _build_gap(mech, inv_slug, routes[mech], by_study.get(mech), _derivable(mech, catalog), now)
        if g["id"] not in done:
            cands.append(g)
    out: list[dict] = []
    per: dict = {}
    for g in sorted(cands, key=lambda g: g["id"]):
        if per.get(g["study"], 0) >= CAP:
            continue
        per[g["study"]] = per.get(g["study"], 0) + 1
        out.append(g)
    return out


def _gap(gap: Any) -> dict:
    """Mirror of discover._viz_prop: a ``cg:`` gap dict that is still ``proposed``."""
    if not isinstance(gap, dict) or not str(gap.get("id", "")).startswith("cg:"):
        raise ValueError("not a capability gap (id must start with 'cg:')")
    if gap.get("status", "proposed") != "proposed":
        raise ValueError(f"capability gap {gap['id']!r} is already {gap.get('status')!r}; only a "
                         "'proposed' gap can be accepted or rejected")
    return gap


def accept_capability_gap(gap: dict, *, rationale: str, by: str = "author", now: str | None = None) -> dict:
    """Rationale-gated accept (author or AI-decider). Required before build."""
    if not (rationale or "").strip():
        raise ValueError("a rationale is required - accepting a gap authorizes a build")
    _gap(gap)
    gap["status"] = "accepted"
    gap["accept"] = {"rationale": rationale, "by": by}
    gap.setdefault("history", []).append({"at": now or _now(), "event": "accepted", "by": by,
                                          "rationale": rationale})
    return gap


def reject_capability_gap(gap: dict, *, rationale: str, by: str = "author", now: str | None = None) -> dict:
    """Terminal reject (rationale required, proposed-only)."""
    if not (rationale or "").strip():
        raise ValueError("a rationale is required - a decision on a gap documents why")
    _gap(gap)
    gap["status"] = "rejected"
    gap.setdefault("history", []).append({"at": now or _now(), "event": "rejected", "by": by,
                                          "rationale": rationale})
    return gap


def build_capability_gap(gap: dict, build_hook: Callable[[dict, Any], dict], *,
                         ws_root: Any = None, catalog: list | None = None,
                         now: str | None = None) -> dict:
    """Route an ACCEPTED gap to the workspace build hook (the engine never builds itself).
    reuse/built -> built only if the hook names a process, answers for THIS gap, and (when
    ``catalog`` is given) the process exists in it; needs_implementation -> needs_author;
    anything else, a hook exception, or a failed verification -> failed (never left
    ``accepted``)."""
    if not isinstance(gap, dict) or gap.get("status") != "accepted":
        raise ValueError(f"gap {(gap or {}).get('id')} must be accepted before build "
                         f"(is {(gap or {}).get('status')!r})")
    try:
        res = build_hook(gap, ws_root) or {}
    except Exception as e:  # noqa: BLE001
        res = {"status": "failed", "detail": f"build_hook raised {type(e).__name__}: {e}"}
    if not isinstance(res, dict):
        res = {"status": "failed", "detail": "build_hook returned a non-mapping"}
    st = res.get("status")
    process = res.get("process")
    if st in ("reuse", "built"):
        if res.get("gap_id") not in (None, gap.get("id")):
            st, res = "failed", {"detail": f"hook answered for {res.get('gap_id')!r}, not {gap.get('id')!r}"}
        elif not process:
            st, res = "failed", {"detail": "hook reported success but named no process"}
        elif catalog is not None and process_present(catalog, gap.get("mechanism", ""), process) is None:
            st, res = "failed", {"detail": f"process {process!r} not found in the catalog"}
    if st in ("reuse", "built"):
        gap["status"] = "built"
        gap["built"] = {"process": process, "via": st, "detail": res.get("detail")}
    elif st == "needs_implementation":
        gap["status"] = "needs_author"
        gap["detail"] = res.get("detail")
    else:
        gap["status"] = "failed"
        gap["detail"] = res.get("detail")
    gap.setdefault("history", []).append({"at": now or _now(), "event": gap["status"], "by": "build_hook"})
    return gap


def close_built_capability_gaps(gaps: list[dict], catalog: list[dict] | None,
                                studies: dict | None = None, *, now: str | None = None) -> list[str]:
    """Close-on-fresh (mirror of ``_close_built_viz_proposals``): flip an ``accepted`` gap to
    ``built`` ONLY when a process providing its mechanism is actually present in the catalog
    (exact check, ``process_present``); the process ref is recorded in ``built``. Anything
    else (proposed/needs_author/failed/terminal, or no such process) is untouched. ``studies``
    is accepted for signature parity with viz and unused."""
    closed: list[str] = []
    for g in gaps or []:
        if not isinstance(g, dict) or g.get("status") != "accepted":
            continue
        prior = (g.get("built") or {}).get("process") if isinstance(g.get("built"), dict) else None
        ref = process_present(catalog, g.get("mechanism", ""), prior)
        if ref is None:
            continue
        at = now or _now()
        g["status"] = "built"
        g["built"] = {"at": at, "process": ref, "via": "recatalog"}
        g.setdefault("history", []).append({"at": at, "event": "built", "by": "recatalog", "process": ref})
        closed.append(g["id"])
    return closed
