"""The gap-report CI renderer (discovery_engine.integration.ci).

Turns the linter's --json report into GitHub Actions annotations + a markdown
summary + the gate exit code — the reusable surface the gap-report composite
Action (and viva-template) call.
"""
from __future__ import annotations


import yaml

from discovery_engine.integration import ci


def _report(findings, *, by_check=None, by_level=None, internal_errors=0, exit_code=0):
    return {
        "findings": findings,
        "summary": {
            "total": len(findings),
            "by_level": by_level or {},
            "by_check": by_check or {},
            "internal_errors": internal_errors,
        },
        "exit_code": exit_code,
    }


# ---------------------------------------------------------------------------
# annotations
# ---------------------------------------------------------------------------

def test_annotations_shape_and_file_from_study_slug():
    rep = _report([
        {"level": "warning", "check": "study_missing_tests", "study_slug": "s1",
         "field_path": "behavior_tests", "message": "no tests"},
    ])
    [line] = ci.annotations(rep)
    assert line.startswith("::warning")
    assert "file=workspace/studies/s1/study.yaml" in line
    assert "title=gap%3A study_missing_tests" in line   # ':' escaped in a property
    assert "s1 [behavior_tests]: no tests" in line


def test_annotations_level_mapping_and_warn_only_demotes_errors():
    rep = _report([
        {"level": "error", "check": "render_blocked", "study_slug": "s1", "message": "x"},
        {"level": "info", "check": "narrative_spine_completeness", "study_slug": "s1", "message": "y"},
    ])
    normal = ci.annotations(rep)
    assert normal[0].startswith("::error") and normal[1].startswith("::notice")
    warn = ci.annotations(rep, warn_only=True)
    assert warn[0].startswith("::warning")   # error demoted under warn_only


def test_annotations_escape_newlines_in_message():
    rep = _report([{"level": "warning", "check": "c", "study_slug": "s", "message": "a\nb"}])
    [line] = ci.annotations(rep)
    assert "%0A" in line and "\n" not in line.split("::", 2)[-1]


def test_annotations_no_file_for_workspace_level_finding():
    rep = _report([{"level": "warning", "check": "c", "study_slug": None, "message": "m"}])
    [line] = ci.annotations(rep)
    assert "file=" not in line
    assert "(workspace): m" in line


# ---------------------------------------------------------------------------
# markdown_summary
# ---------------------------------------------------------------------------

def test_markdown_summary_clean_is_a_checkmark():
    out = ci.markdown_summary(_report([]))
    assert "No gaps" in out and "✅" in out


def test_markdown_summary_tallies_and_flags_internal_errors():
    rep = _report(
        [{"level": "warning", "check": "study_claim_empty", "study_slug": "s1", "message": "m"}],
        by_level={"warning": 1}, by_check={"study_claim_empty": 1}, internal_errors=2,
    )
    out = ci.markdown_summary(rep)
    assert "1 finding(s)" in out
    assert "`study_claim_empty`" in out
    assert "2 crashed check(s)" in out   # internal errors surfaced prominently


# ---------------------------------------------------------------------------
# gate_exit_code
# ---------------------------------------------------------------------------

def test_gate_exit_code_warn_only_is_always_zero():
    assert ci.gate_exit_code(_report([], exit_code=1, internal_errors=3), warn_only=True) == 0


def test_gate_exit_code_blocks_on_errors_and_internal_errors():
    assert ci.gate_exit_code(_report([], exit_code=1)) == 1
    assert ci.gate_exit_code(_report([], exit_code=0)) == 0
    # a crashed check escalates to 1 even with exit_code 0 (blind spot)
    assert ci.gate_exit_code(_report([], exit_code=0, internal_errors=1)) == 1
    assert ci.gate_exit_code(_report([], exit_code=0, internal_errors=1),
                             fail_on_internal_errors=False) == 0


# ---------------------------------------------------------------------------
# main — end to end against a tiny workspace
# ---------------------------------------------------------------------------

def test_main_runs_against_a_workspace(tmp_path, capsys):
    ws = tmp_path / "ws"
    (ws).mkdir()
    (ws / "workspace.yaml").write_text(yaml.safe_dump({"name": "ws", "description": "t"}))
    sd = ws / "studies" / "s-gap"
    sd.mkdir(parents=True)
    # a study past Design with no tests -> study_missing_tests fires
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "name": "s-gap", "schema_version": 3, "status": "completed",
        "baseline": [{"name": "b", "composite": "pkg.x"}],
        "runs": [{"name": "r", "status": "completed"}],
    }))
    summary_file = tmp_path / "summary.md"
    code = ci.main(["--ws", str(ws), "--warn-only", "--annotate",
                    "--summary-file", str(summary_file)])
    out = capsys.readouterr().out
    assert code == 0                       # warn-only always 0
    assert "::warning" in out              # annotations emitted
    assert "gap report" in out.lower()     # markdown summary printed
    assert summary_file.is_file() and "finding(s)" in summary_file.read_text()
