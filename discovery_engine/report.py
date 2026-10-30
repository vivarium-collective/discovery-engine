"""Render reports/index.html for workspace and per-model targets.

Pass 10B adds :func:`render_workspace_findings_index`, which walks every
``studies/*/study.yaml`` in the workspace and produces a flat cross-study
findings index at ``reports/findings.html`` (linked from the workspace
dashboard). The aggregation, sort, and per-row shape are documented on
that function.
"""
from __future__ import annotations
import shutil
from collections import defaultdict
from datetime import date
from pathlib import Path

from discovery_engine.text_utils import first_sentence
from discovery_engine.workspace_paths import WorkspacePaths

import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape

from ._resources import resource_dir
from .report_linter import (
    LintFinding,
    format_findings,
    has_blocking_errors,
    lint_workspace_report,
    load_overrides,
    write_override,
)


class ReportLintBlocked(RuntimeError):
    """Raised by render_workspace_report when blocking lint findings exist.

    Carries the list of findings so the caller can surface them. The
    ``/viva-report`` skill catches this and asks for ``--force`` (which
    writes each blocking finding's override key to
    ``.pbg/report-lint-overrides.json`` and retries).
    """

    def __init__(self, findings: list[LintFinding]):
        self.findings = findings
        super().__init__(format_findings(findings))


def _env(template_dir: Path) -> Environment:
    return Environment(
        loader=FileSystemLoader(str(template_dir)),
        autoescape=select_autoescape(["html"]),
        keep_trailing_newline=True,
    )


def _copy_assets(target_assets_dir: Path) -> None:
    """Copy static assets (style.css, render-helpers.js)."""
    target_assets_dir.mkdir(parents=True, exist_ok=True)
    src = resource_dir("templates") / "_assets"
    for name in ("style.css", "render-helpers.js"):
        shutil.copy2(src / name, target_assets_dir / name)


def render_workspace_report(
    ws_root: Path,
    *,
    today: str | None = None,
    lint: bool = True,
    force: bool = False,
    on_force_log_overrides: bool = True,
) -> Path:
    """Build <ws_root>/reports/index.html from workspace.yaml + decisions log.

    Pass B: by default, runs the report linter first. If any blocking
    error-level findings exist (and are not yet in the override file),
    raises :class:`ReportLintBlocked` and refuses to render — UNLESS
    ``force=True``, in which case each blocking finding's override_key
    is appended to ``<ws_root>/.pbg/report-lint-overrides.json`` and the
    render proceeds. This satisfies the spec's "lint failures block
    publication unless explicitly overridden and logged" acceptance.

    Args:
        lint: Run the linter pre-render. Default True. Pass False to
            preserve the pre-Pass-B unconditional behavior (e.g. for
            internal callers that have already linted).
        force: If True, blocking errors are written to the override file
            and the render proceeds. Default False.
        on_force_log_overrides: If True (default), --force writes any
            blocking errors to the override file. If False, --force
            silently bypasses without logging — STRONGLY discouraged;
            kept for unit tests.
    """
    if lint:
        findings = lint_workspace_report(ws_root)
        overrides = load_overrides(ws_root)
        if has_blocking_errors(findings, overrides):
            if not force:
                raise ReportLintBlocked(
                    [f for f in findings if f.level == "error" and f.override_key not in overrides]
                )
            if on_force_log_overrides:
                for f in findings:
                    if f.level == "error" and f.override_key not in overrides:
                        write_override(ws_root, f)
    today = today or date.today().isoformat()
    wp = WorkspacePaths.load(ws_root)
    ws = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
    decisions_file = wp.docs / "decisions.yaml"
    decisions = (
        (yaml.safe_load(decisions_file.read_text(encoding="utf-8")) or {}).get("decisions", [])
        if decisions_file.exists() else []
    )
    env = _env(resource_dir("templates") / "workspace" / "reports")
    tpl = env.get_template("index.html.j2")
    out = wp.reports / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    _copy_assets(wp.reports / "assets")
    # Pass 10B: also harvest cross-study findings for the dashboard link
    # + a sibling findings.html page (which is rendered next).
    findings_count = _count_workspace_findings(ws_root)
    out.write_text(tpl.render(
        workspace_name=ws["name"],
        generated_at=today,
        models=ws.get("models", {}),
        decisions=decisions,
        findings_count=findings_count,
    ))
    # Always render the findings index page too — even when empty, so the
    # link from index.html resolves (empty-state copy lives in the template).
    render_workspace_findings_index(ws_root, today=today)
    return out


# ---------------------------------------------------------------------------
# Pass 10B: cross-study findings index
# ---------------------------------------------------------------------------


def _first_sentence(text: str, *, max_chars: int = 220) -> str:
    """One-liner preview — see :func:`discovery_engine.text_utils.first_sentence`."""
    return first_sentence(text, max_chars=max_chars)


