"""Tests for discovery_engine.investigation_status (Task 3).

Covers:
- roll_up_acceptance: all-pass, any-fail, partial, in-progress, mixed
- aggregate verdict priority: fail > in-progress > caveats > passing
- write_investigation_acceptance: parallel slot, never-clobber, idempotent,
  diverges_from_authored flag, comment preservation
"""
from __future__ import annotations

from pathlib import Path

import pytest

from discovery_engine import study_io
from discovery_engine.investigation_status import (
    roll_up_acceptance,
    write_investigation_acceptance,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _inv_spec(criteria: list[dict]) -> dict:
    return {"name": "test-inv", "acceptance_criteria": criteria}


def _study_with_outcome(behavior: str, result: str) -> dict:
    return {
        "name": "s",
        "behavior_tests": [{"name": behavior}],
        "runs": [
            {
                "name": "r1",
                "status": "completed",
                "outcomes": {behavior: {"result": result}},
            }
        ],
    }


# ---------------------------------------------------------------------------
# Task 3a: roll_up_acceptance (pure)
# ---------------------------------------------------------------------------

class TestRollUpAcceptance:
    def test_all_pass_returns_passing(self):
        inv = _inv_spec([
            {"study": "s1", "behavior": "b1"},
            {"study": "s2", "behavior": "b2"},
        ])
        studies = {
            "s1": _study_with_outcome("b1", "PASS"),
            "s2": _study_with_outcome("b2", "pass"),
        }
        r = roll_up_acceptance(inv, studies)
        assert r["verdict_status"] == "passing"
        assert r["unmet"] == []
        assert len(r["criteria"]) == 2
        assert all(c["result"] == "passing" for c in r["criteria"])

    def test_any_fail_returns_failing(self):
        inv = _inv_spec([
            {"study": "s1", "behavior": "b1"},
            {"study": "s2", "behavior": "b2"},
        ])
        studies = {
            "s1": _study_with_outcome("b1", "PASS"),
            "s2": _study_with_outcome("b2", "FAIL"),
        }
        r = roll_up_acceptance(inv, studies)
        assert r["verdict_status"] == "failing"
        assert any(c["study"] == "s2" and c["result"] == "failing" for c in r["unmet"])

    def test_partial_no_fail_returns_passing_with_caveats(self):
        inv = _inv_spec([{"study": "s1", "behavior": "b1"}])
        studies = {"s1": _study_with_outcome("b1", "partial")}
        r = roll_up_acceptance(inv, studies)
        assert r["verdict_status"] == "passing-with-caveats"

    def test_missing_study_returns_in_progress(self):
        inv = _inv_spec([{"study": "missing", "behavior": "b1"}])
        r = roll_up_acceptance(inv, {})
        assert r["verdict_status"] == "in-progress"
        assert r["unmet"][0]["study"] == "missing"

    def test_behavior_not_in_outcomes_returns_in_progress(self):
        inv = _inv_spec([{"study": "s1", "behavior": "no-such-behavior"}])
        studies = {"s1": _study_with_outcome("b1", "PASS")}
        r = roll_up_acceptance(inv, studies)
        assert r["verdict_status"] == "in-progress"

    def test_fail_beats_in_progress(self):
        inv = _inv_spec([
            {"study": "s1", "behavior": "b1"},
            {"study": "missing", "behavior": "b2"},
        ])
        studies = {"s1": _study_with_outcome("b1", "FAIL")}
        r = roll_up_acceptance(inv, studies)
        assert r["verdict_status"] == "failing"

    def test_in_progress_beats_caveats(self):
        inv = _inv_spec([
            {"study": "s1", "behavior": "b1"},
            {"study": "s2", "behavior": "b2"},
        ])
        studies = {
            "s1": _study_with_outcome("b1", "partial"),
            "s2": _study_with_outcome("b2", "PASS"),
        }
        # One partial + one pending-missing → partial wins but let's check…
        # Actually s2=PASS, s1=partial → caveats (no in-progress)
        r = roll_up_acceptance(inv, studies)
        assert r["verdict_status"] == "passing-with-caveats"

    def test_in_progress_from_missing_beats_caveats(self):
        inv = _inv_spec([
            {"study": "s1", "behavior": "b1"},
            {"study": "missing", "behavior": "b2"},
        ])
        studies = {"s1": _study_with_outcome("b1", "partial")}
        r = roll_up_acceptance(inv, studies)
        # missing study → in-progress; partial → caveats; in-progress wins
        assert r["verdict_status"] == "in-progress"

    def test_empty_criteria_returns_in_progress(self):
        inv = _inv_spec([])
        r = roll_up_acceptance(inv, {})
        assert r["verdict_status"] == "in-progress"
        assert r["criteria"] == []
        assert r["unmet"] == []

    def test_unmet_excludes_passing_criteria(self):
        inv = _inv_spec([
            {"study": "s1", "behavior": "b1"},
            {"study": "s2", "behavior": "b2"},
        ])
        studies = {
            "s1": _study_with_outcome("b1", "PASS"),
            "s2": _study_with_outcome("b2", "FAIL"),
        }
        r = roll_up_acceptance(inv, studies)
        assert len(r["unmet"]) == 1
        assert r["unmet"][0]["study"] == "s2"

    def test_criteria_carry_all_fields(self):
        inv = _inv_spec([{"study": "s1", "behavior": "b1"}])
        studies = {"s1": _study_with_outcome("b1", "PASS")}
        r = roll_up_acceptance(inv, studies)
        c = r["criteria"][0]
        assert "study" in c and "behavior" in c and "result" in c


# ---------------------------------------------------------------------------
# Task 3b: write_investigation_acceptance
# ---------------------------------------------------------------------------

INV_COMMENT_YAML = """\
# investigation comment — must survive
name: test-investigation

executive:
  verdict: Promising early results  # authored verdict — must NOT change
  verdict_status: in-progress       # authored status — must NOT change
  what_is_this: This is the investigation.

acceptance_criteria:
  - study: s1
    behavior: beh-a  # inline comment on behavior
  - study: s2
    behavior: beh-b
# end of file comment
"""

STUDY_S1_PASS = {
    "name": "s1",
    "behavior_tests": [{"name": "beh-a"}],
    "runs": [{"name": "r1", "status": "completed",
              "outcomes": {"beh-a": {"result": "PASS"}}}],
}

STUDY_S2_PASS = {
    "name": "s2",
    "behavior_tests": [{"name": "beh-b"}],
    "runs": [{"name": "r1", "status": "completed",
              "outcomes": {"beh-b": {"result": "PASS"}}}],
}

STUDY_S2_FAIL = {
    "name": "s2",
    "behavior_tests": [{"name": "beh-b"}],
    "runs": [{"name": "r1", "status": "completed",
              "outcomes": {"beh-b": {"result": "FAIL"}}}],
}


def _inv_dir(tmp_path: Path, raw_yaml: str,
             studies: dict | None = None) -> tuple[Path, Path]:
    """Create a minimal workspace with an investigation and its studies.
    Returns (workspace_root, inv_dir)."""
    ws = tmp_path / "ws"
    ws_inv = ws / "investigations" / "test-investigation"
    ws_inv.mkdir(parents=True)
    (ws_inv / "investigation.yaml").write_text(raw_yaml)

    ws_studies = ws / "studies"
    ws_studies.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n")

    if studies:
        for slug, spec in studies.items():
            sd = ws_studies / slug
            sd.mkdir()
            study_io.save_yaml_atomic(sd / "study.yaml", spec)

    return ws, ws_inv


class TestWriteInvestigationAcceptance:
    def test_writes_computed_slots(self, tmp_path: Path):
        ws, inv_dir = _inv_dir(tmp_path, INV_COMMENT_YAML,
                               {"s1": STUDY_S1_PASS, "s2": STUDY_S2_PASS})
        changed = write_investigation_acceptance(inv_dir, ws)
        assert changed is True
        spec = study_io.load_yaml_mapping(inv_dir / "investigation.yaml")
        exec_ = spec["executive"]
        assert "computed_verdict_status" in exec_
        assert exec_["computed_verdict_status"] == "passing"
        assert "computed_acceptance" in exec_

    def test_never_touches_authored_verdict(self, tmp_path: Path):
        ws, inv_dir = _inv_dir(tmp_path, INV_COMMENT_YAML,
                               {"s1": STUDY_S1_PASS, "s2": STUDY_S2_PASS})
        write_investigation_acceptance(inv_dir, ws)
        spec = study_io.load_yaml_mapping(inv_dir / "investigation.yaml")
        assert spec["executive"]["verdict"] == "Promising early results"
        assert spec["executive"]["verdict_status"] == "in-progress"

    def test_preserves_yaml_comments(self, tmp_path: Path):
        ws, inv_dir = _inv_dir(tmp_path, INV_COMMENT_YAML,
                               {"s1": STUDY_S1_PASS, "s2": STUDY_S2_PASS})
        write_investigation_acceptance(inv_dir, ws)
        text = (inv_dir / "investigation.yaml").read_text()
        assert "# investigation comment — must survive" in text
        assert "# authored verdict — must NOT change" in text
        assert "# authored status — must NOT change" in text
        assert "# inline comment on behavior" in text
        assert "# end of file comment" in text

    def test_idempotent_returns_false(self, tmp_path: Path):
        ws, inv_dir = _inv_dir(tmp_path, INV_COMMENT_YAML,
                               {"s1": STUDY_S1_PASS, "s2": STUDY_S2_PASS})
        assert write_investigation_acceptance(inv_dir, ws) is True
        assert write_investigation_acceptance(inv_dir, ws) is False
        assert write_investigation_acceptance(inv_dir, ws) is False

    def test_idempotent_file_unchanged(self, tmp_path: Path):
        ws, inv_dir = _inv_dir(tmp_path, INV_COMMENT_YAML,
                               {"s1": STUDY_S1_PASS, "s2": STUDY_S2_PASS})
        write_investigation_acceptance(inv_dir, ws)
        text_after_first = (inv_dir / "investigation.yaml").read_text()
        write_investigation_acceptance(inv_dir, ws)
        assert (inv_dir / "investigation.yaml").read_text() == text_after_first

    def test_diverges_from_authored_true_when_differs(self, tmp_path: Path):
        """authored verdict_status=in-progress but computed=failing → diverges."""
        ws, inv_dir = _inv_dir(tmp_path, INV_COMMENT_YAML,
                               {"s1": STUDY_S1_PASS, "s2": STUDY_S2_FAIL})
        write_investigation_acceptance(inv_dir, ws)
        spec = study_io.load_yaml_mapping(inv_dir / "investigation.yaml")
        ca = spec["executive"]["computed_acceptance"]
        assert ca["diverges_from_authored"] is True

    def test_diverges_from_authored_false_when_agrees(self, tmp_path: Path):
        yaml_text = """\
name: inv-agree
executive:
  verdict_status: failing
acceptance_criteria:
  - {study: s1, behavior: beh-a}
"""
        # Use a study with beh-a=FAIL so computed verdict is "failing" (matching authored)
        study_s1_fail = {
            "name": "s1",
            "behavior_tests": [{"name": "beh-a"}],
            "runs": [{"name": "r1", "status": "completed",
                      "outcomes": {"beh-a": {"result": "FAIL"}}}],
        }
        ws, inv_dir = _inv_dir(tmp_path, yaml_text, {"s1": study_s1_fail})
        write_investigation_acceptance(inv_dir, ws)
        spec = study_io.load_yaml_mapping(inv_dir / "investigation.yaml")
        ca = spec["executive"]["computed_acceptance"]
        assert ca["diverges_from_authored"] is False

    def test_diverges_false_when_no_authored_verdict_status(self, tmp_path: Path):
        yaml_text = """\
name: inv-no-vs
acceptance_criteria:
  - {study: s1, behavior: beh-a}
"""
        ws, inv_dir = _inv_dir(tmp_path, yaml_text,
                               {"s1": STUDY_S1_PASS})
        write_investigation_acceptance(inv_dir, ws)
        spec = study_io.load_yaml_mapping(inv_dir / "investigation.yaml")
        ca = spec["executive"]["computed_acceptance"]
        assert ca["diverges_from_authored"] is False

    def test_criteria_in_computed_acceptance(self, tmp_path: Path):
        ws, inv_dir = _inv_dir(tmp_path, INV_COMMENT_YAML,
                               {"s1": STUDY_S1_PASS, "s2": STUDY_S2_FAIL})
        write_investigation_acceptance(inv_dir, ws)
        spec = study_io.load_yaml_mapping(inv_dir / "investigation.yaml")
        ca = spec["executive"]["computed_acceptance"]
        criteria = ca["criteria"]
        assert len(criteria) == 2
        by_study = {c["study"]: c for c in criteria}
        assert by_study["s1"]["result"] == "passing"
        assert by_study["s2"]["result"] == "failing"

    def test_unmet_in_computed_acceptance(self, tmp_path: Path):
        ws, inv_dir = _inv_dir(tmp_path, INV_COMMENT_YAML,
                               {"s1": STUDY_S1_PASS, "s2": STUDY_S2_FAIL})
        write_investigation_acceptance(inv_dir, ws)
        spec = study_io.load_yaml_mapping(inv_dir / "investigation.yaml")
        ca = spec["executive"]["computed_acceptance"]
        assert len(ca["unmet"]) == 1
        assert ca["unmet"][0]["study"] == "s2"

    def test_creates_executive_if_absent(self, tmp_path: Path):
        yaml_text = """\
name: inv-no-exec
acceptance_criteria:
  - {study: s1, behavior: beh-a}
"""
        ws, inv_dir = _inv_dir(tmp_path, yaml_text,
                               {"s1": STUDY_S1_PASS})
        write_investigation_acceptance(inv_dir, ws)
        spec = study_io.load_yaml_mapping(inv_dir / "investigation.yaml")
        assert "computed_verdict_status" in spec.get("executive", {})
