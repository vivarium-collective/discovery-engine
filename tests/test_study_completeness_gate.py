"""Study-completeness gate — rule 2 (completed-but-verdict-pending) + the family.

The gate detects three states of a study that LOOKS done but is internally
inconsistent, so an investigation should not read as complete over it:

  1. ``status_claims_done_no_runs_recorded`` — claims done, no runs recorded
     (pre-existing: ``checks_structure._check_status_claims_done_but_no_runs_recorded``).
  2. ``completed_but_verdict_pending`` — presents as completed but the computed
     verdict is still pending (has runs, every test pending → gate undecided).
     **This module's subject.**
  3. ``declared_composite_unresolved`` — declares a Model composite that does not
     resolve. Emitted publish-side in vivarium-workbench (which owns the composite
     registry/resolver); listed in the family constant so the id set is single-sourced.

Rule 2's live case: ``viva_simulation_core`` biogears-sbml-sepsis studies ran
(runs.db populated) but the study-run HTTP flush 500'd on numpy serialization
before grading the behaviour tests into ``outcomes`` — so ``canonical_outcomes``
is empty, the computed verdict is ``blocked``, and the gate can never pass even
though the study is marked complete.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from discovery_engine.reporting.linter import (
    STUDY_COMPLETENESS_CHECKS,
    lint_workspace_report,
)
from discovery_engine.reporting.linter.checks_structure import (
    _check_completed_but_verdict_pending,
)
from discovery_engine.reporting.linter.context import _LintContext
from discovery_engine.investigation.status import roll_up_acceptance


def _ctx(spec: dict) -> _LintContext:
    # roll_up_verdict reads only the spec, so ws_root is irrelevant here.
    return _LintContext(ws_root=Path("/nonexistent"), slug="s", spec=spec)


def _checks(ctx: _LintContext) -> list[str]:
    return [f.check for f in ctx.findings]


# ---------------------------------------------------------------------------
# Rule 2 — the check fires exactly on "completed, but verdict still pending"
# ---------------------------------------------------------------------------

def test_fires_when_completed_but_verdict_blocked():
    # Has runs but no graded outcomes → roll_up_verdict == "blocked"; status
    # claims completion → the study reads as done over an undecided gate.
    spec = {
        "status": "completed",
        "baseline": [{"name": "b1", "composite": "pkg.composites.x"}],
        "behavior_tests": [{"name": "t1"}],
        "runs": [{"name": "r1", "status": "completed"}],
    }
    ctx = _ctx(spec)
    _check_completed_but_verdict_pending(ctx)
    hits = [f for f in ctx.findings if f.check == "completed_but_verdict_pending"]
    assert len(hits) == 1
    assert hits[0].level == "warning"
    # the still-pending test name is surfaced in the message
    assert "t1" in hits[0].message


def test_fires_when_completion_claimed_via_evaluation_status():
    spec = {
        "evaluation_status": "evaluated",
        "behavior_tests": [{"name": "t1"}],
        "runs": [{"name": "r1", "status": "completed"}],
    }
    ctx = _ctx(spec)
    _check_completed_but_verdict_pending(ctx)
    assert "completed_but_verdict_pending" in _checks(ctx)


def test_does_not_fire_when_not_claiming_completion():
    # Same ran-but-pending shape, but the study does NOT claim completion — it is
    # legitimately mid-flight, so flagging it as a completeness problem is wrong.
    spec = {
        "status": "in-progress",
        "behavior_tests": [{"name": "t1"}],
        "runs": [{"name": "r1", "status": "completed"}],
    }
    ctx = _ctx(spec)
    _check_completed_but_verdict_pending(ctx)
    assert "completed_but_verdict_pending" not in _checks(ctx)


def test_does_not_fire_when_no_runs_recorded():
    # No runs → roll_up_verdict == "not_started"; that is rule 1's territory
    # (status_claims_done_no_runs_recorded), not rule 2 — avoid double-flagging.
    spec = {"status": "completed", "behavior_tests": [{"name": "t1"}]}
    ctx = _ctx(spec)
    _check_completed_but_verdict_pending(ctx)
    assert "completed_but_verdict_pending" not in _checks(ctx)


def test_does_not_fire_when_verdict_actually_passed():
    # Runs carry graded outcomes → verdict "passed"; the gate IS decided.
    spec = {
        "status": "completed",
        "behavior_tests": [{"name": "t1"}],
        "runs": [{"name": "r1", "status": "completed",
                  "outcomes": {"t1": {"result": "pass"}}}],
    }
    ctx = _ctx(spec)
    _check_completed_but_verdict_pending(ctx)
    assert "completed_but_verdict_pending" not in _checks(ctx)


# ---------------------------------------------------------------------------
# End-to-end through the linter + the family constant
# ---------------------------------------------------------------------------

def _write_ws(tmp_path: Path, study_spec: dict) -> Path:
    ws = tmp_path / "ws"
    (ws).mkdir(parents=True)
    (ws / "workspace.yaml").write_text(yaml.safe_dump({"name": "ws", "description": "t"}))
    sd = ws / "studies" / study_spec["name"]
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump(study_spec))
    return ws


def test_rule2_fires_end_to_end_via_lint_workspace_report(tmp_path):
    ws = _write_ws(tmp_path, {
        "name": "s-pending", "schema_version": 3, "status": "completed",
        "baseline": [{"name": "b1", "composite": "pkg.composites.x"}],
        "behavior_tests": [{"name": "t1"}],
        "runs": [{"name": "r1", "status": "completed"}],
    })
    findings = lint_workspace_report(ws)
    family = [f for f in findings if f.check in STUDY_COMPLETENESS_CHECKS]
    assert any(f.check == "completed_but_verdict_pending" for f in family)


def test_study_completeness_family_is_exported_and_complete():
    assert STUDY_COMPLETENESS_CHECKS == frozenset({
        "status_claims_done_no_runs_recorded",
        "completed_but_verdict_pending",
        "study_missing_tests",
        "study_claim_empty",
        "declared_composite_unresolved",
    })


def test_completed_but_pending_check_id_is_registered():
    from discovery_engine.reporting.linter import CHECKS
    assert "completed_but_verdict_pending" in CHECKS


# ---------------------------------------------------------------------------
# The gate's purpose: such a member forces the investigation OUT of "complete"
# ---------------------------------------------------------------------------

def test_completed_but_pending_member_keeps_investigation_out_of_passing():
    # The investigation's acceptance rollup must NOT read "passing" when a member
    # study it accepts on has run but graded no outcomes (verdict pending).
    inv_spec = {
        "name": "inv",
        "studies": ["s-pending"],
        "acceptance_criteria": [{"study": "s-pending", "behavior": "t1"}],
    }
    pending_study = {
        "name": "s-pending", "status": "completed",
        "behavior_tests": [{"name": "t1"}],
        "runs": [{"name": "r1", "status": "completed"}],  # no outcomes → pending
    }
    rolled = roll_up_acceptance(inv_spec, {"s-pending": pending_study})
    assert rolled["verdict_status"] != "passing"


# ---------------------------------------------------------------------------
# Robustness: a str ws_root must not crash checks into blind spots
# ---------------------------------------------------------------------------

def test_lint_workspace_report_coerces_str_ws_root(tmp_path):
    # Several checks do `ctx.ws_root / x` / `.iterdir()`; a str ws_root used to
    # raise TypeError/AttributeError, demoted to linter_internal_error — the check
    # silently did not run. lint must coerce so str and Path give the same result.
    from discovery_engine.reporting.linter import lint_workspace_report
    ws = _write_ws(tmp_path, {
        "name": "s1", "schema_version": 3, "status": "completed",
        "baseline": [{"name": "b1", "composite": "pkg.x"}],
        "behavior_tests": [{"name": "t1"}],
        "runs": [{"name": "r1", "status": "completed"}],
    })
    ie_str = [f for f in lint_workspace_report(str(ws)) if f.check == "linter_internal_error"]
    ie_path = [f for f in lint_workspace_report(ws) if f.check == "linter_internal_error"]
    assert ie_str == [] and ie_path == [], "str/Path ws_root must not crash checks"


# ---------------------------------------------------------------------------
# study_missing_tests + study_claim_empty — phase-gated coverage checks
# ---------------------------------------------------------------------------

def test_study_missing_tests_fires_only_past_design():
    from discovery_engine.reporting.linter.checks_structure import _check_study_missing_tests
    # ran, no tests -> fires
    c = _ctx({"status": "completed", "runs": [{"name": "r", "status": "completed"}]})
    _check_study_missing_tests(c)
    assert "study_missing_tests" in _checks(c)
    # Design-stage, no tests -> does NOT fire (legitimately sparse)
    c = _ctx({"status": "planned"})
    _check_study_missing_tests(c)
    assert "study_missing_tests" not in _checks(c)
    # has tests -> does NOT fire
    c = _ctx({"status": "completed", "behavior_tests": [{"name": "t1"}],
              "runs": [{"name": "r", "status": "completed"}]})
    _check_study_missing_tests(c)
    assert "study_missing_tests" not in _checks(c)


def test_study_claim_empty_fires_only_with_evidence():
    from discovery_engine.reporting.linter.checks_structure import _check_study_claim_empty
    # evidence (decided gate + findings), empty claim -> fires
    c = _ctx({"gate_status": "passed", "findings": [{"summary": "x"}]})
    _check_study_claim_empty(c)
    assert "study_claim_empty" in _checks(c)
    # planned, no evidence, empty claim -> does NOT fire
    c = _ctx({"status": "planned"})
    _check_study_claim_empty(c)
    assert "study_claim_empty" not in _checks(c)
    # claim authored -> does NOT fire
    c = _ctx({"gate_status": "passed", "claim": "We find X."})
    _check_study_claim_empty(c)
    assert "study_claim_empty" not in _checks(c)


def test_family_now_covers_five_checks():
    assert STUDY_COMPLETENESS_CHECKS == frozenset({
        "status_claims_done_no_runs_recorded",
        "completed_but_verdict_pending",
        "study_missing_tests",
        "study_claim_empty",
        "declared_composite_unresolved",
    })
    from discovery_engine.reporting.linter import CHECKS
    assert "study_missing_tests" in CHECKS and "study_claim_empty" in CHECKS


# ---------------------------------------------------------------------------
# build_json_report — the CI contract
# ---------------------------------------------------------------------------

def test_build_json_report_shape_and_internal_errors(tmp_path):
    from discovery_engine.reporting.linter import build_json_report, lint_workspace_report
    ws = _write_ws(tmp_path, {
        "name": "s1", "schema_version": 3, "status": "completed",
        "baseline": [{"name": "b1", "composite": "pkg.x"}],
        "runs": [{"name": "r1", "status": "completed"}],  # no tests -> a gap
    })
    rep = build_json_report(lint_workspace_report(ws), exit_code=1)
    assert set(rep) == {"findings", "summary", "exit_code"}
    assert rep["exit_code"] == 1
    s = rep["summary"]
    assert set(s) == {"total", "by_level", "by_check", "internal_errors"}
    assert s["total"] == len(rep["findings"])
    assert s["internal_errors"] == 0  # clean after the ws_root coercion
    # the missing-tests gap is surfaced in by_check
    assert s["by_check"].get("study_missing_tests", 0) >= 1
