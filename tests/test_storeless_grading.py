"""Tests for store-less evaluator grading + candidate_model sentinel + grader stamp.

The bug these guard against (traced on viva-sbml-decomposition's recovery investigation):
``compute_outcomes`` bailed to ``store_unresolved`` when a run's store couldn't be resolved,
BEFORE dispatching any evaluator — so store-less evaluators (recovery / non_recoverable gates
that re-derive their result internally and need no run data) were never graded offline, and
the derived verdict fell back to the stale identity-baseline authored outcome.

Run with: .venv/bin/python -m pytest tests/test_storeless_grading.py -v
"""
from __future__ import annotations

import sys
import textwrap
from pathlib import Path

import pytest
import yaml

from discovery_engine import study_evaluator as se


@pytest.fixture(autouse=True)
def _evict_fixture_packages():
    se.clear_workspace_evaluator_cache()
    yield
    se.clear_workspace_evaluator_cache()
    for mod_name in list(sys.modules):
        if mod_name.startswith(("pbg_slws", "pbg_rdws")):
            del sys.modules[mod_name]


def _ws_with_storeless(tmp_path: Path, kind: str = "recovery_check") -> Path:
    """A workspace whose pbg_slws.evaluators registers ONE store-less evaluator."""
    ws = tmp_path / "ws"
    pkg = ws / "pbg_slws"
    pkg.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: slws\n", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "evaluators.py").write_text(textwrap.dedent(f"""
        from discovery_engine.study_evaluator import storeless
        @storeless
        def _recovery(test, reader, ws_root):
            # re-derives its own result; must run even with reader=None
            assert reader is None or True
            return {{"result": "FAIL", "evaluated_by": "code", "measured_value": 0.58,
                    "status": "contradicts", "detail": "real recovery search ran"}}
        def register_evaluators(registry):
            registry["{kind}"] = _recovery
    """), encoding="utf-8")
    return ws


def _study_dir(tmp_path: Path, *, kind: str, run_has_store: bool) -> Path:
    sd = tmp_path / "studies" / "recover-x"
    sd.mkdir(parents=True)
    run = {"name": "baseline"}
    if run_has_store:
        store = tmp_path / "store"
        (store / "history").mkdir(parents=True)
        run["emitter"] = {"store": str(store)}
    spec = {
        "schema_version": 4,
        "name": "recover-x",
        "behavior_tests": [{
            "name": "recover-mechanism",
            "measure": {"kind": kind, "candidate_model": "@recovered"},
            "pass_if": {"op": ">=", "value": 0.5},
        }],
        "runs": [run],
    }
    (sd / "study.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    return sd


# --- sub-feature 1: store-less grading when the store is unresolved -----------------

def test_storeless_test_graded_when_store_unresolved(tmp_path):
    ws = _ws_with_storeless(tmp_path)
    sd = _study_dir(tmp_path, kind="recovery_check", run_has_store=False)
    se.compute_outcomes(sd, ws_root=ws)
    doc = yaml.safe_load((sd / "study.yaml").read_text())
    co = doc["runs"][0]["computed_outcomes"]
    # the store-less test WAS graded (not skipped as store_unresolved)
    assert "recover-mechanism" in co
    assert co["recover-mechanism"]["result"] == "FAIL"
    assert co["recover-mechanism"]["evaluated_by"] == "code"
    # a GRADED block must NOT carry _status (that convention means "stub, skip me", which
    # finding_observations uses to blank the block — it would hide the real grade). The
    # store-less provenance lives in the grader stamp instead.
    assert "_status" not in co
    assert co["_grader"]["store"] == "store_less"   # Phase 2: three-valued; re-derived-by-design


def test_non_storeless_unresolved_still_bails(tmp_path):
    # A kind the workspace did NOT mark store-less: unresolved store => store_unresolved.
    ws = _ws_with_storeless(tmp_path)  # registers recovery_check as store-less
    sd = _study_dir(tmp_path, kind="generation_average", run_has_store=False)  # native run-data kind
    se.compute_outcomes(sd, ws_root=ws)
    co = yaml.safe_load((sd / "study.yaml").read_text())["runs"][0]["computed_outcomes"]
    assert co.get("_status") == "store_unresolved"
    assert "recover-mechanism" not in co


def test_storeless_grade_fills_finding(tmp_path):
    # The whole point: a store-less grade must reach findings[] (and thus the verdict + the
    # generative loop). With the old `_status: store_less_graded` marker, finding_observations
    # blanked the block and the finding never filled — this guards that bridge.
    from discovery_engine import finding_observations as fo
    ws = _ws_with_storeless(tmp_path)
    sd = tmp_path / "studies" / "recover-x"
    sd.mkdir(parents=True)
    spec = {
        "schema_version": 4,
        "name": "recover-x",
        "behavior_tests": [{
            "name": "recover-mechanism",
            "measure": {"kind": "recovery_check", "candidate_model": "@recovered"},
            "pass_if": {"op": "<=", "value": 0.05},
        }],
        "findings": [{
            "id": "f-recover",
            "statement": "recovery of the mechanism",
            "evidence": {"from_test": "recover-mechanism"},
        }],
        "runs": [{"name": "baseline"}],  # no store → unresolved
    }
    (sd / "study.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")
    se.compute_outcomes(sd, ws_root=ws)
    res = fo.populate_finding_observations(sd)
    doc = yaml.safe_load((sd / "study.yaml").read_text())
    # the store-less grade (measured_value 0.58) flowed into the finding's evidence.observed
    assert res["filled"] == 1
    assert doc["findings"][0]["evidence"]["observed"] == 0.58


def test_storeless_graded_when_ws_root_omitted(tmp_path):
    # sbml's repro: compute_outcomes(study_dir) with NO ws_root. The workspace root must be
    # derived from study_dir so the @storeless evaluators are found — otherwise the store-less
    # set is empty and it wrongly bails to store_unresolved. Study lives UNDER the workspace.
    ws = tmp_path / "ws"
    pkg = ws / "pbg_slws"
    pkg.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: slws\n", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "evaluators.py").write_text(textwrap.dedent("""
        from discovery_engine.study_evaluator import storeless
        @storeless
        def _recovery(test, reader, ws_root):
            return {"result": "FAIL", "evaluated_by": "code", "measured_value": 0.58,
                    "status": "contradicts"}
        def register_evaluators(registry):
            registry["recovery_check"] = _recovery
    """), encoding="utf-8")
    sd = ws / "studies" / "recover-x"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.dump({
        "schema_version": 4, "name": "recover-x",
        "behavior_tests": [{"name": "recover-mechanism",
                            "measure": {"kind": "recovery_check"},
                            "pass_if": {"op": ">=", "value": 0.5}}],
        "runs": [{"name": "baseline"}],  # no store (recover_candidate strips the emitter)
    }), encoding="utf-8")
    se.clear_workspace_evaluator_cache()
    se.compute_outcomes(sd)  # <-- NO ws_root, exactly as sbml calls it
    co = yaml.safe_load((sd / "study.yaml").read_text())["runs"][0]["computed_outcomes"]
    assert co.get("_status") != "store_unresolved"  # was the bug
    assert co["recover-mechanism"]["result"] == "FAIL"
    assert co["_grader"].get("store") == "store_less"   # Phase 2: three-valued


# --- sub-feature 2: candidate_model sentinel is load-bearing ------------------------

def test_sentinel_routes_to_evaluator_even_for_run_data_kind(tmp_path):
    # kind IS a native RUN_DATA_KIND, but candidate_model:"@recovered" must route to the
    # registered evaluator instead of grading the recorded run.
    ws = _ws_with_storeless(tmp_path, kind="generation_average")  # shadow a native kind
    se.clear_workspace_evaluator_cache()
    test = {"name": "t", "measure": {"kind": "generation_average", "candidate_model": "@recovered"},
            "pass_if": {"op": ">=", "value": 0.5}}
    out = se.evaluate_test(test, reader=None, ws_root=ws)
    assert out["evaluated_by"] == "code"
    assert out.get("detail") == "real recovery search ran"


def test_sentinel_without_evaluator_refuses_baseline(tmp_path):
    # sentinel measure, native kind, NO workspace evaluator => agent bucket, not a baseline grade.
    test = {"name": "t", "measure": {"kind": "generation_average", "candidate_model": "@recovered",
                                     "field": "obs.val"},
            "pass_if": {"op": ">=", "value": 0.5}}
    out = se.evaluate_test(test, reader=None, ws_root=None)
    assert out["evaluated_by"] == "agent"
    assert "candidate_model" in (out.get("reason") or out.get("detail") or "")


# --- sub-feature 3: grader-freshness stamp ------------------------------------------

def test_grader_stamp_written(tmp_path):
    ws = _ws_with_storeless(tmp_path)
    sd = _study_dir(tmp_path, kind="recovery_check", run_has_store=False)
    se.compute_outcomes(sd, ws_root=ws)
    co = yaml.safe_load((sd / "study.yaml").read_text())["runs"][0]["computed_outcomes"]
    assert co["_grader"]["version"] == se.GRADER_VERSION
