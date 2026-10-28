"""Tests for the static baseline render-guarantee check.

A v3/v4 study whose baseline the workbench study-detail loader rejects (empty or
malformed) must be caught by `/viva-report` even when no server is running — so
"passes lint" implies "renders". This mirrors the workbench's
`_validate_study_v3_or_v4` baseline rules WITHOUT importing vivarium-workbench
(the `test_no_workbench_import` guard forbids that import in the plugin).

Direct `_LintContext` call style (as in test_report_linter_baseline_config_carry).
Synthetic fixtures only.
"""
from __future__ import annotations

from pathlib import Path

from discovery_engine.report_linter import (
    _CHECK_FUNCTIONS,
    _LintContext,
    _check_render_guarantee_baseline,
)


def _run(spec: dict, slug: str = "s1", ws_root: Path = Path(".")):
    ctx = _LintContext(ws_root=ws_root, slug=slug, spec=spec)
    _check_render_guarantee_baseline(ctx)
    return [f for f in ctx.findings if f.check == "render_blocked"]


def _ok_baseline():
    return [{"name": "b", "composite": "pkg.composites.x"}]


def test_check_is_registered():
    assert _check_render_guarantee_baseline in _CHECK_FUNCTIONS


def test_empty_v3_baseline_is_render_blocked():
    findings = _run({"schema_version": 3, "baseline": []})
    assert findings and findings[0].level == "error"
    assert findings[0].field_path == "baseline"


def test_absent_v3_baseline_is_render_blocked():
    findings = _run({"schema_version": 3})
    assert findings and findings[0].field_path == "baseline"


def test_baseline_entry_missing_name_and_ref():
    findings = _run({"schema_version": 3, "baseline": [{}]})
    paths = {f.field_path for f in findings}
    assert "baseline[0].name" in paths
    assert "baseline[0]" in paths  # requires composite/step/process


def test_valid_composite_baseline_passes():
    assert _run({"schema_version": 3, "baseline": _ok_baseline()}) == []


def test_valid_bare_process_baseline_passes():
    findings = _run({"schema_version": 3, "baseline": [{"name": "brain", "process": "CTRNNProcess"}]})
    assert findings == []


def test_v4_conditions_baseline_without_composite_is_blocked():
    findings = _run({"schema_version": 4, "conditions": {"baseline": {"params": {}}}})
    assert findings and findings[0].field_path == "conditions.baseline.composite"


def test_v4_conditions_baseline_with_composite_passes():
    findings = _run({"schema_version": 4, "conditions": {"baseline": {"composite": "pkg.composites.x"}}})
    assert findings == []


def test_v2_spec_is_skipped():
    # v2 studies use a different loader shape; the check must not fire on them.
    assert _run({"schema_version": 2, "baseline": []}) == []


def test_unversioned_spec_is_skipped():
    assert _run({"baseline": []}) == []


def test_workspace_pseudo_slug_is_skipped():
    assert _run({"schema_version": 3, "baseline": []}, slug="<workspace>") == []
