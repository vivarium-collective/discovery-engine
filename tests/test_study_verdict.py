"""Tests for discovery_engine.study_verdict (Task 1 + Task 2).

Covers:
- roll_up_verdict: all-pass, any-fail, partial, not-started, blocked
- The passed predicate EXACTLY matches server.py _condition_satisfied
- write_gate_evaluator: parallel slot write, never-clobber, idempotent,
  diverges_from_authored flag, comment preservation
"""
from __future__ import annotations

from pathlib import Path

import pytest

from discovery_engine import study_io
from discovery_engine.study_verdict import (
    diverges_from_authored,
    roll_up_verdict,
    write_gate_evaluator,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _spec(tests: list[dict], outcomes: dict | None = None, *,
          has_runs: bool = True) -> dict:
    """Build a minimal study spec with behavior_tests and one run."""
    runs = []
    if has_runs:
        run: dict = {"name": "r1", "status": "completed"}
        if outcomes is not None:
            run["outcomes"] = outcomes
        runs = [run]
    return {"name": "s", "behavior_tests": tests, "runs": runs}


def _named(*names: str) -> list[dict]:
    return [{"name": n} for n in names]


# ---------------------------------------------------------------------------
# Task 1: roll_up_verdict
# ---------------------------------------------------------------------------

class TestRollUpVerdictPassed:
    def test_all_pass_returns_passed(self):
        spec = _spec(
            _named("t1", "t2"),
            {"t1": {"result": "PASS"}, "t2": {"result": "pass"}},
        )
        v = roll_up_verdict(spec)
        assert v["result"] == "passed"
        assert v["blocked_by"] == []
        assert v["evaluated_by"] == "code"

    def test_one_pass_returns_passed(self):
        spec = _spec(_named("t1"), {"t1": {"result": "PASS"}})
        assert roll_up_verdict(spec)["result"] == "passed"

    def test_pass_predicate_matches_server_condition_satisfied(self):
        """The passed rule MUST equal: fail==0 and pass>0 (server.py:9561)."""
        from discovery_engine import study_status
        spec = _spec(_named("t1", "t2"),
                     {"t1": {"result": "PASS"}, "t2": {"result": "ok"}})
        counts = study_status.count_test_outcomes(spec, spec.get("runs"))
        assert counts["fail"] == 0 and counts["pass"] > 0, "server predicate must hold"
        assert roll_up_verdict(spec)["result"] == "passed"


class TestRollUpVerdictFailed:
    def test_any_fail_returns_failed(self):
        spec = _spec(
            _named("t1", "t2"),
            {"t1": {"result": "PASS"}, "t2": {"result": "FAIL"}},
        )
        v = roll_up_verdict(spec)
        assert v["result"] == "failed"
        assert "t2" in v["blocked_by"]

    def test_fail_includes_pending_in_blocked_by(self):
        spec = _spec(
            _named("t1", "t2", "t3"),
            {"t1": {"result": "FAIL"}},  # t2, t3 are pending
        )
        v = roll_up_verdict(spec)
        assert v["result"] == "failed"
        assert "t1" in v["blocked_by"]
        assert "t2" in v["blocked_by"]
        assert "t3" in v["blocked_by"]

    def test_fail_beats_partial(self):
        spec = _spec(
            _named("t1", "t2"),
            {"t1": {"result": "FAIL"}, "t2": {"result": "partial"}},
        )
        assert roll_up_verdict(spec)["result"] == "failed"


class TestRollUpVerdictNeedsCalibration:
    def test_partial_no_fail_returns_needs_calibration(self):
        spec = _spec(
            _named("t1", "t2"),
            {"t1": {"result": "PASS"}, "t2": {"result": "partial"}},
        )
        assert roll_up_verdict(spec)["result"] == "needs_calibration"

    def test_skip_no_fail_returns_needs_calibration(self):
        spec = _spec(_named("t1"), {"t1": {"result": "skip"}})
        assert roll_up_verdict(spec)["result"] == "needs_calibration"


class TestRollUpVerdictNotStarted:
    def test_no_runs_returns_not_started(self):
        spec = _spec(_named("t1"), has_runs=False)
        assert roll_up_verdict(spec)["result"] == "not_started"

    def test_no_tests_returns_not_started(self):
        spec = {"name": "s", "behavior_tests": [], "runs": []}
        assert roll_up_verdict(spec)["result"] == "not_started"

    def test_empty_spec_returns_not_started(self):
        assert roll_up_verdict({})["result"] == "not_started"


class TestRollUpVerdictBlocked:
    def test_has_runs_but_all_pending_returns_blocked(self):
        # Has a run but no outcomes recorded → all tests pending
        spec = _spec(_named("t1", "t2"), {})  # empty outcomes
        v = roll_up_verdict(spec)
        assert v["result"] == "blocked"
        assert "t1" in v["blocked_by"]
        assert "t2" in v["blocked_by"]


# ---------------------------------------------------------------------------
# Public helper: diverges_from_authored (pure, reusable by the spine)
# ---------------------------------------------------------------------------

class TestDivergesFromAuthored:
    def test_diverges_when_authored_disagrees(self):
        spec = _spec(_named("t1"), {"t1": {"result": "FAIL"}})
        spec["gate_status"] = "passed"
        assert diverges_from_authored(spec) is True

    def test_agrees_when_authored_matches(self):
        spec = _spec(_named("t1"), {"t1": {"result": "FAIL"}})
        spec["gate_status"] = "failed"
        assert diverges_from_authored(spec) is False

    def test_no_authored_gate_status_is_not_divergent(self):
        spec = _spec(_named("t1"), {"t1": {"result": "PASS"}})
        assert diverges_from_authored(spec) is False

    def test_unrecognised_gate_status_is_not_divergent(self):
        spec = _spec(_named("t1"), {"t1": {"result": "PASS"}})
        spec["gate_status"] = "whatever"
        assert diverges_from_authored(spec) is False

    def test_tolerant_of_empty_spec(self):
        assert diverges_from_authored({}) is False
        assert diverges_from_authored(None) is False

    def test_matches_write_gate_evaluator(self, tmp_path: Path):
        # The helper value equals what write_gate_evaluator persists.
        d = _study_dir(tmp_path, COMMENT_YAML)  # gate_status=passed, t1=FAIL
        write_gate_evaluator(d)
        spec = study_io.load_yaml_mapping(d / "study.yaml")
        persisted = spec["pipeline_gate"]["gate_evaluator"]["diverges_from_authored"]
        assert diverges_from_authored(spec) == persisted is True


# ---------------------------------------------------------------------------
# Task 2: write_gate_evaluator
# ---------------------------------------------------------------------------

def _study_dir(tmp_path: Path, raw_yaml: str) -> Path:
    d = tmp_path / "s1"
    d.mkdir()
    (d / "study.yaml").write_text(raw_yaml)
    return d


COMMENT_YAML = """\
# top-level comment preserved
name: my-study
gate_status: passed  # authored gate status — must NOT be changed

pipeline_gate:
  prerequisites:
    - study: prior
      condition: tests-passed  # inline comment must survive
behavior_tests:
  - name: t1
  - name: t2
runs:
  - name: r1  # authored run comment
    status: completed
    outcomes:
      t1:
        result: FAIL  # authored FAIL — must survive
      t2:
        result: PASS
# end comment
"""


class TestWriteGateEvaluator:
    def test_writes_gate_evaluator_slot(self, tmp_path: Path):
        d = _study_dir(tmp_path, COMMENT_YAML)
        changed = write_gate_evaluator(d)
        assert changed is True
        spec = study_io.load_yaml_mapping(d / "study.yaml")
        ge = spec["pipeline_gate"]["gate_evaluator"]
        assert ge["result"] == "failed"
        assert ge["evaluated_by"] == "code"
        assert "diverges_from_authored" in ge

    def test_never_touches_gate_status(self, tmp_path: Path):
        d = _study_dir(tmp_path, COMMENT_YAML)
        write_gate_evaluator(d)
        spec = study_io.load_yaml_mapping(d / "study.yaml")
        # authored gate_status must be unchanged
        assert spec.get("gate_status") == "passed"

    def test_never_touches_authored_outcomes(self, tmp_path: Path):
        d = _study_dir(tmp_path, COMMENT_YAML)
        write_gate_evaluator(d)
        spec = study_io.load_yaml_mapping(d / "study.yaml")
        run = spec["runs"][0]
        assert run["outcomes"]["t1"]["result"] == "FAIL"
        assert run["outcomes"]["t2"]["result"] == "PASS"

    def test_preserves_yaml_comments(self, tmp_path: Path):
        d = _study_dir(tmp_path, COMMENT_YAML)
        write_gate_evaluator(d)
        text = (d / "study.yaml").read_text()
        assert "# top-level comment preserved" in text
        assert "# authored gate status — must NOT be changed" in text
        assert "# inline comment must survive" in text
        assert "# authored run comment" in text
        assert "# authored FAIL — must survive" in text
        assert "# end comment" in text

    def test_idempotent_returns_false_on_no_change(self, tmp_path: Path):
        d = _study_dir(tmp_path, COMMENT_YAML)
        assert write_gate_evaluator(d) is True
        assert write_gate_evaluator(d) is False
        assert write_gate_evaluator(d) is False

    def test_idempotent_file_unchanged(self, tmp_path: Path):
        d = _study_dir(tmp_path, COMMENT_YAML)
        write_gate_evaluator(d)
        text_after_first = (d / "study.yaml").read_text()
        write_gate_evaluator(d)
        assert (d / "study.yaml").read_text() == text_after_first

    def test_diverges_from_authored_when_gate_status_disagrees(self, tmp_path: Path):
        """gate_status=passed but computed=failed → diverges_from_authored=True."""
        d = _study_dir(tmp_path, COMMENT_YAML)  # gate_status=passed, but t1=FAIL
        write_gate_evaluator(d)
        spec = study_io.load_yaml_mapping(d / "study.yaml")
        assert spec["pipeline_gate"]["gate_evaluator"]["diverges_from_authored"] is True

    def test_diverges_false_when_gate_status_agrees(self, tmp_path: Path):
        yaml_text = """\
name: agree
gate_status: failed
behavior_tests:
  - name: t1
runs:
  - name: r1
    status: completed
    outcomes:
      t1: {result: FAIL}
"""
        d = _study_dir(tmp_path, yaml_text)
        write_gate_evaluator(d)
        spec = study_io.load_yaml_mapping(d / "study.yaml")
        assert spec["pipeline_gate"]["gate_evaluator"]["diverges_from_authored"] is False

    def test_diverges_false_when_no_authored_gate_status(self, tmp_path: Path):
        yaml_text = """\
name: no-gate
behavior_tests:
  - name: t1
runs:
  - name: r1
    status: completed
    outcomes:
      t1: {result: PASS}
"""
        d = _study_dir(tmp_path, yaml_text)
        write_gate_evaluator(d)
        spec = study_io.load_yaml_mapping(d / "study.yaml")
        assert spec["pipeline_gate"]["gate_evaluator"]["diverges_from_authored"] is False

    def test_creates_pipeline_gate_if_absent(self, tmp_path: Path):
        yaml_text = """\
name: no-pg
behavior_tests:
  - name: t1
runs:
  - name: r1
    status: completed
    outcomes:
      t1: {result: PASS}
"""
        d = _study_dir(tmp_path, yaml_text)
        write_gate_evaluator(d)
        spec = study_io.load_yaml_mapping(d / "study.yaml")
        assert "pipeline_gate" in spec
        assert spec["pipeline_gate"]["gate_evaluator"]["result"] == "passed"

    def test_blocked_by_contains_failing_tests(self, tmp_path: Path):
        yaml_text = """\
name: fail-study
behavior_tests:
  - name: alpha
  - name: beta
runs:
  - name: r1
    status: completed
    outcomes:
      alpha: {result: FAIL}
      beta: {result: PASS}
"""
        d = _study_dir(tmp_path, yaml_text)
        write_gate_evaluator(d)
        spec = study_io.load_yaml_mapping(d / "study.yaml")
        ge = spec["pipeline_gate"]["gate_evaluator"]
        assert "alpha" in ge["blocked_by"]
        assert "beta" not in ge["blocked_by"]


# ---------------------------------------------------------------------------
# Wave 3a — preregistration_status (critique #18)
# ---------------------------------------------------------------------------

from discovery_engine.study_verdict import preregistration_status


def test_preregistration_status_absent_block_degrades():
    res = preregistration_status({"name": "s"})
    assert res == {"preregistered": False,
                   "registered_before_run": None,
                   "criteria_match": None}
    assert preregistration_status(None)["preregistered"] is False


def test_preregistration_registered_before_run_true():
    spec = {
        "preregistered": {"registered_at": "2026-01-01"},
        "runs": [{"name": "r", "status": "complete", "timestamp": "2026-05-01"}],
    }
    res = preregistration_status(spec)
    assert res["preregistered"] is True
    assert res["registered_before_run"] is True


def test_preregistration_registered_after_run_false():
    spec = {
        "preregistered": {"registered_at": "2026-06-01"},
        "runs": [{"name": "r", "status": "complete", "started_at": "2026-05-01"}],
    }
    assert preregistration_status(spec)["registered_before_run"] is False


def test_preregistration_missing_timestamp_is_none():
    spec = {
        "preregistered": {"thresholds": {"t1": {"low": 1}}},  # no registered_at
        "runs": [{"name": "r", "status": "complete", "timestamp": "2026-05-01"}],
    }
    res = preregistration_status(spec)
    assert res["registered_before_run"] is None  # degrade — no registered_at


def test_preregistration_criteria_match():
    spec = {
        "preregistered": {"registered_at": "2026-01-01",
                          "thresholds": {"t1": {"low": 1}, "t2": {"high": 5}}},
        "behavior_tests": [{"name": "t1", "pass_if": {"low": 1}},
                           {"name": "t2", "pass_if": {"high": 5}}],
    }
    assert preregistration_status(spec)["criteria_match"] is True


def test_preregistration_criteria_mismatch():
    spec = {
        "preregistered": {"registered_at": "2026-01-01",
                          "thresholds": {"t1": {"low": 9}}},
        "behavior_tests": [{"name": "t1", "pass_if": {"low": 1}}],
    }
    assert preregistration_status(spec)["criteria_match"] is False


def test_derive_confidence_reconciles_rail_and_graph():
    """derive_confidence gives ONE canonical value so the rail dot, graph node,
    and study badge agree: a study with runs but no coded tests (computed verdict
    = not_started) falls back to the AUTHORED report.verdict the rail reads,
    instead of the stale lifecycle status."""
    from discovery_engine.study_verdict import derive_confidence
    # runs, no coded tests, authored passing verdict, status still "running":
    # the graph should read Accepted (matching the green rail), not Investigating.
    spec = {"status": "running", "runs": [{"name": "baseline", "status": "completed"}],
            "report": {"verdict": "passing-with-caveats"}}
    assert derive_confidence(spec) == "Accepted"
    # authored refuted -> Refuted
    assert derive_confidence({"report": {"verdict": "refuted"}}) == "Refuted"
    # nothing authored, still running -> Investigating; planned -> Planned
    assert derive_confidence({"status": "running"}) == "Investigating"
    assert derive_confidence({"status": "planned"}) == "Planned"
    # a real failing test outcome beats the authored verdict
    refuted = {"behavior_tests": [{"name": "T1"}],
               "runs": [{"name": "baseline", "outcomes": {"T1": "FAIL"}}],
               "report": {"verdict": "passing"}}
    assert derive_confidence(refuted) == "Refuted"
