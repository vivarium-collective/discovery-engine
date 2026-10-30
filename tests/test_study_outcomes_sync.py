"""Tests for study_outcomes.sync() — records runs then computes outcomes (best-effort).

Run with:
    .venv/bin/python -m pytest tests/test_study_outcomes_sync.py -v
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest
import yaml

from discovery_engine import run_registry, study_io, study_outcomes as so


# ---------------------------------------------------------------------------
# Shared helpers (same style as test_computed_outcomes.py)
# ---------------------------------------------------------------------------


class FakeReader:
    """Minimal RunReader stub."""
    def __init__(self, data: dict[str, pl.DataFrame]):
        self._data = data

    def series(self, name: str) -> pl.DataFrame:
        if name not in self._data:
            raise KeyError(name)
        return self._data[name]

    def observables(self) -> list[str]:
        return list(self._data.keys())


def _make_obs_val_series(vals: list[float]) -> pl.DataFrame:
    n = len(vals)
    return pl.DataFrame({
        "generation": [0] * n,
        "time": [float(i) for i in range(n)],
        "abs_time": [float(i) for i in range(n)],
        "value": vals,
    })


def _fake_reader() -> FakeReader:
    return FakeReader({"obs.val": _make_obs_val_series([40.0, 50.0, 60.0])})


# Minimal study YAML — has tests and a run with a resolvable emitter.store
_STUDY_YAML_TMPL = textwrap.dedent("""\
    schema_version: 4
    name: sync-study

    tests:
    - name: scalar-test
      measure:
        kind: range_check_per_generation
        path: obs.val
        window: full_lineage_from_gen_0
      pass_if:
        op: range
        low: 0
        high: 100

    - name: agent-test
      measure:
        kind: tooling
      pass_if:
        op: range
        low: 0
        high: 100

    runs:
    - name: run-a
      status: completed
      emitter:
        store: {store_path}
      outcomes:
        scalar-test:
          result: PASS
          detail: authored pass
        agent-test:
          result: PASS
          detail: authored agent pass
    """)


@pytest.fixture
def study_with_store_and_db(tmp_path: Path):
    """study_dir with study.yaml (run-a has resolvable store) + runs.db with a new run-b."""
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    (store_dir / "history").mkdir()

    study_dir = tmp_path / "study"
    study_dir.mkdir()
    (study_dir / "study.yaml").write_text(
        _STUDY_YAML_TMPL.format(store_path=str(store_dir)),
        encoding="utf-8",
    )

    # Add a new run to the DB that is NOT yet in study.yaml
    run_registry.register_run(
        study_dir / "runs.db",
        "run-b",
        spec_id="sync-study",
        status="completed",
        started_at="2026-01-02T00:00:00Z",
        completed_at="2026-01-02T00:01:00Z",
    )

    return study_dir, store_dir


# ===========================================================================
# TEST 1: happy path — sync records runs AND computes outcomes
# ===========================================================================


def test_mechanical_record_tags_variant_from_label():
    """A variant run (label != baseline) is tagged with `variant`, so canonical-run
    selection excludes it from the baseline gate; a baseline run is not tagged."""
    var = so._mechanical_record({"run_id": "r1", "status": "complete", "label": "sweep-k-0.5"})
    assert var.get("variant") == "sweep-k-0.5"
    base = so._mechanical_record({"run_id": "r2", "status": "complete", "label": "baseline"})
    assert "variant" not in base
    none = so._mechanical_record({"run_id": "r3", "status": "complete"})
    assert "variant" not in none


def test_sync_returns_record_runs_keys_and_computed(study_with_store_and_db):
    """sync returns added/updated from record_runs PLUS a computed key with summary."""
    study_dir, _store = study_with_store_and_db

    with patch("viva_emitters.RunReader") as mock_cls:
        mock_cls.open.return_value = _fake_reader()
        result = so.sync(study_dir)

    # record_runs keys present
    assert "added" in result
    assert "updated" in result
    # run-b was in db but not in yaml → added=1
    assert result["added"] == 1

    # computed key present with expected structure from compute_outcomes
    assert "computed" in result
    comp = result["computed"]
    assert "runs_evaluated" in comp
    assert "tests_code" in comp
    assert "tests_agent" in comp


def test_sync_compute_outcomes_writes_computed_outcomes_block(study_with_store_and_db):
    """sync's compute_outcomes call writes computed_outcomes into run-a in study.yaml."""
    study_dir, _store = study_with_store_and_db

    with patch("viva_emitters.RunReader") as mock_cls:
        mock_cls.open.return_value = _fake_reader()
        so.sync(study_dir)

    doc = yaml.safe_load((study_dir / "study.yaml").read_text())
    runs_by_name = {r["name"]: r for r in doc["runs"]}

    # run-a (resolvable store) has a computed_outcomes block
    assert "computed_outcomes" in runs_by_name["run-a"]
    co = runs_by_name["run-a"]["computed_outcomes"]
    assert "scalar-test" in co
    assert "agent-test" in co
    assert co["scalar-test"]["evaluated_by"] == "code"
    assert co["agent-test"]["evaluated_by"] == "agent"


