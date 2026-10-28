"""Tests for the S3 narrative-spine completeness check in report_linter.

The check aggregates missing v4 narrative-spine sections into ONE info-level
finding per study, so the rendered report shows
"narrative incomplete: N sections missing" rather than 14 separate findings.

Covers:
- A fully-populated v4 spec produces zero narrative-spine findings.
- A bare v3 spec (no v4 fields) produces one finding listing all missing
  sections.
- v3 fallbacks satisfy their canonical v4 counterparts (a study with
  `purpose.question` is not flagged for missing `question`; a study with
  `baseline` is not flagged for missing `conditions`; a study with
  `expected_behavior` is not flagged for missing `behavior_tests`; a study
  with `observables` is not flagged for missing `readouts`).
- Empty values count as missing (e.g. `report: {}` is treated as absent).
- The finding's severity is `info` (does not block publication).
- The finding aggregates star + non-star sections separately in the
  message so the user sees which to prioritize.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from discovery_engine.report_linter import (
    _check_narrative_spine_completeness,
    _LintContext,
    has_blocking_errors,
    lint_workspace_report,
)


def _ws(tmp_path: Path, spec: dict) -> Path:
    """Build a minimal workspace with one study containing `spec`."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: test\n")
    sd = ws / "studies" / "s1"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump(spec))
    return ws


def _narrative_findings(ws: Path) -> list:
    return [
        f for f in lint_workspace_report(ws)
        if f.check == "narrative_spine_completeness"
    ]


# ---------------------------------------------------------------------------
# Fully-populated v4 spec — zero findings
# ---------------------------------------------------------------------------


def _full_v4_spec() -> dict:
    return {
        "schema_version": 4,
        "name": "s1",
        "baseline": [{"name": "b", "composite": "pkg.composites.foo"}],
        "runtime": {"subprocess_timeout_s": 600},
        "report": {"verdict": "passing", "confidence": "high"},
        "study_card": {"goal": "test"},
        "question": "Does X happen?",
        "assumptions": [{"text": "y"}],
        "conditions": {"baseline": {"composite": "pkg.composites.foo"}},
        "enforced_params": {"k": 1},
        "behavior_tests": [{"name": "t", "measure": {"kind": "x"}, "pass_if": {"op": "y"}}],
        "readouts": [{"name": "r", "store_path": "a.b"}],
        "biological_summary": "DnaA cycles...",
        "literature_anchors": [{"expectation": "x", "model_observable": "y"}],
        "model_change": {"base_model": "pkg.foo"},
        "implementation_requirements": [{"id": "r-1", "title": "t"}],
        "design_pivot_required": [{"id": "P-1", "question": "A or B?"}],
        "conclusion_verdicts": {
            "regression_compatibility": {"result": "PASS", "basis": "ok"},
        },
    }


def test_full_v4_spec_produces_no_findings(tmp_path):
    ws = _ws(tmp_path, _full_v4_spec())
    assert _narrative_findings(ws) == []


# ---------------------------------------------------------------------------
# Bare v3 spec — one aggregated finding listing all missing sections
# ---------------------------------------------------------------------------


def test_bare_v3_spec_flags_all_v4_only_sections(tmp_path):
    ws = _ws(tmp_path, {
        "schema_version": 3,
        "name": "s1",
        "baseline": [{"name": "b", "composite": "pkg.composites.foo"}],
        # Has v3 question + behavior_tests + baseline → those don't flag
        "question": "Does X happen?",
        "behavior_tests": [
            {"name": "t", "measure": {"kind": "x"}, "pass_if": {"op": "y"}}
        ],
        "readouts": [{"name": "r", "store_path": "a.b"}],
    })
    findings = _narrative_findings(ws)
    assert len(findings) == 1
    f = findings[0]
    assert f.level == "info"
    assert f.check == "narrative_spine_completeness"
    # The 4 ★ sections that are v4-only: report, study_card, conclusion_verdicts.
    # (question/behavior_tests/readouts/conditions all have v3 fallbacks.)
    assert "★ report, study_card, conclusion_verdicts" in f.message
    # The other v4-only sections show up under `· other:`.
    for marker in ("runtime", "biological_summary", "literature_anchors",
                   "model_change", "implementation_requirements",
                   "design_pivot_required", "enforced_params"):
        assert marker in f.message
    # Info-level findings are not blocking.
    assert not has_blocking_errors(findings)


