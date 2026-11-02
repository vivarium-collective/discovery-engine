"""Tests for the composed workspace-lint entrypoint (discovery_engine.workspace_lint).

Covers the composition seam: shared report-linter findings + workspace-local
``CHECKS`` (string-or-LintFinding, raising checks non-fatal) + the str->warning
coercion with a stable override_key. Reuses the report-linter's clean-baseline
fixture so the shared pass contributes no spurious findings.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from discovery_engine.report_linter import LintFinding
from discovery_engine.workspace_lint import (
    _coerce,
    lint_workspace,
    load_workspace_lint_checks,
)

FIXTURES = Path(__file__).parent / "fixtures" / "lint-cases"


def _clean_ws(dest: Path) -> Path:
    shutil.copytree(FIXTURES / "clean-baseline", dest)
    return dest


def test_local_checks_composed_with_shared(tmp_path):
    ws = _clean_ws(tmp_path / "ws")

    def chk_str(ws_root):
        return ["a workspace-specific rule tripped"]

    def chk_find(ws_root):
        return [LintFinding(level="error", study_slug="s", field_path="x",
                            message="hard local error", override_key="k", check="local")]

    def chk_boom(ws_root):
        raise RuntimeError("kaboom")

    findings = lint_workspace(ws, local_checks=[chk_str, chk_find, chk_boom])
    msgs = [f.message for f in findings]

    # string -> warning
    assert any(m == "a workspace-specific rule tripped" for m in msgs)
    assert any(f.message == "a workspace-specific rule tripped" and f.level == "warning"
               for f in findings)
    # LintFinding passes through with its level
    assert any(f.message == "hard local error" and f.level == "error" for f in findings)
    # a raising check is surfaced, not fatal
    assert any("kaboom" in m for m in msgs)


def test_str_coerced_to_stable_warning():
    a = _coerce("same message")
    b = _coerce("same message")
    assert a.level == "warning"
    assert a.study_slug == "<workspace>"
    assert a.override_key == b.override_key  # stable across calls (overridable)
    assert _coerce("other").override_key != a.override_key


def test_lintfinding_passes_through_unchanged():
    f = LintFinding(level="info", study_slug="s", field_path="p",
                    message="m", override_key="ok", check="c")
    assert _coerce(f) is f


def test_missing_lint_checks_module_yields_no_local_checks(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("package_path: no_such_pkg_xyz\n")
    assert load_workspace_lint_checks(ws) == []


def test_none_ws_root_is_empty():
    assert load_workspace_lint_checks(None) == []