def _harvest_findings(ws_root: Path) -> list[dict]:
    """Walk every ``study.yaml`` and return one row per finding.

    Studies are enumerated layout-aware via
    :meth:`WorkspacePaths.iter_study_dirs`, so findings are harvested from both
    the nested investigation-centric layout
    (``investigations/<inv>/studies/<study>/``) and the legacy flat layout
    (``studies/<study>/``).

    Each row has the keys the template expects:
    ``study_slug``, ``study_link`` (str | None), ``id``, ``kind``, ``status``,
    ``statement``, ``statement_oneliner``, ``cites`` (list[str]),
    ``expert_doc`` (str | None).

    Rows with malformed findings (missing required keys, non-canonical
    enum values) are skipped — the linter is the right place to surface
    those, not the rendering pipeline.

    ``planned`` findings are kept (not dropped): they are design-phase
    target findings authored before a study runs. The renderer groups them
    into a separate "Planned / targets" section so they are never mingled
    with realized results.
    """
    realized_status = {"confirms", "partial", "contradicts", "novel"}
    valid_status = realized_status | {"planned"}
    valid_kind = {"biological", "computational", "methodological"}
    out: list[dict] = []
    for sd in WorkspacePaths.load(ws_root).iter_study_dirs():
        sy = sd / "study.yaml"
        if not sy.is_file():
            continue
        try:
            study = yaml.safe_load(sy.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        slug = study.get("name") or sd.name
        # Per-study report.html may not exist (no per-study reports today,
        # but link anyway when found — keeps the page useful as the
        # workspace grows).
        per_study_report = sd / "reports" / "index.html"
        # Relative path from reports/findings.html (one level under ws_root)
        # to studies/<slug>/reports/index.html.
        study_link = (
            f"../studies/{slug}/reports/index.html"
            if per_study_report.is_file() else None
        )
        for f in (study.get("findings") or []):
            if not isinstance(f, dict):
                continue
            fid = f.get("id")
            kind = f.get("kind")
            status = f.get("status")
            statement = f.get("statement", "")
            if not (isinstance(fid, str) and isinstance(statement, str)
                    and kind in valid_kind and status in valid_status):
                continue
            ev = f.get("expected") or {}
            cites = ev.get("cites") if isinstance(ev, dict) else None
            cites = [c for c in cites if isinstance(c, str)] if isinstance(cites, list) else []
            xr = f.get("expert_reference") or {}
            expert_doc = xr.get("doc") if isinstance(xr, dict) else None
            if not isinstance(expert_doc, str):
                expert_doc = None
            out.append({
                "study_slug": slug,
                "study_link": study_link,
                "id": fid,
                "kind": kind,
                "status": status,
                "statement": statement,
                "statement_oneliner": _first_sentence(statement),
                "cites": cites,
                "expert_doc": expert_doc,
            })
    return out


def _count_workspace_findings(ws_root: Path) -> int:
    """Cheap count used for the dashboard link badge. Tolerant of missing dirs."""
    return len(_harvest_findings(ws_root))


def render_workspace_findings_index(
    ws_root: Path,
    *,
    today: str | None = None,
) -> Path:
    """Render ``<ws_root>/reports/findings.html`` — cross-study findings.

    Aggregates every ``findings[]`` entry across every ``studies/*/study.yaml``
    into a single flat list, then renders two pre-grouped views:

      - Group by ``status`` (default visible) — sorted within each group by
        kind then id, so the eye lands on similar findings first.
      - Group by ``kind`` (hidden until toggle) — same secondary sort.

    Filter chips at the top let the reader toggle individual status /
    kind values on or off; the in-page JS is a few lines, no framework.

    ``planned`` findings (design-phase targets) are rendered below the
    realized views in their own "Planned / targets" section, clearly
    labelled as not-yet-realized and excluded from the realized grouping
    and filter chips.

    Empty workspaces (no findings at all) render a friendly empty-state
    panel pointing at ``/viva-study findings <slug>``.

    Linked from ``reports/index.html`` via the "Findings index" panel.
    """
    today = today or date.today().isoformat()
    ws = yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8"))
    findings = _harvest_findings(ws_root)

    # Split realized results from design-phase targets. Realized findings keep
    # the existing group-by-status / group-by-kind views; planned findings are
    # rendered in a separate "Planned / targets" section so a not-yet-realized
    # target is never shown as a result.
    realized = [f for f in findings if f["status"] != "planned"]
    planned_findings = [f for f in findings if f["status"] == "planned"]
    planned_findings.sort(key=lambda r: (r["study_slug"], r["kind"], r["id"]))

    by_status: dict[str, list[dict]] = defaultdict(list)
    by_kind: dict[str, list[dict]] = defaultdict(list)
    # Sort each group: secondary by kind (or status), tertiary by id.
    for f in realized:
        by_status[f["status"]].append(f)
        by_kind[f["kind"]].append(f)
    for rows in by_status.values():
        rows.sort(key=lambda r: (r["kind"], r["id"]))
    for rows in by_kind.values():
        rows.sort(key=lambda r: (r["status"], r["id"]))

    env = _env(resource_dir("templates") / "workspace" / "reports")
    tpl = env.get_template("findings_index.html.j2")
    reports = WorkspacePaths.load(ws_root).reports
    out = reports / "findings.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    _copy_assets(reports / "assets")
    out.write_text(tpl.render(
        workspace_name=ws.get("name", ws_root.name),
        generated_at=today,
        findings=findings,
        realized=realized,
        planned_findings=planned_findings,
        by_status=dict(by_status),
        by_kind=dict(by_kind),
    ))
    return out
