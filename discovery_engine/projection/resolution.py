"""resolution — the DE 2.0 gap-resolution taxonomy.

A capability gap or a claim gap declares HOW it can be resolved as a ``resolution_route``:
an atomic enum of four strategies combined with a mode.

    resolution_route:
      routes: [acquire_data | develop_method | request_expert | spawn_research]  # >=1, from the enum
      mode: all | any        # all = every route must be satisfied; any = one suffices

The four routes are deliberately orthogonal strategies for closing a gap:

- ``acquire_data``    — the gap closes by obtaining a measurement / dataset that already exists
                        in the world (run an experiment, pull a benchmark, measure a constant).
- ``develop_method``  — the gap closes by building a capability we do not yet have (a process,
                        a solver, an analysis) — the capability-gap BUILD route generalizes to this.
- ``request_expert``  — the gap closes by a human/domain decision or prior the engine cannot derive.
- ``spawn_research``  — the gap closes by opening a new study / line of inquiry (the generative loop).

Sugar: a bare route string or a plain list normalizes to the full block (``mode`` defaults to
``all``); a single route needs no mode. This module is the CANONICAL vocabulary the engine and
the workspace cards share, so a card and the engine agree on what "resolve this gap" means.
"""
from __future__ import annotations

from typing import Any

ACQUIRE_DATA = "acquire_data"
DEVELOP_METHOD = "develop_method"
REQUEST_EXPERT = "request_expert"
SPAWN_RESEARCH = "spawn_research"

#: the atomic resolution-route enum (order is canonical, used for stable sorting)
RESOLUTION_ROUTES = (ACQUIRE_DATA, DEVELOP_METHOD, REQUEST_EXPERT, SPAWN_RESEARCH)
RESOLUTION_MODES = ("all", "any")


class ResolutionRouteError(ValueError):
    """An invalid ``resolution_route`` (unknown route, empty routes, or a bad mode)."""


def normalize_resolution_route(value: Any) -> dict:
    """Normalize the sugar forms into the canonical ``{"routes": [...], "mode": "all"|"any"}``.

    Accepts a bare route string (``"develop_method"``), a plain list of routes
    (``["acquire_data", "develop_method"]``), or a ``{routes, mode}`` mapping. A bare
    string or list defaults ``mode`` to ``"all"``; routes are de-duplicated in first-seen
    order. Raises :class:`ResolutionRouteError` on an unknown route, an empty route list,
    an unknown field, or a bad mode — the engine never silently drops a mis-declared route.
    """
    if isinstance(value, str):
        routes: list = [value]
        mode = "all"
    elif isinstance(value, (list, tuple)):
        routes = list(value)
        mode = "all"
    elif isinstance(value, dict):
        extra = set(value) - {"routes", "mode"}
        if extra:
            raise ResolutionRouteError(f"unknown resolution_route field(s): {sorted(extra)}")
        routes = list(value.get("routes") or [])
        mode = value.get("mode", "all")
    else:
        raise ResolutionRouteError(
            f"resolution_route must be a str, list, or mapping; got {type(value).__name__}")

    seen: set = set()
    deduped: list = []
    for r in routes:
        if r not in seen:
            seen.add(r)
            deduped.append(r)
    if not deduped:
        raise ResolutionRouteError("resolution_route needs at least one route")
    bad = [r for r in deduped if r not in RESOLUTION_ROUTES]
    if bad:
        raise ResolutionRouteError(
            f"unknown resolution route(s) {bad}; allowed: {list(RESOLUTION_ROUTES)}")
    if mode not in RESOLUTION_MODES:
        raise ResolutionRouteError(
            f"resolution_route mode must be one of {list(RESOLUTION_MODES)}, got {mode!r}")
    return {"routes": deduped, "mode": mode}


def is_valid_resolution_route(value: Any) -> bool:
    """True iff ``value`` normalizes to a valid resolution_route (never raises)."""
    try:
        normalize_resolution_route(value)
        return True
    except ResolutionRouteError:
        return False


def resolution_satisfied(resolution_route: Any, done_routes: Any) -> bool:
    """Is a gap's ``resolution_route`` satisfied, given the routes already carried out?

    ``mode: all`` → every declared route must be in ``done_routes``; ``mode: any`` → at
    least one. A ``None`` / empty resolution_route is unsatisfiable (nothing declared to do)."""
    if not resolution_route:
        return False
    block = normalize_resolution_route(resolution_route)
    done = set(done_routes or [])
    if block["mode"] == "all":
        return all(r in done for r in block["routes"])
    return any(r in done for r in block["routes"])


def resolution_route_of(record: Any) -> dict | None:
    """Read + normalize the ``resolution_route`` off a gap / claim-gap / card mapping, or
    ``None`` when it declares none. Raises :class:`ResolutionRouteError` if present but invalid."""
    if not isinstance(record, dict):
        return None
    raw = record.get("resolution_route")
    return normalize_resolution_route(raw) if raw is not None else None
