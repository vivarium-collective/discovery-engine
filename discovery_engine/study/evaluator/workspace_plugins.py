"""Pluggable workspace evaluators + derived-scalar computers (generic seam —
the framework ships none), plus the grader-identity stamp and freshness hashing.

A workspace augments evaluation by shipping a ``register_evaluators(registry)``
hook in its ``viva_<name>`` / ``pbg_<name>`` package (the same package that hosts
build_core()). When evaluate_test hits a measure.kind that is NOT a native
RUN_DATA_KIND, it consults the workspace registry before falling back to the
agent bucket.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable


_WS_EVALUATOR_CACHE: dict[str, dict[str, Callable]] = {}
_WS_DERIVED_SCALAR_CACHE: dict[str, dict[str, Callable]] = {}


def clear_workspace_evaluator_cache() -> None:
    """Drop the per-workspace evaluator + derived-scalar caches."""
    _WS_EVALUATOR_CACHE.clear()
    _WS_DERIVED_SCALAR_CACHE.clear()


def _workspace_package_slug(ws_root: Any) -> str:
    """Importable package name for the workspace, mirroring build_core()'s home.

    Canonical is ``viva_<name>`` (post-rebrand), but a not-yet-migrated workspace
    may still ship ``pbg_<name>/`` on disk. ``resolve_package_dir`` returns
    whichever exists (viva_ preferred, pbg_ recognized during the migration
    window), so the evaluators hook imports the real package. Derived from the
    workspace ``name`` (where build_core lives for a viva-template workspace).
    dashes -> underscores.
    """
    import yaml
    from pathlib import Path

    from viva_workspace import resolve_package_dir
    name = "workspace"
    wy = Path(ws_root) / "workspace.yaml"
    if wy.is_file():
        data = yaml.safe_load(wy.read_text(encoding="utf-8")) or {}
        name = data.get("name") or name
    return resolve_package_dir(ws_root, name).name


def _workspace_evaluator_packages(ws_root: Any) -> list[str]:
    """Importable package names that may host the workspace's ``evaluators`` hook.

    Two conventions exist and both must be tried:

    * the **name-derived slug** (``viva_<name>`` / ``pbg_<name>``) — where
      ``build_core`` lives for a viva-template workspace whose package differs
      from its simulation package (e.g. package_path = ``<pkg>`` but
      the workspace package is ``viva_<name>``), and
    * workspace.yaml **``package_path``** — the workspace's own package for the
      common case where the package IS the package_path (e.g. multiscale-BATS,
      package_path = ``multiscale_bats``). The name-derived slug there would be
      ``viva_multiscale_BATS``, which does not exist, so ``package_path`` is the
      only place the hook lives.

    Returned de-duplicated, name-slug first. Never raises.
    """
    import yaml
    from pathlib import Path

    cands: list[str] = []
    try:
        cands.append(_workspace_package_slug(ws_root))
    except Exception:  # noqa: BLE001
        pass
    try:
        wy = Path(ws_root) / "workspace.yaml"
        if wy.is_file():
            data = yaml.safe_load(wy.read_text(encoding="utf-8")) or {}
            pkg_path = data.get("package_path")
            if pkg_path:
                cands.append(str(pkg_path).replace("-", "_"))
    except Exception:  # noqa: BLE001
        pass
    seen: set[str] = set()
    out: list[str] = []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def load_workspace_evaluators(ws_root: Any) -> dict[str, Callable]:
    """Import the workspace's ``<pkg>.evaluators`` hook(s) and collect registrations.

    Tries each candidate package (name-derived slug AND workspace.yaml
    ``package_path`` — see :func:`_workspace_evaluator_packages`) so the hook is
    found whether the workspace package is ``viva_<name>`` or its own
    ``package_path``. Registrations from every hook found are merged.

    Returns a {measure_kind: callable} dict. Empty if ws_root is None or no hook
    is present. A hook that raises is skipped (a broken workspace hook must never
    crash evaluation — degrade to the agent bucket). Cached per ws_root.
    """
    import sys
    from pathlib import Path
    if ws_root is None:
        return {}
    key = str(Path(ws_root).resolve())
    if key in _WS_EVALUATOR_CACHE:
        return _WS_EVALUATOR_CACHE[key]
    registry: dict[str, Callable] = {}
    if key not in sys.path:
        sys.path.insert(0, key)
    for pkg in _workspace_evaluator_packages(ws_root):
        try:
            mod = __import__(f"{pkg}.evaluators", fromlist=["register_evaluators"])
            hook = getattr(mod, "register_evaluators", None)
            if callable(hook):
                hook(registry)
        except Exception:  # noqa: BLE001 — never let a workspace hook break evaluation
            continue
    _WS_EVALUATOR_CACHE[key] = registry
    return registry


# Bump when the grading LOGIC changes (not for data changes). Stamped into each run's
# computed_outcomes as ``_grader`` so a consumer (the investigation-graph card) can detect
# a stale computed gate and force a re-grade when the grader has moved on.
GRADER_VERSION = "3-freshness"


def storeless(fn: Callable) -> Callable:
    """Mark a workspace evaluator as STORE-LESS: it re-derives its result internally
    (e.g. a recovery evaluator that re-runs SINDy+fit) and needs no run store. Such an
    evaluator is graded by :func:`compute_outcomes` even when the run store is unresolved,
    instead of being skipped as ``store_unresolved``. Use as a decorator on the evaluator
    a workspace registers via ``register_evaluators``."""
    fn.storeless = True  # type: ignore[attr-defined]
    return fn


def _storeless_kinds(ws_root: Any) -> set[str]:
    """Measure kinds whose registered workspace evaluator is store-less (see :func:`storeless`)."""
    return {k for k, fn in load_workspace_evaluators(ws_root).items()
            if getattr(fn, "storeless", False)}


def _grader_stamp(inputs_hash: str | None = None, store: str | None = None,
                  graded_at: str | None = None) -> dict:
    """The grader-identity stamp written into every computed_outcomes block (Phase 2).

    ``version``     — ``GRADER_VERSION``; a consumer compares it to the current grader and re-grades
                      when it differs (``grader_freshness.grade_freshness``).
    ``graded_at``   — ISO-8601 UTC time the grade was computed. Idempotent: a caller passes the
                      prior ``graded_at`` back in when the ``inputs_hash`` is unchanged, so a
                      re-grade of identical inputs does not churn the YAML.
    ``inputs_hash`` — ``_inputs_hash`` of (test defs + run provenance + evaluator-registry
                      fingerprint); changes when the grader or its inputs change.
    ``store``       — three-valued provenance: ``resolved`` (openable run store — render a link),
                      ``store_less`` (re-derived by design, no store — "re-derived, no run store"),
                      ``unresolved`` (a store that SHOULD exist could not be opened).
    """
    stamp: dict = {"version": GRADER_VERSION}
    stamp["graded_at"] = graded_at or datetime.now(timezone.utc).isoformat()
    if inputs_hash is not None:
        stamp["inputs_hash"] = inputs_hash
    if store is not None:
        stamp["store"] = store
    return stamp


def _evaluator_fingerprint(ws_root: Any) -> str:
    """A stable fingerprint of the workspace's registered evaluator kinds + the grader version —
    one of the three inputs to ``_inputs_hash``. Changes when the grading surface changes."""
    try:
        kinds = sorted(load_workspace_evaluators(ws_root).keys())
    except Exception:  # noqa: BLE001 — a broken workspace never breaks the hash
        kinds = []
    return _sha16(json.dumps([GRADER_VERSION, kinds], sort_keys=True))


def _run_provenance_id(run: dict) -> str:
    """The identity of the specific run data a grade was computed from (best-effort)."""
    keys = ("name", "commit", "timestamp", "env_id", "provenance_status", "generation_id")
    return json.dumps({k: run.get(k) for k in keys if run.get(k) is not None}, sort_keys=True)


def _inputs_hash(tests: Any, run: dict, ws_root: Any) -> str:
    """Hash of (test definitions + run provenance + evaluator-registry fingerprint). The value a
    freshness check compares against — it changes iff the grade's inputs change."""
    payload = json.dumps([tests, _run_provenance_id(run or {})], sort_keys=True, default=str)
    return _sha16(payload + _evaluator_fingerprint(ws_root))


