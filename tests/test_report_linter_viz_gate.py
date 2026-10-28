"""Every study must DECLARE at least one visualization, or the linter errors
(blocking, override-able).

On-disk figure HTML alone does NOT satisfy the rule — only a declared
``visualizations[]`` / ``embed_visualizations[]`` does — because the workbench's
Visualizations surface renders *declared* viz, not loose figure files.

Strengthened from the former completion-only gate
(``status_claims_done_no_visualizations``): the requirement now binds EVERY
study, not just ones that claim completion. A study still in early design opts
out via ``.pbg/report-lint-overrides.json`` (or ``/viva-report --force``), which
downgrades any error to a warning — the same escape hatch every other hard gate
uses.

Uses the direct ``_LintContext`` call style of the ``viz_stale`` tests —
``WorkspacePaths.load`` tolerates a missing workspace.yaml and falls back to flat
``studies/<slug>/`` under the tmp root.
"""
from __future__ import annotations

from discovery_engine.report_linter import (
    _LintContext,
    _check_missing_visualizations,
)

CHECK = "missing_visualizations"


def _findings(ws_root, slug, spec):
    ctx = _LintContext(ws_root=ws_root, slug=slug, spec=spec)
    _check_missing_visualizations(ctx)
    return [f for f in ctx.findings if f.check == CHECK]


def test_completed_study_without_declared_viz_is_a_blocking_error(tmp_path):
    found = _findings(tmp_path, "s1", {"status": "completed", "visualizations": []})
    assert len(found) == 1
    f = found[0]
    assert f.level == "error"
    assert f.field_path == "visualizations"
    assert "declare" in f.message.lower()


def test_in_design_study_without_viz_ALSO_errors(tmp_path):
    # Strengthened: the rule now binds EVERY study, not just completed ones.
    # (Formerly this was only a non-blocking warning for in-design studies.)
    found = _findings(tmp_path, "s1", {"status": "in-progress", "visualizations": []})
    assert len(found) == 1
    assert found[0].level == "error"


def test_study_with_no_status_at_all_errors(tmp_path):
    found = _findings(tmp_path, "s1", {"name": "s1"})
    assert len(found) == 1
    assert found[0].level == "error"


def test_declared_visualization_is_silent(tmp_path):
    spec = {"status": "completed",
            "visualizations": [{"name": "ladder", "address": "local:RecruitmentLadder"}]}
    assert _findings(tmp_path, "s1", spec) == []


def test_embed_visualization_is_silent(tmp_path):
    spec = {"gate_status": "passed",
            "embed_visualizations": [{"name": "x", "html_path": "viz/x.html"}]}
    assert _findings(tmp_path, "s1", spec) == []


def test_on_disk_figure_alone_does_NOT_satisfy_the_rule(tmp_path):
    # The crux of the strengthened rule: a loose figure file is NOT a declared
    # visualization — the workbench renders declared viz, so a study whose only
    # "viz" is an on-disk chart must still error. The message hints why.
    sd = tmp_path / "studies" / "s1" / "viz"
    sd.mkdir(parents=True)
    (sd / "mechanism-ladder.html").write_text("<div>chart</div>", encoding="utf-8")
    found = _findings(tmp_path, "s1", {"status": "completed"})
    assert len(found) == 1
    assert found[0].level == "error"
    assert "figure" in found[0].message.lower()


def test_workspace_pseudo_slug_is_skipped(tmp_path):
    assert _findings(tmp_path, "<workspace>", {"status": "completed"}) == []