# ---------------------------------------------------------------------------
# v3 fallbacks satisfy their v4 canonical counterparts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("v3_field,v3_payload,canonical_field", [
    ("purpose", {"question": "Does X?"}, "question"),
    ("baseline", [{"name": "b", "composite": "pkg.composites.foo"}], "conditions"),
    ("expected_behavior", [{"name": "t", "en": "x"}], "behavior_tests"),
    ("observables", [{"name": "r", "store_path": "a.b"}], "readouts"),
    ("key_assumptions", ["foo"], "assumptions"),
])
def test_v3_fallback_satisfies_canonical(tmp_path, v3_field, v3_payload, canonical_field):
    """A study with the v3 fallback field but not the canonical v4 field
    should NOT be flagged for missing the canonical field."""
    spec = {
        "schema_version": 3,
        "name": "s1",
        "baseline": [{"name": "b", "composite": "pkg.composites.foo"}],
        v3_field: v3_payload,
    }
    ws = _ws(tmp_path, spec)
    findings = _narrative_findings(ws)
    if not findings:
        return  # full satisfaction is also fine
    msg = findings[0].message
    assert canonical_field not in msg, (
        f"canonical_field {canonical_field!r} should be satisfied by v3 "
        f"fallback {v3_field!r}, but lint message still flags it: {msg!r}"
    )


# ---------------------------------------------------------------------------
# Empty values count as missing
# ---------------------------------------------------------------------------


def test_empty_report_counts_as_missing(tmp_path):
    spec = _full_v4_spec()
    spec["report"] = {}
    ws = _ws(tmp_path, spec)
    findings = _narrative_findings(ws)
    assert len(findings) == 1
    assert "report" in findings[0].message


def test_empty_study_card_counts_as_missing(tmp_path):
    spec = _full_v4_spec()
    spec["study_card"] = {}
    ws = _ws(tmp_path, spec)
    findings = _narrative_findings(ws)
    assert len(findings) == 1
    assert "study_card" in findings[0].message


def test_empty_list_counts_as_missing(tmp_path):
    spec = _full_v4_spec()
    spec["literature_anchors"] = []
    ws = _ws(tmp_path, spec)
    findings = _narrative_findings(ws)
    assert len(findings) == 1
    assert "literature_anchors" in findings[0].message


# ---------------------------------------------------------------------------
# Aggregation message format
# ---------------------------------------------------------------------------


def test_message_aggregates_count_and_lists_both_tiers(tmp_path):
    spec = {
        "schema_version": 4,
        "name": "s1",
        "baseline": [{"name": "b", "composite": "pkg.composites.foo"}],
        # Star satisfied: question, baseline (covers conditions), one
        # behavior_test, one readout. Missing star: report, study_card,
        # conclusion_verdicts.
        "question": "Does X?",
        "behavior_tests": [
            {"name": "t", "measure": {"kind": "x"}, "pass_if": {"op": "y"}}
        ],
        "readouts": [{"name": "r", "store_path": "a.b"}],
    }
    ws = _ws(tmp_path, spec)
    findings = _narrative_findings(ws)
    assert len(findings) == 1
    msg = findings[0].message
    assert msg.startswith("narrative incomplete: ")
    assert "★ " in msg
    assert "· other:" in msg
    assert "/viva-study fill-overview" in msg


# ---------------------------------------------------------------------------
# Check function in isolation
# ---------------------------------------------------------------------------


def test_check_in_isolation_skips_workspace_pseudo_slug(tmp_path):
    """The aggregate iterator may include a `<workspace>` pseudo-slug; the
    check should skip it cleanly."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: test\n")
    ctx = _LintContext(ws_root=ws, slug="<workspace>", spec={"name": "x"})
    _check_narrative_spine_completeness(ctx)
    assert ctx.findings == []