def _sha16(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()[:16]


def load_workspace_derived_scalars(ws_root: Any) -> dict[str, Callable]:
    """Import the workspace's ``<pkg>.evaluators`` hook and collect derived-scalar
    computers registered via ``register_derived_scalars(reg)``.

    Returns a ``{field_name: fn}`` dict where ``fn(reader, test, ws_root) -> float``.
    Empty if ws_root is None or no hook is present. A hook that raises is skipped
    (a broken workspace hook must never crash evaluation). Cached per ws_root.
    """
    import sys
    from pathlib import Path
    if ws_root is None:
        return {}
    key = str(Path(ws_root).resolve())
    if key in _WS_DERIVED_SCALAR_CACHE:
        return _WS_DERIVED_SCALAR_CACHE[key]
    registry: dict[str, Callable] = {}
    if key not in sys.path:
        sys.path.insert(0, key)
    for pkg in _workspace_evaluator_packages(ws_root):
        try:
            mod = __import__(f"{pkg}.evaluators", fromlist=["register_derived_scalars"])
            hook = getattr(mod, "register_derived_scalars", None)
            if callable(hook):
                hook(registry)
        except Exception:  # noqa: BLE001 — never let a workspace hook break evaluation
            continue
    _WS_DERIVED_SCALAR_CACHE[key] = registry
    return registry


def declares_sources(*observables: str) -> Callable:
    """Decorator for a derived-scalar computer: declare the emitted observables it reads.

    A derived scalar (e.g. ``log10_enrichment``) is computed at evaluation time from one or
    more run observables (e.g. ``enrichment_factor``). Declaring those sources lets a run be
    CHECKED and CONFIGURED to emit them --- the run-readiness contract maps a behavior-test's
    derived measure to the observables the run must emit, instead of the run emitting the
    (unproduced) derived-scalar name and recording an empty readout."""
    def _decorate(fn: Callable) -> Callable:
        fn.source_observables = tuple(observables)  # type: ignore[attr-defined]
        return fn
    return _decorate
