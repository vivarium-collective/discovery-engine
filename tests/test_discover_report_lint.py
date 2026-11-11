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
from discovery_engine.study.band_provenance import bands_missing_provenance
from discovery_engine.study.io import load_yaml_mapping

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


# ---------------------------------------------------------------------------
# band-provenance — list acceptance bands with no cites
# ---------------------------------------------------------------------------


def test_discover_band_provenance_json_matches_primitive(tmp_path, capsys):
    dest = tmp_path / "ws"
    shutil.copytree(FIXTURES / "band-test-missing-cites", dest)
    argv = ["--workspace", str(dest)]

    rc = discover.main([*argv, "band-provenance", "study-band-no-cites", "--json"])
    assert rc == 0
    printed = json.loads(capsys.readouterr().out)
    expected = bands_missing_provenance(
        load_yaml_mapping(dest / "studies" / "study-band-no-cites" / "study.yaml")
    )
    assert printed == expected
    assert len(printed) == 4


def test_discover_band_provenance_clean_study_reports_no_gaps(tmp_path, capsys):
    dest = tmp_path / "ws"
    shutil.copytree(FIXTURES / "band-test-missing-cites", dest)
    rc = discover.main(["--workspace", str(dest), "band-provenance", "study-band-with-cites"])
    assert rc == 0
    assert "no provenance gaps" in capsys.readouterr().out


# ---------------------------------------------------------------------------
# populate-observations + readout-migrate — delegate to the module main()s
# ---------------------------------------------------------------------------


def test_discover_populate_observations_runs_per_study_and_all(tmp_path):
    ws, argv = _ws(tmp_path)  # clean-baseline, study "study-clean"
    assert discover.main([*argv, "populate-observations", "--study", "study-clean"]) == 0
    assert discover.main([*argv, "populate-observations", "--all"]) == 0


def test_discover_readout_migrate_dry_run_then_write(tmp_path):
    ws, argv = _ws(tmp_path)
    # dry-run (default) must not touch study.yaml
    before = (ws / "studies" / "study-clean" / "study.yaml").read_text(encoding="utf-8")
    assert discover.main([*argv, "readout-migrate", "study-clean"]) == 0
    after = (ws / "studies" / "study-clean" / "study.yaml").read_text(encoding="utf-8")
    assert before == after
    # --write is accepted (clean study has nothing to migrate -> still 0)
    assert discover.main([*argv, "readout-migrate", "study-clean", "--write"]) == 0
