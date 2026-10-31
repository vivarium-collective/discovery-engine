"""Capability gaps as a first-class part of the discovery cycle (PROTOTYPE).

The model-building analog of ``viz_proposals``: when ``model_architect`` resolves a
required mechanism to the **BUILD** route (no catalog component provides it), that
is a *capability gap* — a typed, content-addressed ``CapabilityGap`` the cycle emits
as a ``needs_build`` frontier item, routes (on accept) to an injected ``build_hook``,
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
from datetime import datetime, timezone
from typing import Any, Callable

from .model_architect import BUILD, ModelBuildRequest, _provides, choose_acquisition_route

TERMINAL = ("rejected", "built")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _hash8(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:8]


def _build_gap(mechanism: str, inv_slug: str, route_info: dict, source: dict, now: str) -> dict:
    return {
        "id": f"cg:{inv_slug}:{_hash8({'mechanism': mechanism, 'inv': inv_slug})}",
        "status": "proposed",
        "kind": "needs_build",
        "mechanism": mechanism,
        "source": source,  # which study/question surfaced the requirement
        "suggested": {
            "route": BUILD,
            "rationale": route_info.get("rationale", ""),
            "needs_author": True,
            "effort": "needs-build",
        },
        "approval": "review",            # rationale-gated accept; never auto-build
        "history": [{"at": now, "event": "proposed", "by": "engine"}],
        "built": None,
    }


def propose_capability_gaps(inv_spec: dict, catalog: list[dict], required_mechanisms: list[str],
                            *, source_by_mech: dict | None = None, now: str | None = None) -> list[dict]:
    """Emit ``CapabilityGap`` records for every required mechanism whose acquisition
    route is BUILD (no catalog component provides it). Content-addressed + idempotent:
    an id already on ``inv_spec['capability_gaps']`` with a non-``proposed`` status is
    not re-emitted (rejected/built terminal; accepted persisted)."""
    inv_spec = inv_spec or {}
    now = now or _now()
    inv_slug = inv_spec.get("name", "")
    source_by_mech = source_by_mech or {}
    done = {p.get("id") for p in inv_spec.get("capability_gaps") or []
            if isinstance(p, dict) and p.get("status", "proposed") != "proposed"}

    request = ModelBuildRequest(question_id=inv_slug, required_mechanisms=list(required_mechanisms))
    routes = choose_acquisition_route(request, catalog or [])

    out: list[dict] = []
    for mech in sorted(routes):
        if routes[mech].get("route") != BUILD:
            continue  # reuse / wrap — no gap
        gap = _build_gap(mech, inv_slug, routes[mech], source_by_mech.get(mech, {}), now)
        if gap["id"] not in done:
            out.append(gap)
    return out


def accept_capability_gap(gap: dict, *, rationale: str, by: str = "author", now: str | None = None) -> dict:
    """Rationale-gated accept (author or AI-decider). Required before build."""
    if not rationale:
        raise ValueError("accept requires a rationale (author or AI-decider)")
    if gap.get("status") in TERMINAL:
        return gap
    gap["status"] = "accepted"
    gap["accept"] = {"rationale": rationale, "by": by}
    gap.setdefault("history", []).append({"at": now or _now(), "event": "accepted", "by": by})
    return gap


def reject_capability_gap(gap: dict, *, rationale: str, now: str | None = None) -> dict:
    gap["status"] = "rejected"
    gap.setdefault("history", []).append({"at": now or _now(), "event": "rejected", "by": "author", "rationale": rationale})
    return gap


def build_capability_gap(gap: dict, build_hook: Callable[[dict, Any], dict], *,
                         ws_root: Any = None, now: str | None = None) -> dict:
    """Route an accepted gap to the workspace build hook. The engine NEVER builds
    itself — it only calls the injected ``build_hook``. Maps the hook's status onto
    the gap lifecycle: reuse/built -> built, needs_implementation -> needs_author
    (awaiting a human/agent), failed -> failed."""
    if gap.get("status") != "accepted":
        raise ValueError(f"gap {gap.get('id')} must be accepted before build (is {gap.get('status')!r})")
    res = build_hook(gap, ws_root) or {}
    st = res.get("status")
    if st in ("reuse", "built"):
        gap["status"] = "built"
        gap["built"] = {"process": res.get("process"), "via": st, "detail": res.get("detail")}
    elif st == "needs_implementation":
        gap["status"] = "needs_author"
        gap["detail"] = res.get("detail")
    else:
        gap["status"] = "failed"
        gap["detail"] = res.get("detail")
    gap.setdefault("history", []).append({"at": now or _now(), "event": gap["status"], "by": "build_hook"})
    return gap


def close_built_capability_gaps(gaps: list[dict], catalog: list[dict]) -> list[str]:
    """Close-on-fresh: a non-terminal gap whose mechanism is now provided by a catalog
    component (after a re-catalog) flips to ``built`` and leaves the frontier."""
    closed: list[str] = []
    for g in gaps or []:
        if g.get("status") in TERMINAL:
            continue
        mech = g.get("mechanism", "")
        if any(_provides(c, mech) for c in catalog or []):
            g["status"] = "built"
            if not isinstance(g.get("built"), dict):
                g["built"] = {}
            g["built"]["via"] = "recatalog"
            g.setdefault("history", []).append({"at": _now(), "event": "built", "by": "recatalog"})
            closed.append(g["id"])
    return closed
