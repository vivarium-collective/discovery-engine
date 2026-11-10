"""Foundation for the workspace report linter.

Finding model (``LintFinding``), per-study context (``_LintContext``),
phase-aware completeness gating, override-key derivation + override-file IO,
real-composite reference resolution, workspace spec discovery, and the shared
primitives (``_cached``, ``_is_nonempty``, ``_walk_strings``, server-URL
resolver) used by the check modules.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

import yaml

from discovery_engine.study.band_provenance import has_numeric_band as _is_numeric_band
from discovery_engine.bibtex import bib_keys
from discovery_engine.reporting.rigor import run_is_emitter_backed
from discovery_engine.workspace_paths import WorkspacePaths
from discovery_engine.study.observable_lint import reconcile_study_observables




# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LintFinding:
    """One linter finding.

    Attributes:
        level: ``error`` (blocks publication), ``warning``, or ``info``.
        study_slug: The study slug the finding pertains to (or ``"<workspace>"``
            for workspace-level findings).
        field_path: Dotted path to the offending field (e.g.
            ``conclusion_logic.if_primary_tests_pass``).
        message: Human-readable explanation.
        override_key: Stable string suitable for an override file. Built
            from ``check_name + study_slug + field_path`` so it is stable
            across linter runs as long as the underlying violation is the
            same one.
        check: Internal check identifier (one of the keys in CHECKS).
    """

    level: str
    study_slug: str
    field_path: str
    message: str
    override_key: str
    check: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class _LintContext:
    """Per-study context handed to each check function.

    ``ws_cache`` is a plain dict shared by every ``_LintContext`` built
    within a single ``lint_workspace_report()`` call (all studies AND
    investigations in that run point at the *same* dict instance). It
    memoizes the workspace-global scans (bib keys, expert-doc names, viz
    classes) that depend only on ``ws_root`` — not on the study — so each
    scan runs at most once per lint run instead of once per study. See
    ``_cached()`` below and ``lint_workspace_report()``, which is the only
    place a fresh ``ws_cache`` is minted.
    """

    ws_root: Path
    slug: str
    spec: dict
    findings: list[LintFinding] = field(default_factory=list)
    strict: bool = False
    ws_cache: dict = field(default_factory=dict)

    def add(
        self,
        *,
        level: str,
        field_path: str,
        message: str,
        check: str,
    ) -> None:
        key = _override_key(check=check, slug=self.slug, field_path=field_path)
        self.findings.append(
            LintFinding(
                level=level,
                study_slug=self.slug,
                field_path=field_path,
                message=message,
                override_key=key,
                check=check,
            )
        )


# ---------------------------------------------------------------------------
# Phase-aware completeness gating (issue #97)
# ---------------------------------------------------------------------------

# A study advances Design -> Build -> Simulate -> Evaluate -> Decide. A study
# in early Design legitimately has no planned runs, simulation set, or variants
# yet — the processes it tests aren't built. Firing completeness *warnings* on
# those fields conflates "Design-stage, correctly sparse" with "should be
# filled," which inflates the warning count and buries the actionable findings.
_PHASE_ORDER = {"Design": 0, "Build": 1, "Simulate": 2, "Evaluate": 3, "Decide": 4}


def _phase_index(spec: dict) -> int | None:
    """Numeric rank of the study's ``phase``; ``None`` when unset/unknown.

    ``None`` means 'no phase signal' — callers preserve the pre-#97 behavior
    (warn) rather than silently suppressing.
    """
    phase = spec.get("phase")
    if not isinstance(phase, str) or not phase.strip():
        return None
    return _PHASE_ORDER.get(phase.strip().capitalize())


def _inferred_phase_index(spec: dict) -> int | None:
    """Best-effort phase index from status signals when `phase:` is absent.

    A seeded / planned study often carries `status` / `simulation_status` /
    `design_status` but no explicit `phase:`. Treating that as "phase unknown ->
    warning" flags an unbuilt design for missing runs / variants / visualizations
    it legitimately cannot have yet. Infer the stage instead:

    - status planned/draft, or design not yet approved, or simulation not run
      -> Design (0): correctly sparse, completeness checks stay info.
    - simulation ran/running -> Evaluate: built content is genuinely expected.
    """
    status = str(spec.get("status") or "").strip().lower()
    sim = str(spec.get("simulation_status") or "").strip().lower()
    design = str(spec.get("design_status") or "").strip().lower()
    if status in {"planned", "draft", "proposed"} or sim in {"not_run", "planned", "not run"} \
       or design in {"drafted", "draft", "proposed"}:
        return _PHASE_ORDER["Design"]
    if sim in {"ran", "running", "complete", "completed"}:
        return _PHASE_ORDER["Evaluate"]
    return None


def _completeness_level(spec: dict, *, warn_at: str) -> str:
    """Level for a completeness check, gated on the study's phase.

    - phase (or inferred stage) >= ``warn_at`` -> ``"warning"`` (genuinely overdue)
    - phase (or inferred stage) <  ``warn_at`` -> ``"info"``    (correctly sparse)
    - no phase and no status signal at all      -> ``"warning"`` (keep old behavior)
    """
    idx = _phase_index(spec)
    if idx is None:
        idx = _inferred_phase_index(spec)
    if idx is None:
        return "warning"
    return "warning" if idx >= _PHASE_ORDER[warn_at] else "info"


# ---------------------------------------------------------------------------
# Override-key derivation
# ---------------------------------------------------------------------------


def _override_key(*, check: str, slug: str, field_path: str) -> str:
    """Stable hash for an override entry.

    A short 12-char hex digest of ``check|slug|field_path``. Keeps the file
    grep-friendly while being deterministic across linter runs.
    """
    raw = f"{check}|{slug}|{field_path}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()[:12]
    return f"{check}:{slug}:{digest}"


# ---------------------------------------------------------------------------
# Override file IO
# ---------------------------------------------------------------------------


_OVERRIDE_FILE_NAME = "report-lint-overrides.json"


def override_path(ws_root: Path) -> Path:
    """Where the override JSON lives, relative to the workspace root."""
    return WorkspacePaths.load(ws_root).pbg / _OVERRIDE_FILE_NAME


def load_overrides(ws_root: Path) -> set[str]:
    """Return the set of override keys currently logged for this workspace.

    Missing file is treated as an empty set (no overrides).
    """
    path = override_path(ws_root)
    if not path.is_file():
        return set()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    overrides = data.get("overrides") or []
    return {entry["key"] for entry in overrides if isinstance(entry, dict) and "key" in entry}


def write_override(
    ws_root: Path,
    finding: LintFinding,
    *,
    reason: str = "force-published via /viva-report --force",
    now: _dt.datetime | None = None,
) -> Path:
    """Append a single override entry; idempotent (won't double-add).

    Returns the path to the override file.
    """
    now = now or _dt.datetime.now()
    path = override_path(ws_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {"schema_version": 1, "overrides": []}
    else:
        data = {"schema_version": 1, "overrides": []}
    data.setdefault("schema_version", 1)
    data.setdefault("overrides", [])
    keys = {e.get("key") for e in data["overrides"]}
    if finding.override_key not in keys:
        data["overrides"].append({
            "key": finding.override_key,
            "added_at": now.isoformat(timespec="seconds"),
            "reason": reason,
            # Embedded provenance — lets a reviewer find the original violation
            # without re-running the linter against an older workspace snapshot.
            "check": finding.check,
            "study_slug": finding.study_slug,
            "field_path": finding.field_path,
            "message": finding.message,
        })
    path.write_text(json.dumps(data, indent=2, sort_keys=False))
    return path


def has_blocking_errors(
    findings: Iterable[LintFinding],
    overrides: set[str] | None = None,
) -> bool:
    """True iff any error-level finding remains after override application."""
    overrides = overrides or set()
    for f in findings:
        if f.level == "error" and f.override_key not in overrides:
            return True
    return False


def apply_overrides(
    findings: Iterable[LintFinding],
    overrides: set[str],
) -> list[LintFinding]:
    """Downgrade error-level findings whose key is overridden to warnings.

    Returns a NEW list (does not mutate input). Useful for surfacing the
    fact that an override was applied without dropping the finding entirely.
    """
    out: list[LintFinding] = []
    for f in findings:
        if f.level == "error" and f.override_key in overrides:
            out.append(LintFinding(
                level="warning",
                study_slug=f.study_slug,
                field_path=f.field_path,
                message=f"[overridden] {f.message}",
                override_key=f.override_key,
                check=f.check,
            ))
        else:
            out.append(f)
    return out


# ---------------------------------------------------------------------------
# Real-composite resolution (pure helper — registry supplied by the caller)
# ---------------------------------------------------------------------------


def _collect_composite_refs(spec: dict) -> list[str]:
    """Every composite identifier a study spec REFERENCES, de-duplicated in
    discovery order.

    Collected from the canonical reference sites:

    * ``baseline[].composite`` (a single dict or a list of dicts),
    * ``conditions.baseline.composite`` and ``conditions.variants[].composite``,
    * ``simulation_set[].composite`` and ``simulation_set[].base_model``.

    Variant ``base_composite`` (which names a *baseline* by name, not a
    registered composite id) is intentionally NOT collected.
    """
    refs: list[str] = []

    def _add(v) -> None:
        if isinstance(v, str) and v.strip():
            refs.append(v.strip())

    baseline = (spec or {}).get("baseline")
    for b in (baseline if isinstance(baseline, list) else [baseline]):
        if isinstance(b, dict):
            _add(b.get("composite"))

    conditions = (spec or {}).get("conditions")
    if isinstance(conditions, dict):
        cb = conditions.get("baseline")
        for b in (cb if isinstance(cb, list) else [cb]):
            if isinstance(b, dict):
                _add(b.get("composite"))
        variants = conditions.get("variants")
        if isinstance(variants, list):
            for v in variants:
                if isinstance(v, dict):
                    _add(v.get("composite"))

    sim_set = (spec or {}).get("simulation_set")
    if isinstance(sim_set, list):
        for s in sim_set:
            if isinstance(s, dict):
                _add(s.get("composite"))
                _add(s.get("base_model"))

    seen: set[str] = set()
    out: list[str] = []
    for r in refs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def unresolved_composite_refs(spec: dict, known_composite_ids) -> list[str]:
    """Return the composite references a study DECLARES that are NOT in the
    caller-supplied set of known (registered) composite ids.

    Contract
    --------
    This helper is PURE: it has no access to a composite registry. The canonical
    signal that a study references a *real* composite is that its
    ``baseline[].composite`` (and the other reference sites collected by
    :func:`_collect_composite_refs`) resolves in the workspace registry. Because
    :mod:`discovery_engine.reporting.rigor` / this linter run on specs alone and cannot know
    the registry, the CALLER (the dashboard, which owns ``/api/composites``)
    passes ``known_composite_ids`` — the set/iterable of every registered
    composite id — and this function returns every declared reference absent from
    that set, de-duplicated and in declaration order. An empty result means all
    referenced composites resolve.

    Passing an empty / ``None`` ``known_composite_ids`` returns ALL declared
    refs (every reference is "unresolved" against an empty registry) — callers
    that don't yet have a registry should treat that as "unknown", not "all bad".
    """
    known = set(known_composite_ids or ())
    return [r for r in _collect_composite_refs(spec) if r not in known]


# ---------------------------------------------------------------------------
# Discovery: walk a workspace and yield (slug, spec) pairs
# ---------------------------------------------------------------------------


def _iter_study_specs(ws_root: Path) -> Iterator[tuple[str, dict]]:
    """Yield (slug, parsed-yaml) for every study under the workspace.

    Looks under ``<ws_root>/studies/<slug>/study.yaml`` first, then falls
    back to the legacy ``<ws_root>/investigations/<slug>/spec.yaml``.
    Silently skips unparseable YAML (the report renderer reports those
    separately; the linter focuses on content checks).
    """
    wp = WorkspacePaths.load(ws_root)
    studies_dir = wp.studies
    if studies_dir.is_dir():
        for child in sorted(studies_dir.iterdir()):
            if not child.is_dir():
                continue
            spec_path = child / "study.yaml"
            if not spec_path.is_file():
                continue
            try:
                data = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            slug = data.get("name") or child.name
            yield slug, data
    invs_dir = wp.investigations
    if invs_dir.is_dir():
        for child in sorted(invs_dir.iterdir()):
            if not child.is_dir():
                continue
            spec_path = child / "spec.yaml"
            if not spec_path.is_file():
                continue
            try:
                data = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
            except yaml.YAMLError:
                continue
            slug = data.get("name") or child.name
            yield slug, data


def _iter_investigation_specs(ws_root: Path) -> Iterator[tuple[str, dict]]:
    """Yield (slug, parsed-yaml) for every modern v2 investigation under the
    workspace — ``<ws_root>/investigations/<slug>/investigation.yaml``.

    These carry the investigation-level narrative spine (executive /
    scientific_argument / biological_story) the report renders, distinct from
    the per-study specs in :func:`_iter_study_specs`.
    """
    wp = WorkspacePaths.load(ws_root)
    invs_dir = wp.investigations
    if not invs_dir.is_dir():
        return
    for child in sorted(invs_dir.iterdir()):
        if not child.is_dir():
            continue
        spec_path = child / "investigation.yaml"
        if not spec_path.is_file():
            continue
        try:
            data = yaml.safe_load(spec_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        yield (data.get("name") or child.name), data


# ---------------------------------------------------------------------------
# Shared primitives — generic helpers + the per-lint-run workspace-scan cache,
# used by check modules across the package.
# ---------------------------------------------------------------------------


def _is_nonempty(v) -> bool:
    if v is None:
        return False
    if isinstance(v, (str, list, dict, tuple, set)):
        return bool(v)
    return True


def _walk_strings(obj, prefix: str = "") -> Iterator[tuple[str, str]]:
    """Yield (dotted-path, string-value) for every string leaf in obj."""
    if isinstance(obj, str):
        if prefix:
            yield prefix, obj
        return
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _walk_strings(v, f"{prefix}.{k}" if prefix else str(k))
        return
    if isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _walk_strings(v, f"{prefix}[{i}]")
        return


def _cached(ctx: "_LintContext", key: str, compute):
    """Memoize a workspace-global scan for the lifetime of one lint run.

    ``ctx.ws_cache`` is shared across every ``_LintContext`` created inside
    a single ``lint_workspace_report()`` call, so a scan that depends only
    on ``ctx.ws_root`` (not on the individual study) runs at most once per
    lint run — instead of once per study — while staying lazy: it's only
    computed the first time a check actually needs it.

    Scoped to one call, not module-level: a fresh ``ws_cache`` dict is
    minted on every ``lint_workspace_report()`` invocation, so this can
    never serve stale data across two separate lint runs even if the
    workspace's files changed in between.
    """
    if key not in ctx.ws_cache:
        ctx.ws_cache[key] = compute()
    return ctx.ws_cache[key]


# ---------------------------------------------------------------------------
# Optional live-server URL resolver (used by the server-backed lint probes)
# ---------------------------------------------------------------------------


_SERVER_URL_RESOLVER = None


def register_server_url_resolver(fn) -> None:
    """Install ``fn(ws_root) -> str | None`` used by the optional live-server
    lint probes. ``None`` restores the default (the workbench server-info
    reader in ``discovery_engine.integration.server_preflight``, imported
    lazily; if unavailable the probes are simply skipped)."""
    global _SERVER_URL_RESOLVER
    _SERVER_URL_RESOLVER = fn


def _server_url(ws_root: Path):
    fn = _SERVER_URL_RESOLVER
    if fn is None:
        try:
            from discovery_engine.integration.server_preflight import read_server_url as fn
        except Exception:  # noqa: BLE001
            return None
    return fn(ws_root)


__all__ = [
    'LintFinding',
    '_LintContext',
    '_PHASE_ORDER',
    '_phase_index',
    '_inferred_phase_index',
    '_completeness_level',
    '_override_key',
    '_OVERRIDE_FILE_NAME',
    'override_path',
    'load_overrides',
    'write_override',
    'has_blocking_errors',
    'apply_overrides',
    '_collect_composite_refs',
    'unresolved_composite_refs',
    '_iter_study_specs',
    '_iter_investigation_specs',
    '_is_nonempty',
    '_walk_strings',
    '_cached',
    '_SERVER_URL_RESOLVER',
    'register_server_url_resolver',
    '_server_url',
]
