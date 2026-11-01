"""Phase 2 of the unified graded-test ontology: the freshness / invalidation contract.

Every computed grade carries a `_grader` stamp rich enough to know when it has gone stale:
  - `version`      — GRADER_VERSION (exists since #22)
  - `graded_at`    — ISO time the grade was computed (idempotent: unchanged when inputs_hash matches)
  - `inputs_hash`  — hash of (test defs + run provenance + evaluator-registry fingerprint)
  - `store`        — THREE-valued now: resolved | store_less | unresolved (was two-valued; sbml-cycle's
                     Audit renderer needs store_less distinct from a genuinely-missing store)
and `grader_freshness.grade_freshness(stamp)` resolves fresh|stale SERVER-SIDE (the published bundle
reads the baked flag, never recomputes). An absent/behind stamp is stale — never render it fresh.
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from discovery_engine import study_evaluator as se
from discovery_engine import grader_freshness as gf


@pytest.fixture(autouse=True)
def _evict(tmp_path):
    se.clear_workspace_evaluator_cache()
    yield
    se.clear_workspace_evaluator_cache()
    for m in [m for m in sys.modules if m.startswith("pbg_slws")]:
        del sys.modules[m]


def _ws_storeless(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    pkg = ws / "pbg_slws"
    pkg.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: slws\n", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "evaluators.py").write_text(textwrap.dedent("""
        from discovery_engine.study_evaluator import storeless
        @storeless
        def _recovery(test, reader, ws_root):
            return {"result": "FAIL", "evaluated_by": "code", "measured_value": 0.58}
        def register_evaluators(registry):
            registry["recovery_check"] = _recovery
    """), encoding="utf-8")
    return ws


def _study(tmp_path: Path) -> Path:
    sd = tmp_path / "studies" / "recover-x"
    sd.mkdir(parents=True)
    spec = {"schema_version": 4, "name": "recover-x",
            "behavior_tests": [{"name": "recover", "measure": {"kind": "recovery_check"},
                                "pass_if": {"op": ">=", "value": 0.5}}],
            "runs": [{"name": "baseline"}]}  # no store -> store-less path
    (sd / "study.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return sd


# --- stamp enrichment --------------------------------------------------------

def test_grader_stamp_has_graded_at_and_inputs_hash():
    stamp = se._grader_stamp(inputs_hash="abc123", store="resolved")
    assert stamp["version"] == se.GRADER_VERSION
    assert isinstance(stamp.get("graded_at"), str) and "T" in stamp["graded_at"]  # ISO
    assert stamp["inputs_hash"] == "abc123"
    assert stamp["store"] == "resolved"


def test_store_is_three_valued_store_less_not_unresolved(tmp_path):
    ws = _ws_storeless(tmp_path)
    sd = _study(tmp_path)
    se.compute_outcomes(sd, ws_root=ws)
    co = yaml.safe_load((sd / "study.yaml").read_text())["runs"][0]["computed_outcomes"]
    # the recovery grade is real and re-derived by design -> store_less, NOT the ambiguous "unresolved"
    assert co["_grader"]["store"] == "store_less"
    assert co["recover"]["result"] == "FAIL"
    assert "inputs_hash" in co["_grader"] and "graded_at" in co["_grader"]


def test_graded_at_is_idempotent_when_inputs_unchanged(tmp_path):
    ws = _ws_storeless(tmp_path)
    sd = _study(tmp_path)
    se.compute_outcomes(sd, ws_root=ws)
    first = yaml.safe_load((sd / "study.yaml").read_text())["runs"][0]["computed_outcomes"]["_grader"]
    se.compute_outcomes(sd, ws_root=ws)  # re-grade, nothing changed
    second = yaml.safe_load((sd / "study.yaml").read_text())["runs"][0]["computed_outcomes"]["_grader"]
    assert second["inputs_hash"] == first["inputs_hash"]
    assert second["graded_at"] == first["graded_at"]       # NOT re-stamped -> no YAML churn


# --- freshness resolver (server-side) ----------------------------------------

def test_grade_freshness_fresh_when_version_matches():
    assert gf.grade_freshness({"version": se.GRADER_VERSION, "inputs_hash": "h"}) == gf.FRESH


def test_grade_freshness_stale_when_version_behind():
    assert gf.grade_freshness({"version": "1-ancient", "inputs_hash": "h"}) == gf.STALE


def test_grade_freshness_stale_when_no_stamp():
    assert gf.grade_freshness({}) == gf.STALE
    assert gf.grade_freshness(None) == gf.STALE


def test_grade_freshness_stale_when_inputs_hash_changed():
    stamp = {"version": se.GRADER_VERSION, "inputs_hash": "old"}
    assert gf.grade_freshness(stamp, current_inputs_hash="new") == gf.STALE
    assert gf.grade_freshness(stamp, current_inputs_hash="old") == gf.FRESH


def test_annotate_freshness_bakes_the_flag():
    co = {"T": {"result": "FAIL"}, "_grader": {"version": se.GRADER_VERSION}}
    gf.annotate_freshness(co)
    assert co["_grader"]["freshness"] == gf.FRESH
    co2 = {"T": {"result": "FAIL"}, "_grader": {"version": "1-old"}}
    gf.annotate_freshness(co2)
    assert co2["_grader"]["freshness"] == gf.STALE


# --- report_linter stale-grade rule ------------------------------------------

def _lint(spec, tmp_path):
    from discovery_engine.report_linter import _LintContext, _check_stale_computed_grades
    ctx = _LintContext(ws_root=tmp_path, slug="s1", spec=spec)
    _check_stale_computed_grades(ctx)
    return [f for f in ctx.findings if f.check == "stale_computed_grades"]


def test_linter_flags_a_behind_grader_stamp(tmp_path):
    spec = {"runs": [{"name": "baseline",
                      "computed_outcomes": {"T": {"result": "FAIL"},
                                            "_grader": {"version": "1-ancient"}}}]}
    found = _lint(spec, tmp_path)
    assert len(found) == 1
    assert found[0].level == "warning"


def test_linter_silent_on_current_grader_stamp(tmp_path):
    spec = {"runs": [{"name": "baseline",
                      "computed_outcomes": {"T": {"result": "FAIL"},
                                            "_grader": {"version": se.GRADER_VERSION,
                                                        "inputs_hash": "h"}}}]}
    assert _lint(spec, tmp_path) == []