def test_sync_record_runs_side_effects_preserved(study_with_store_and_db):
    """After sync, run-b (only in DB) is present in study.yaml."""
    study_dir, _store = study_with_store_and_db

    with patch("viva_emitters.RunReader") as mock_cls:
        mock_cls.open.return_value = _fake_reader()
        so.sync(study_dir)

    doc = yaml.safe_load((study_dir / "study.yaml").read_text())
    names = [r["name"] for r in doc["runs"]]
    assert "run-a" in names
    assert "run-b" in names


# ===========================================================================
# TEST 2: graceful degradation — compute_outcomes error does not raise
# ===========================================================================


def test_sync_degradation_on_compute_error(tmp_path: Path, monkeypatch):
    """If compute_outcomes raises, sync returns {"error": ...} under "computed" and does NOT raise."""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    study_io.save_yaml_atomic(
        study_dir / "study.yaml",
        {"name": "deg-study", "runs": []},
    )
    run_registry.register_run(
        study_dir / "runs.db",
        "run-x",
        spec_id="deg-study",
        status="completed",
        started_at="2026-01-01T00:00:00Z",
        completed_at="2026-01-01T00:01:00Z",
    )

    import discovery_engine.study_evaluator as se
    monkeypatch.setattr(se, "compute_outcomes", _raise_runtime)

    # Must not raise
    result = so.sync(study_dir)

    # record_runs side effects intact: run-x added to yaml
    spec = study_io.load_yaml_mapping(study_dir / "study.yaml")
    assert any(r["name"] == "run-x" for r in spec.get("runs", []))

    # record_runs keys present
    assert result["added"] == 1
    assert "updated" in result

    # computed carries the error, not an exception
    assert "computed" in result
    assert "error" in result["computed"]
    assert "boom" in result["computed"]["error"]


def _raise_runtime(*_args, **_kwargs):
    raise RuntimeError("boom")


# ===========================================================================
# Task 2: sync calls populate_finding_observations as best-effort 4th step
# ===========================================================================


def test_sync_includes_findings_key_in_summary(study_with_store_and_db):
    """sync summary has a 'findings' key after populate_finding_observations is wired."""
    study_dir, _store = study_with_store_and_db

    with patch("viva_emitters.RunReader") as mock_cls:
        mock_cls.open.return_value = _fake_reader()
        result = so.sync(study_dir)

    assert "findings" in result, (
        "sync summary must include 'findings' key (populate_finding_observations result)"
    )
    findings = result["findings"]
    assert isinstance(findings, dict), "findings value must be a dict"
    assert "filled" in findings
    assert "skipped" in findings


def test_sync_findings_best_effort_on_error(tmp_path: Path, monkeypatch):
    """If populate_finding_observations raises, sync captures error under 'findings' and does NOT raise."""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    study_io.save_yaml_atomic(
        study_dir / "study.yaml",
        {"name": "err-study", "runs": []},
    )

    import discovery_engine.finding_observations as fo
    monkeypatch.setattr(fo, "populate_finding_observations", _raise_runtime)

    result = so.sync(study_dir)
    assert "findings" in result
    assert "error" in result["findings"]
    assert "boom" in result["findings"]["error"]


def test_sync_findings_zero_when_no_findings(study_with_store_and_db):
    """When the study has no findings[], findings={filled:0, skipped:0}."""
    study_dir, _store = study_with_store_and_db

    with patch("viva_emitters.RunReader") as mock_cls:
        mock_cls.open.return_value = _fake_reader()
        result = so.sync(study_dir)

    findings = result.get("findings", {})
    # Study fixture has no findings[] section — both counts are 0
    assert findings.get("filled", -1) == 0
    assert findings.get("skipped", -1) == 0


def test_sync_degradation_on_import_error(tmp_path: Path, monkeypatch):
    """If study_evaluator is unavailable (ImportError), sync returns {"skipped": ...} and does NOT raise."""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    study_io.save_yaml_atomic(
        study_dir / "study.yaml",
        {"name": "imp-study", "runs": []},
    )

    import builtins
    real_import = builtins.__import__

    def _block_evaluator(name, *args, **kwargs):
        if "study_evaluator" in name:
            raise ImportError(f"simulated missing extra: {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _block_evaluator)

    result = so.sync(study_dir)

    assert "computed" in result
    assert "skipped" in result["computed"]
    assert "evaluator extra unavailable" in result["computed"]["skipped"]
