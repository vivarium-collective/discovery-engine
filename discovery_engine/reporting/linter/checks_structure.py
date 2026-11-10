"""Structural checks: visualization-address resolution, DAG-edge consistency,
multi-axis status, and runs-DB / status-vs-runs drift.
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
from discovery_engine.reporting.linter.context import *  # noqa: F401,F403




# ---------------------------------------------------------------------------
# 11. visualization_address_unresolved
# ---------------------------------------------------------------------------


import re as _re

_VIZ_CLASS_RE = _re.compile(r"^\s*class\s+(\w+)\s*[\(:]", _re.MULTILINE)
_VIZ_DEF_RE = _re.compile(r"^\s*def\s+update_(\w+)\s*\(", _re.MULTILINE)


def _viz_classes_in_workspace(ws_root: Path) -> set[str]:
    """Scan every <ws_root>/*/visualizations/ and <ws_root>/visualizations/
    for class definitions and `update_*` factory functions. Returns the set
    of names that a `local:<Name>` address could resolve to.

    Static analysis only — no module import. For `update_foo_bar` the helper
    yields both `foo_bar` and `FooBar` (the `as_visualization` decorator
    derives the registered class name from the function's snake_case
    suffix, and dashboard usage uses either form).
    """
    out: set[str] = set()
    candidates: list[Path] = []
    try:
        children = list(ws_root.iterdir())
    except OSError:
        return out
    for child in children:
        if not child.is_dir():
            continue
        v = child / "visualizations"
        if v.is_dir():
            candidates.append(v)
    direct = ws_root / "visualizations"
    if direct.is_dir():
        candidates.append(direct)
    for d in candidates:
        for py in d.rglob("*.py"):
            try:
                src = py.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for m in _VIZ_CLASS_RE.finditer(src):
                out.add(m.group(1))
            for m in _VIZ_DEF_RE.finditer(src):
                snake = m.group(1)
                pascal = "".join(part.capitalize() for part in snake.split("_"))
                out.add(snake)
                out.add(pascal)
    return out


_VIZ_SCAN_SKIP = frozenset({
    "__pycache__", "node_modules", "out", "outputs", "reports",
    "datasets", "references", "tests", "visualizations",
})


def _pkg_class_locations(ws_root: Path) -> dict[str, str]:
    """``{ClassName: workspace-relative file}`` for every ``class`` defined under
    a workspace package BUT NOT under a ``visualizations/`` directory.

    Lets the unresolved-address check tell the difference between "no such viz
    class exists" and "the class exists, just in the wrong place" — the common
    authoring mistake of defining a ``Visualization`` subclass in a package-root
    module (e.g. ``<pkg>/viz_foo.py``) instead of ``<pkg>/visualizations/``,
    where the dashboard's ``local:`` resolver and :func:`_viz_classes_in_workspace`
    both look. First definition wins. Pure text scan — no import.
    """
    out: dict[str, str] = {}
    try:
        children = [c for c in ws_root.iterdir()
                    if c.is_dir() and not c.name.startswith(".")]
    except OSError:
        return out
    pkg_dirs = [c for c in children
                if (c / "__init__.py").is_file() and c.name not in _VIZ_SCAN_SKIP]
    for pkg in pkg_dirs:
        for py in sorted(pkg.rglob("*.py")):
            rel_parts = py.relative_to(pkg).parts
            if any(p in _VIZ_SCAN_SKIP or p.startswith(".") for p in rel_parts):
                continue
            try:
                src = py.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            for m in _VIZ_CLASS_RE.finditer(src):
                out.setdefault(m.group(1), py.relative_to(ws_root).as_posix())
    return out


# Figure-file suffixes a bare file-pointer field may name. The workbench's
# tolerant discovery renders + downloads such a figure, but the canonical
# declaration is ``address: <scheme>:<file>``. HTML → ``html:``; images →
# ``image:``.
_FIGURE_FILE_SUFFIXES = {".svg", ".png", ".gif", ".jpg", ".jpeg", ".html", ".htm"}


def _figure_file_pointer(v: dict) -> tuple[str, str] | None:
    """If ``v`` declares a figure FILE via a bare file-pointer field
    (``path``/``file``/``chart``/``src``) — rather than an ``address:`` scheme —
    return ``(ref, canonical_scheme)`` for the canonical-form nudge; else None.
    ``canonical_scheme`` is ``html`` for self-contained HTML, ``image`` for a
    static image."""
    for fld in ("path", "file", "chart", "src"):
        cand = str(v.get(fld) or "").strip()
        if not cand:
            continue
        suffix = Path(cand).suffix.lower()
        if suffix not in _FIGURE_FILE_SUFFIXES:
            continue
        scheme = "html" if suffix in {".html", ".htm"} else "image"
        return cand, scheme
    return None


def _check_visualization_addresses(ctx: _LintContext) -> None:
    """Each ``study.visualizations[].address`` of the form ``local:<Name>``
    must resolve to a class declared somewhere under a workspace
    ``<package>/visualizations/`` directory. Otherwise the dashboard renders
    the entry as text with no chart and gives no clue why.

    Dotted-path addresses (``pkg.module.ClassName``) are intentionally NOT
    checked here — that would require importing arbitrary workspace code
    inside the linter. Authors of dotted addresses should test them with
    ``/viva-study preview-viz``.
    """
    viz = ctx.spec.get("visualizations") or []
    if not isinstance(viz, list):
        return
    known: set[str] | None = None  # lazy: only scan the FS when needed
    for idx, v in enumerate(viz):
        if not isinstance(v, dict):
            continue
        addr = v.get("address")
        # mem3dg-readdy friction #26: study.yaml.visualizations entries
        # missing `address:` would 500 at render time with KeyError:
        # 'address'. The error surfaces inside the rendered viz iframe and
        # is invisible to anyone not opening the dashboard. Catch it at
        # lint time instead, naming the workspace.yaml.visualizations[].class
        # cross-reference as the natural fix.
        if not isinstance(addr, str) or not addr:
            viz_name = v.get("name", f"<index-{idx}>")
            # A figure declared via a bare file-pointer field (path/file/chart/src)
            # renders + downloads via the workbench's tolerant discovery, but the
            # canonical form is `address: <scheme>:<file>`. Nudge (warning), don't
            # block, and name the right scheme for this file type.
            ptr = _figure_file_pointer(v)
            if ptr is not None:
                ref, scheme = ptr
                ctx.add(
                    level="warning",
                    field_path=f"visualizations[{idx}].address",
                    message=(
                        f"Visualization {viz_name!r} declares its figure file via a "
                        f"bare file-pointer field instead of the canonical "
                        f"`address:` form. The workbench renders and downloads it, "
                        f"but use `address: {scheme}:{ref}` so figure discovery is "
                        f"unambiguous (this is what drives the investigation "
                        f"figure count and the `↓ figures` download zip)."
                    ),
                    check="visualization_path_not_canonical",
                )
                continue
            # A design-stage stub (status: stub) legitimately has no address
            # yet — the three-view scaffold declares World/Mechanism/Decision
            # views up front and the author wires each one as the study is
            # built. Downgrade to an info nudge rather than a blocking error so
            # a freshly-scaffolded study isn't buried in viz errors; a non-stub
            # entry missing its address is still the blocking KeyError below.
            if str(v.get("status") or "").strip().lower() == "stub":
                ctx.add(
                    level="info",
                    field_path=f"visualizations[{idx}].address",
                    message=(
                        f"Visualization {viz_name!r} is a design-stage stub with "
                        "no `address:` yet. Wire it to a class in "
                        "<package>/visualizations/ (address: local:<ClassName>) "
                        "as the study is built, then drop `status: stub`."
                    ),
                    check="visualization_address_missing",
                )
                continue
            ctx.add(
                level="error",
                field_path=f"visualizations[{idx}].address",
                message=(
                    f"Visualization {viz_name!r} has no `address:` field. "
                    "The dashboard's renderer raises KeyError('address') and "
                    "produces an error-stub HTML iframe (invisible to lint, "
                    "visible in the dashboard). Add `address: local:<ClassName>` "
                    "pointing at a class in <package>/visualizations/, or set "
                    "`workspace.yaml.visualizations[].class` and reference it "
                    "from the study by name."
                ),
                check="visualization_address_missing",
            )
            continue
        if not addr.startswith("local:"):
            continue  # dotted paths out of scope
        cls = addr[len("local:"):].strip()
        if not cls:
            continue
        if known is None:
            known = _cached(ctx, "viz_classes", lambda: _viz_classes_in_workspace(ctx.ws_root))
        if cls in known:
            continue
        viz_name = v.get("name", f"<index-{idx}>")
        # Distinguish "no such class" from "class exists, wrong place" — the
        # common authoring mistake of defining a Visualization subclass in a
        # package-root module instead of <pkg>/visualizations/. When the class
        # IS defined elsewhere in the package, name the file and the fix so the
        # author moves it rather than re-writing a class that already exists.
        misplaced = _cached(ctx, "pkg_class_locations",
                            lambda: _pkg_class_locations(ctx.ws_root))
        if cls in misplaced:
            ctx.add(
                level="error",
                field_path=f"visualizations[{idx}].address",
                message=(
                    f"Visualization {viz_name!r} address {addr!r} is not "
                    f"discoverable: class {cls!r} is defined at "
                    f"{misplaced[cls]!r}, but the dashboard's local: resolver and "
                    f"the linter only find viz classes under a workspace "
                    f"<package>/visualizations/ directory. Move the class into "
                    f"<package>/visualizations/<snake_name>.py (a shim re-export at "
                    f"the old path keeps existing imports working). See "
                    f"docs/conventions/visualizations.md."
                ),
                check="visualization_class_misplaced",
            )
            continue
        ctx.add(
            level="error",
            field_path=f"visualizations[{idx}].address",
            message=(
                f"Visualization {viz_name!r} address {addr!r} is not registered. "
                f"No class {cls!r} was found under any workspace "
                f"<package>/visualizations/ directory. Add the class there, "
                f"or change the address to a dotted module path "
                f"(e.g. 'discovery_engine.visualizations.TimeSeriesFromObservables')."
            ),
            check="visualization_address_unresolved",
        )


# ---------------------------------------------------------------------------
# 12. dag_edges_legacy_and_canonical_both_set
# ---------------------------------------------------------------------------


def _parent_slug(entry) -> str | None:
    """Pull the parent slug out of either entry shape (bare-string or dict)."""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict) and isinstance(entry.get("study"), str):
        return entry["study"]
    return None


def _check_dag_edges_legacy_and_canonical_both_set(ctx: _LintContext) -> None:
    """A study should declare DAG edges via ``pipeline_gate.prerequisites``
    (canonical, Section 2 of the 8-section structure). The legacy
    ``parent_studies`` field still works as a back-compat fallback, but
    setting BOTH risks drift — the dashboard reads only the canonical
    field, so a parent added to ``parent_studies`` after migration goes
    silently unused.

    Three findings shapes:

    - error: both fields set with DIFFERENT parent sets (real drift —
      the legacy entries are being silently ignored)
    - warning: both fields set with the SAME parent set (redundant; drop
      ``parent_studies``)
    - warning: only ``parent_studies`` set (migration deferred — same
      message as the runtime DeprecationWarning)
    """
    pg = ctx.spec.get("pipeline_gate") or {}
    canonical_raw = pg.get("prerequisites") if isinstance(pg, dict) else None
    legacy_raw = ctx.spec.get("parent_studies") or []

    canonical = {s for s in (_parent_slug(e) for e in (canonical_raw or [])) if s}
    legacy = {s for s in (_parent_slug(e) for e in legacy_raw) if s}

    if not canonical and not legacy:
        return

    if canonical and legacy:
        if canonical == legacy:
            ctx.add(
                level="warning",
                field_path="parent_studies",
                message=(
                    "Study declares the same parents in both "
                    "`pipeline_gate.prerequisites` (canonical) and "
                    "`parent_studies` (legacy). Drop the `parent_studies` "
                    "field — the dashboard reads only the canonical one."
                ),
                check="dag_edges_legacy_redundant",
            )
        else:
            ctx.add(
                level="error",
                field_path="parent_studies",
                message=(
                    "Study declares conflicting DAG edges: "
                    f"`pipeline_gate.prerequisites` lists {sorted(canonical)} "
                    f"but `parent_studies` lists {sorted(legacy)}. The "
                    "dashboard reads only `pipeline_gate.prerequisites`, so "
                    "the legacy entries are silently ignored. Reconcile or "
                    "drop `parent_studies`."
                ),
                check="dag_edges_legacy_and_canonical_disagree",
            )
        return

    if legacy and not canonical:
        ctx.add(
            level="warning",
            field_path="parent_studies",
            message=(
                "Study uses the legacy `parent_studies` field for DAG edges. "
                "Migrate to `pipeline_gate.prerequisites` (canonical, "
                "Section 2). The dashboard still reads `parent_studies` as a "
                "back-compat fallback, but a future version will require "
                "the canonical field."
            ),
            check="dag_edges_legacy_only",
        )


# ---------------------------------------------------------------------------
# 13. status_legacy_only — F1 (multi-axis status canonical)
# ---------------------------------------------------------------------------


_MULTI_AXIS_STATUS_FIELDS = (
    "design_status",
    "implementation_status",
    "simulation_status",
    "evaluation_status",
    "gate_status",
    "expert_review_status",
)


def _check_status_legacy_only(ctx: _LintContext) -> None:
    """A study should set at least one Pass A multi-axis status field
    (design_status, implementation_status, simulation_status,
    evaluation_status, gate_status, expert_review_status) instead of the
    legacy `status` enum. The dashboard's effective_status() helper still
    falls back to `status` for back-compat, but the legacy field can't
    carry the orthogonal-axes semantics — a study can be
    `simulation_status: ran` AND `evaluation_status: failed_evaluation`
    at the same time, which a single `status` can't express.

    Fires a warning when `status` is set and NO multi-axis field is set.
    Silent when at least one multi-axis field is set (regardless of
    whether `status` is ALSO set — the dashboard prefers the multi-axis
    value, so redundancy here is harmless drift, not a foot-gun).
    """
    legacy = ctx.spec.get("status")
    if not legacy:
        return
    has_multi_axis = any(ctx.spec.get(f) for f in _MULTI_AXIS_STATUS_FIELDS)
    if has_multi_axis:
        return
    ctx.add(
        level="warning",
        field_path="status",
        message=(
            f"Study uses the legacy `status: {legacy!r}` field with no "
            "multi-axis status set. The canonical fields are the six "
            "Pass A axes (design_status, implementation_status, "
            "simulation_status, evaluation_status, gate_status, "
            "expert_review_status). The dashboard still reads `status` "
            "as a back-compat fallback, but the legacy enum can't carry "
            "the orthogonal-axes semantics — e.g. a study can be "
            "simulation_status:ran AND evaluation_status:failed_evaluation "
            "simultaneously, which `status` alone cannot express."
        ),
        check="status_legacy_only",
    )


# ---------------------------------------------------------------------------
# 14. runs_yaml_vs_db_drift — F2 (runs.db is canonical)
# ---------------------------------------------------------------------------


def _runs_db_path(ws_root: Path, slug: str) -> Path:
    return WorkspacePaths.load(ws_root).studies / slug / "runs.db"


def _runs_db_run_ids(ws_root: Path, slug: str) -> set[str]:
    """Return the set of run_ids in studies/<slug>/runs.db (runs_meta +
    simulations). Empty set if the DB is missing or broken."""
    db = _runs_db_path(ws_root, slug)
    if not db.is_file():
        return set()
    import sqlite3 as _sql
    out: set[str] = set()
    try:
        conn = _sql.connect(str(db))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "runs_meta" in tables:
            for r in conn.execute("SELECT run_id FROM runs_meta"):
                out.add(r[0])
        if "simulations" in tables:
            for r in conn.execute("SELECT simulation_id FROM simulations"):
                out.add(r[0])
        conn.close()
    except _sql.Error:
        return set()
    return out


def _runs_db_rows(ws_root: Path, slug: str) -> list[dict]:
    """Return [{run_id, status}] from studies/<slug>/runs.db runs_meta.

    Empty list if the DB is missing/broken. Used to derive simulation
    status from execution state (status-drift check).
    """
    db = _runs_db_path(ws_root, slug)
    if not db.is_file():
        return []
    import sqlite3 as _sql
    out: list[dict] = []
    try:
        conn = _sql.connect(str(db))
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )}
        if "runs_meta" in tables:
            for r in conn.execute("SELECT run_id, status FROM runs_meta"):
                out.append({"run_id": r[0], "status": r[1]})
        conn.close()
    except _sql.Error:
        return []
    return out


def _check_status_out_of_date_vs_runs(ctx: _LintContext) -> None:
    """Integration friction: a report shipped a "planning" status after
    all studies had executed. Derive the observable status from runs.db and
    flag any stored axis (or a legacy planning headline) that contradicts it,
    so the drift is caught pre-publication instead of by the expert.
    """
    try:
        from discovery_engine.study import status as _ss
    except ImportError:
        return
    rows = _runs_db_rows(ctx.ws_root, ctx.slug)
    for dis in _ss.status_disagreements(ctx.spec, rows):
        ctx.add(
            level="error",
            field_path=dis.get("axis", "status"),
            message=dis.get("message", "status is out of date relative to runs"),
            check="status_out_of_date_vs_runs",
        )


def _check_status_claims_done_but_no_runs_recorded(ctx: _LintContext) -> None:
    """Forward-drift: a study DECLARES completion/pass but records no runs.

    ``status_disagreements`` deliberately ignores this direction — a stored
    ``ran`` with an empty *local* runs.db is ambiguous, because the db may live
    in a different checkout. But when the study's OWN spec carries no ``runs:``
    block AND runs.db is empty, there is nothing in-repo backing the claim, so
    the derive-on-read ``simulation_status`` resolves to ``not_run`` and the
    report renders the study as "pending / not run" even though its headline
    says passed.

    This is the gap a reviewer hit on a workspace investigation (Rashmi,
    2026-05-31): "I think the first study is passed. But I am not sure whether
    it ran the simulations or not. Tests still show pending." The two
    foundational studies declared ``status: completed`` / ``gate_status:
    passed`` but had no ``runs:`` block, so the report showed them as not-run.
    Warning-level (non-blocking): the fix is to record a ``runs:`` block with
    parquet provenance, or to correct the status to match what actually ran.
    """
    spec = ctx.spec
    legacy = str(spec.get("status") or "").strip().lower()
    claims_done = (
        legacy in {"completed", "complete", "done", "passed"}
        or spec.get("gate_status") == "passed"
        or spec.get("evaluation_status") == "evaluated"
        or spec.get("simulation_status") == "ran"
    )
    if not claims_done:
        return
    # "Run provenance" = the author recorded SOMETHING about execution: a
    # `runs:` block (what derive_simulation_status actually reads), or at least
    # a `simulation_set:` / `planned_runs:` block. Only flag when ALL of these
    # are absent and runs.db is empty — i.e. the study claims done but records
    # nothing whatsoever about what ran (the dnaa-0/1 pre-migration state).
    has_evidence = any(
        isinstance(spec.get(k), list) and len(spec.get(k)) > 0
        for k in ("runs", "simulation_set", "planned_runs")
    )
    if has_evidence:
        return
    try:
        if _runs_db_rows(ctx.ws_root, ctx.slug):
            return  # runs.db has rows → backed by local execution state
    except Exception:
        pass
    claimed_by = (
        "gate_status: passed" if spec.get("gate_status") == "passed"
        else "evaluation_status: evaluated"
        if spec.get("evaluation_status") == "evaluated"
        else "simulation_status: ran" if spec.get("simulation_status") == "ran"
        else f"status: {spec.get('status')!r}"
    )
    ctx.add(
        level="warning",
        field_path="runs",
        message=(
            f"study declares completion ({claimed_by}) but records no runs "
            "anywhere — no `runs:`, `simulation_set:`, or `planned_runs:` block "
            "and runs.db has no rows. With no `runs:` block, simulation_status "
            "derives to not_run and the report shows this study as "
            "not-run/pending despite the passing headline. Add a `runs:` block "
            "with parquet provenance (see another study's `runs:` for the "
            "shape) or correct the status to match what actually ran."
        ),
        check="status_claims_done_no_runs_recorded",
    )


def _check_reviewer_clarity_ambiguities(ctx: _LintContext) -> None:
    """Surface anything that would render ambiguously on the reviewer-facing
    run/test/verdict strip — single-sourced from
    ``study_status.study_clarity_summary`` so the linter flags exactly what the
    report would show unclearly. Covers the "ran but every test renders pending"
    and "gate passed but a test FAILED" cases (the dnaa-replication 2026-05-31
    feedback). The "declares done but no runs" case is left to
    ``status_claims_done_no_runs_recorded`` to avoid double-flagging.
    """
    try:
        from discovery_engine.study import status as _ss
    except ImportError:
        return
    summary = _ss.study_clarity_summary(ctx.spec, ctx.spec.get("runs"))
    for note in summary.get("ambiguities", []):
        if "no runs are recorded" in note or "renders as" in note:
            continue  # handled by status_claims_done_no_runs_recorded
        ctx.add(
            level="warning",
            field_path="runs[].outcomes" if "pending" in note else "gate_status",
            message=f"reviewer-facing clarity: {note}",
            check="reviewer_clarity_ambiguity",
        )


def _check_viz_stale_vs_latest_run(ctx: _LintContext) -> None:
    """Charts whose source run != the study's latest run (or unrendered), and
    on-disk charts not in visualizations[]. warning by default; error under --strict.

    Supersedes the older mtime-based ``figure_stale_vs_run`` check: this uses the
    chart's stamped ``source_run_id`` provenance (viz_freshness) against the
    study's latest run in runs.db (run_registry), which is far more precise than
    comparing file mtimes.
    """
    from discovery_engine.viz.freshness import chart_freshness, manifest_diff
    from discovery_engine.study.run_registry import latest_run
    spec = ctx.spec
    study_dir = ctx.ws_root / "studies" / ctx.slug
    latest = latest_run(study_dir / "runs.db")
    entries = spec.get("visualizations") or []

    stale = []
    for e in entries:
        if not (e or {}).get("chart"):
            continue  # legacy address-only entries aren't chart-provenance-tracked
        state = chart_freshness(study_dir, e, latest)
        if state in ("stale", "unrendered"):
            stale.append((e.get("name"), state))
    untracked = list(manifest_diff(study_dir, entries)["untracked"])
    if not stale and not untracked:
        return

    # Fold the per-chart findings into ONE per-study finding so viz hygiene
    # stops dominating the gap count (gaps = error + warning) and the readiness
    # panel leads with substantive findings (needs_human readouts, uncited
    # bands). Demoted to INFO by default — a nudge, not a publication gap — but
    # still promoted to ERROR under --strict so the publish gate is preserved.
    level = "error" if getattr(ctx, "strict", False) else "info"
    parts = []
    if untracked:
        parts.append(
            f"{len(untracked)} chart(s) on disk not registered in visualizations[] "
            "(register with a render: command or remove)"
        )
    if stale:
        names = ", ".join(f"{n!r} ({s})" for n, s in stale)
        parts.append(
            f"{len(stale)} registered visualization(s) stale/unrendered vs the "
            f"study's latest run — {names} (run /viva-study refresh-viz)"
        )
    ctx.add(level=level, field_path="visualizations",
            message="; ".join(parts) + ".",
            check="viz_stale_vs_latest_run")


def _check_runs_yaml_vs_db_drift(ctx: _LintContext) -> None:
    """Per F2, runs.db is the canonical record of which runs exist. The
    `study.yaml.runs[]` field stays in the schema as a soft-deprecated
    back-compat shim — the dashboard still reads it as a fallback count,
    but new runs land only in runs.db.

    Drift is a foot-gun in two directions:

    - WARNING: `runs[]` has run_ids that runs.db does NOT have. Either
      the run records were copied from a previous workspace without
      copying runs.db, or the DB was wiped and the yaml wasn't —
      either way the dashboard will keep showing those runs but the
      Runs tab can't pull their metadata. Tell the user to either
      restore runs.db or migrate (drop the legacy entries).

    - INFO: `runs[]` and runs.db both list the same run_ids. Redundant
      but not broken — the workspace is mid-migration. Migrate at next
      edit.

    Silent when:
    - `runs[]` is absent or empty (the F2 target state).
    - runs.db doesn't exist AND `runs[]` is also empty (pristine study).
    """
    yaml_runs = ctx.spec.get("runs") or []
    if not yaml_runs:
        return

    yaml_ids = {
        r.get("run_id") for r in yaml_runs
        if isinstance(r, dict) and r.get("run_id")
    }
    if not yaml_ids:
        return  # malformed entries — out of scope

    db_ids = _runs_db_run_ids(ctx.ws_root, ctx.slug)
    yaml_only = yaml_ids - db_ids

    if yaml_only:
        ctx.add(
            level="warning",
            field_path="runs",
            message=(
                f"study.yaml.runs[] lists {len(yaml_only)} run_id(s) absent "
                f"from runs.db: {sorted(yaml_only)[:3]}"
                + (" …" if len(yaml_only) > 3 else "")
                + ". F2: runs.db is the canonical source of truth. The "
                "dashboard reads runs[] as a back-compat fallback, but "
                "metadata (params, started_at, log_path) only lives in "
                "runs.db. Either restore the runs.db rows or migrate by "
                "dropping these entries from study.yaml.runs[]."
            ),
            check="runs_yaml_vs_db_drift",
        )
        return

    # All yaml ids are also in db_ids — redundant entries from a legacy
    # write path. Migration is to drop runs[] entirely.
    ctx.add(
        level="info",
        field_path="runs",
        message=(
            f"study.yaml.runs[] redundantly lists {len(yaml_ids)} run_id(s) "
            "that runs.db already records. F2 makes runs.db canonical — "
            "drop study.yaml.runs[] at the next edit. The dashboard reads "
            "it only as a back-compat fallback count."
        ),
        check="runs_yaml_vs_db_redundant",
    )


def _check_runs_without_emitter(ctx: _LintContext) -> None:
    """Soft-WARN: a study records runs but none are persisted via an emitter.

    Future-proofing (composites + emitters): every investigation should persist
    its run trajectories via an emitter (sqlite / parquet / xarray) so results are
    reproducible from disk, not just summarised. A run record evidences this by
    carrying an ``emitter`` or a run-db reference (see
    :func:`discovery_engine.reporting.rigor.run_is_emitter_backed`) — the same predicate the
    ``run_persistence`` rigor dimension uses.

    Warning-level (non-blocking). Silent when:

    - the study records no ``runs[]`` (nothing to persist), or
    - at least one run record IS emitter-backed, or
    - ``studies/<slug>/runs.db`` exists with rows on disk — the canonical
      persistence (F2) is present even if the YAML records don't restate it, so
      warning would be a false positive.
    """
    runs = [r for r in (ctx.spec.get("runs") or []) if isinstance(r, dict)]
    if not runs:
        return
    if any(run_is_emitter_backed(r) for r in runs):
        return
    # Canonical persistence may live in runs.db even when the YAML records omit
    # the emitter field — don't warn when the DB has rows on disk.
    try:
        if _runs_db_rows(ctx.ws_root, ctx.slug):
            return
    except Exception:  # noqa: BLE001 — defensive: a missing/locked DB is not "persisted"
        pass
    ctx.add(
        level="warning",
        field_path="runs",
        message=(
            f"study records {len(runs)} run(s) but none are persisted via an emitter "
            "(no run carries emitter: sqlite/parquet/xarray or a run-db reference, and "
            "runs.db has no rows). Persist run trajectories via the workspace emitter so "
            "results are reproducible from disk, not just summarised."
        ),
        check="runs_without_emitter",
    )


__all__ = [
    '_VIZ_CLASS_RE',
    '_VIZ_DEF_RE',
    '_viz_classes_in_workspace',
    '_VIZ_SCAN_SKIP',
    '_pkg_class_locations',
    '_FIGURE_FILE_SUFFIXES',
    '_figure_file_pointer',
    '_check_visualization_addresses',
    '_parent_slug',
    '_check_dag_edges_legacy_and_canonical_both_set',
    '_MULTI_AXIS_STATUS_FIELDS',
    '_check_status_legacy_only',
    '_runs_db_path',
    '_runs_db_run_ids',
    '_runs_db_rows',
    '_check_status_out_of_date_vs_runs',
    '_check_status_claims_done_but_no_runs_recorded',
    '_check_reviewer_clarity_ambiguities',
    '_check_viz_stale_vs_latest_run',
    '_check_runs_yaml_vs_db_drift',
    '_check_runs_without_emitter',
]
