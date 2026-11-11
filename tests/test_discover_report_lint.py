"""CLI wiring tests for `discover report` and `discover lint`.

These assert the subcommands delegate faithfully to
``discovery_engine.reporting`` — they do NOT re-test the linter/renderer
logic (that lives in test_report_linter.py / test_report*.py). The point is
that the engine-native CLI reproduces the workbench's /api/render +
/api/report-lint surface with no server running.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from discovery_engine import discover
from discovery_engine.reporting.linter import (
    apply_overrides,
    lint_workspace_report,
    load_overrides,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "lint-cases"


def _ws(tmp_path: Path) -> tuple[Path, list[str]]:
    dest = tmp_path / "ws"
    shutil.copytree(FIXTURES / "clean-baseline", dest)
    return dest, ["--workspace", str(dest)]


def test_discover_lint_json_matches_linter_and_is_clean(tmp_path, capsys):
    ws, argv = _ws(tmp_path)

    rc = discover.main([*argv, "lint", "--json"])
    assert rc == 0  # clean baseline has no blocking errors

    printed = json.loads(capsys.readouterr().out)
    expected = [
        f.to_dict()
        for f in apply_overrides(lint_workspace_report(ws), load_overrides(ws))
    ]
    assert printed == expected  # CLI output == direct linter call, byte-for-byte


def test_discover_report_renders_index_html(tmp_path, capsys):
    ws, argv = _ws(tmp_path)

    rc = discover.main([*argv, "report"])
    assert rc == 0

    out = ws / "reports" / "index.html"
    assert out.is_file()
    assert "rendered workspace report" in capsys.readouterr().out


def test_discover_report_no_lint_skips_gate(tmp_path):
    ws, argv = _ws(tmp_path)
    # --no-lint must still render the workspace report (gate bypassed).
    assert discover.main([*argv, "report", "--no-lint"]) == 0
    assert (ws / "reports" / "index.html").is_file()


# ---------------------------------------------------------------------------
# Blocking path (a workspace with error-level findings) — exit codes + --force
# ---------------------------------------------------------------------------


def _blocking_ws(tmp_path: Path) -> tuple[Path, list[str]]:
    dest = tmp_path / "ws"
    shutil.copytree(FIXTURES / "finding-no-evidence", dest)  # 2 error-level findings
    return dest, ["--workspace", str(dest)]


def test_discover_lint_exits_1_on_blocking_then_0_with_force(tmp_path):
    ws, argv = _blocking_ws(tmp_path)
    assert discover.main([*argv, "lint"]) == 1  # blocking errors, no overrides
    assert discover.main([*argv, "lint", "--force"]) == 0  # overrides logged
    assert (ws / ".pbg" / "report-lint-overrides.json").is_file()
    assert discover.main([*argv, "lint"]) == 0  # now clean: overrides applied


def test_discover_report_refuses_then_renders_with_force(tmp_path):
    ws, argv = _blocking_ws(tmp_path)
    # refuses to render while blocking errors are un-overridden
    assert discover.main([*argv, "report"]) == 1
    assert not (ws / "reports" / "index.html").exists()
    # --force logs overrides and renders
    assert discover.main([*argv, "report", "--force"]) == 0
    assert (ws / "reports" / "index.html").is_file()
