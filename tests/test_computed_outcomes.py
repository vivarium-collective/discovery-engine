"""Tests for B2b computed_outcomes write-back.

Tasks:
  - Task 1: _resolve_run_store
  - Task 2: compute_outcomes (comment-preserving, idempotent, reconcile)
  - Task 3: compute-outcomes CLI smoke test

Run with: .venv/bin/python -m pytest tests/test_computed_outcomes.py -v
"""
from __future__ import annotations

import textwrap
from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest
import yaml

from discovery_engine import study_evaluator as se


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class FakeReader:
    """Minimal RunReader stub for unit testing."""
    def __init__(self, data: dict[str, pl.DataFrame]):
        self._data = data
    def series(self, name: str) -> pl.DataFrame:
        if name not in self._data:
            raise KeyError(name)
        return self._data[name]
    def observables(self) -> list[str]:
        return list(self._data.keys())


def _make_obs_val_series(vals: list[float]) -> pl.DataFrame:
    """Simple series with obs.val observable."""
    n = len(vals)
    return pl.DataFrame({
        "generation": [0] * n,
        "time": [float(i) for i in range(n)],
        "abs_time": [float(i) for i in range(n)],
        "value": vals,
    })


def _fake_reader_for_obs_val() -> FakeReader:
    """FakeReader with obs.val series (mean=50, in [0, 100] → PASS for range check)."""
    return FakeReader({"obs.val": _make_obs_val_series([40.0, 50.0, 60.0])})


# ===========================================================================
# TASK 1: _resolve_run_store
# ===========================================================================

def test_resolve_emitter_store_takes_priority(tmp_path: Path):
    """emitter.store is tried first before run_dir / parquet."""
    store = tmp_path / "store"
    store.mkdir()
    (store / "history").mkdir()

    run = {
        "emitter": {"store": str(store)},
        "run_dir": str(tmp_path / "run_dir_nonexistent"),
    }
    result = se._resolve_run_store(run, tmp_path)
    assert result == str(store)


def test_resolve_run_dir_fallback(tmp_path: Path):
    """run_dir is used when emitter.store is absent."""
    run_dir = tmp_path / "run_dir"
    run_dir.mkdir()

    run = {"run_dir": str(run_dir)}
    result = se._resolve_run_store(run, tmp_path)
    assert result == str(run_dir)


def test_resolve_parquet_direct_with_history(tmp_path: Path):
    """parquet: pointing at a dir that already has history/ works directly."""
    hive = tmp_path / "hive"
    hive.mkdir()
    (hive / "history").mkdir()

    run = {"parquet": str(hive)}
    result = se._resolve_run_store(run, tmp_path)
    assert result == str(hive)


def test_resolve_parquet_descends_to_experiment_dir(tmp_path: Path):
    """parquet: pointing one level above the experiment dir descends to find history/."""
    parent = tmp_path / "parquet_parent"
    parent.mkdir()
    child = parent / "experiment_dir"
    child.mkdir()
    (child / "history").mkdir()

    run = {"parquet": str(parent)}
    result = se._resolve_run_store(run, tmp_path)
    assert result == str(child)


def test_resolve_relative_path_against_study_dir(tmp_path: Path):
    """Relative paths are resolved against study_dir."""
    hive = tmp_path / "store"
    hive.mkdir()
    (hive / "history").mkdir()

    run = {"emitter": {"store": "store"}}
    result = se._resolve_run_store(run, tmp_path)
    assert result == str(hive)


def test_resolve_relative_parquet_against_ws_root(tmp_path: Path):
    """Relative parquet: paths are tried against ws_root after study_dir."""
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    hive_parent = ws_root / "studies" / "s1" / "parquet-runs" / "run1"
    hive_parent.mkdir(parents=True)
    exp_dir = hive_parent / "experiment_dir"
    exp_dir.mkdir()
    (exp_dir / "history").mkdir()

    study_dir = tmp_path / "study_dir"
    study_dir.mkdir()

    run = {"parquet": "studies/s1/parquet-runs/run1"}
    result = se._resolve_run_store(run, study_dir, ws_root)
    assert result == str(exp_dir)


def test_resolve_returns_none_when_unresolvable(tmp_path: Path):
    """Returns None when no candidate resolves to an existing path."""
    run = {
        "emitter": {"store": "/nonexistent/path/to/store"},
        "run_dir": "/also/nonexistent",
        "parquet": "/and/this/too",
    }
    result = se._resolve_run_store(run, tmp_path)
    assert result is None


def test_resolve_empty_run_returns_none(tmp_path: Path):
    """Empty run dict returns None."""
    result = se._resolve_run_store({}, tmp_path)
    assert result is None


def test_resolve_parquet_no_history_child_returns_none(tmp_path: Path):
    """parquet parent with no child containing history/ returns None."""
    parent = tmp_path / "empty_parent"
    parent.mkdir()
    # no children

    run = {"parquet": str(parent)}
    result = se._resolve_run_store(run, tmp_path)
    assert result is None


# ===========================================================================
# TASK 2: compute_outcomes
# ===========================================================================

# Minimal study YAML with comments + authored outcomes
_STUDY_YAML_WITH_COMMENTS = textwrap.dedent("""\
    # study header comment — must be preserved
    schema_version: 4
    name: test-study

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
        kind: tooling  # non-run-data kind → agent
      pass_if:
        op: range
        low: 0
        high: 100

    runs:
    # run with a resolvable emitter store
    - name: run-a
      status: completed
      # important comment on run-a — must be preserved
      emitter:
        store: {store_path}
      outcomes:
        scalar-test:
          result: PASS
          detail: hand-authored pass
        agent-test:
          result: PASS
          detail: hand-authored agent-test pass
    """)


def _make_study_yaml(tmp_path: Path, store_path: str) -> Path:
    """Write a minimal study.yaml to tmp_path/study/ and return study_dir."""
    study_dir = tmp_path / "study"
    study_dir.mkdir(exist_ok=True)
    (study_dir / "study.yaml").write_text(
        _STUDY_YAML_WITH_COMMENTS.format(store_path=store_path),
        encoding="utf-8",
    )
    return study_dir


@pytest.fixture
def study_with_real_store(tmp_path: Path):
    """study_dir containing study.yaml + a real tiny store (dir with history/)."""
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    (store_dir / "history").mkdir()
    study_dir = _make_study_yaml(tmp_path, str(store_dir))
    return study_dir, store_dir


def test_compute_outcomes_writes_computed_outcomes_block(
    study_with_real_store,
):
    """compute_outcomes writes a computed_outcomes block for each run."""
    study_dir, _store = study_with_real_store
    with patch("viva_emitters.RunReader") as mock_cls:
        mock_cls.open.return_value = _fake_reader_for_obs_val()
        se.compute_outcomes(study_dir)

    doc = yaml.safe_load((study_dir / "study.yaml").read_text())
    run = doc["runs"][0]
    assert "computed_outcomes" in run

    co = run["computed_outcomes"]
    assert "scalar-test" in co
    assert "agent-test" in co


def test_compute_outcomes_scalar_test_is_code_evaluated(study_with_real_store):
    """The scalar-test (range_check kind, resolvable path) is code-evaluated."""
    study_dir, _store = study_with_real_store
    with patch("viva_emitters.RunReader") as mock_cls:
        mock_cls.open.return_value = _fake_reader_for_obs_val()
        se.compute_outcomes(study_dir)

    doc = yaml.safe_load((study_dir / "study.yaml").read_text())
    co = doc["runs"][0]["computed_outcomes"]
    assert co["scalar-test"]["evaluated_by"] == "code"
    assert "result" in co["scalar-test"]
    assert "reconcile" in co["scalar-test"]


def test_compute_outcomes_agent_test_preserved(study_with_real_store):
    """Non-run-data kind tests are agent-bucketed in computed_outcomes."""
    study_dir, _store = study_with_real_store
    with patch("viva_emitters.RunReader") as mock_cls:
        mock_cls.open.return_value = _fake_reader_for_obs_val()
        se.compute_outcomes(study_dir)

    doc = yaml.safe_load((study_dir / "study.yaml").read_text())
    co = doc["runs"][0]["computed_outcomes"]
    assert co["agent-test"]["evaluated_by"] == "agent"
    assert "reconcile" in co["agent-test"]


def test_compute_outcomes_authored_outcomes_never_modified(study_with_real_store):
    """authored run["outcomes"] block is never touched — byte-unchanged invariant."""
    study_dir, _store = study_with_real_store
    original_text = (study_dir / "study.yaml").read_text()
    original_doc = yaml.safe_load(original_text)
    original_outcomes = original_doc["runs"][0]["outcomes"]

    with patch("viva_emitters.RunReader") as mock_cls:
        mock_cls.open.return_value = _fake_reader_for_obs_val()
        se.compute_outcomes(study_dir)

    after_doc = yaml.safe_load((study_dir / "study.yaml").read_text())
    after_outcomes = after_doc["runs"][0]["outcomes"]

    # Must be byte-unchanged
    assert after_outcomes == original_outcomes
    assert after_outcomes["scalar-test"]["result"] == "PASS"
    assert after_outcomes["scalar-test"]["detail"] == "hand-authored pass"
    assert after_outcomes["agent-test"]["result"] == "PASS"


def test_compute_outcomes_comments_preserved(study_with_real_store):
    """Comments in study.yaml survive the ruamel round-trip."""
    study_dir, _store = study_with_real_store
    with patch("viva_emitters.RunReader") as mock_cls:
        mock_cls.open.return_value = _fake_reader_for_obs_val()
        se.compute_outcomes(study_dir)

    after_text = (study_dir / "study.yaml").read_text()
    assert "# study header comment" in after_text
    assert "# important comment on run-a" in after_text


def test_compute_outcomes_reconcile_agree(study_with_real_store):
    """reconcile=agree when computed result matches authored result."""
    study_dir, _store = study_with_real_store
    with patch("viva_emitters.RunReader") as mock_cls:
        mock_cls.open.return_value = _fake_reader_for_obs_val()
        se.compute_outcomes(study_dir)

    doc = yaml.safe_load((study_dir / "study.yaml").read_text())
    co = doc["runs"][0]["computed_outcomes"]
    # scalar-test: code evaluates to PASS (mean=50 in [0,100]); authored=PASS → agree
    assert co["scalar-test"]["reconcile"] == "agree"


def test_compute_outcomes_reconcile_divergent(tmp_path: Path):
    """reconcile=divergent when computed result differs from authored result."""
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    (store_dir / "history").mkdir()

    # Authored says FAIL; code will compute PASS (mean=50 in [0,100])
    yaml_text = textwrap.dedent(f"""\
        schema_version: 4
        name: test-study
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
        runs:
        - name: run-a
          emitter:
            store: {store_dir}
          outcomes:
            scalar-test:
              result: FAIL
              detail: authored as fail
        """)
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    (study_dir / "study.yaml").write_text(yaml_text)

    with patch("viva_emitters.RunReader") as mock_cls:
        mock_cls.open.return_value = _fake_reader_for_obs_val()
        se.compute_outcomes(study_dir)

    doc = yaml.safe_load((study_dir / "study.yaml").read_text())
    co = doc["runs"][0]["computed_outcomes"]
    assert co["scalar-test"]["result"] == "PASS"   # code says PASS
    assert co["scalar-test"]["reconcile"] == "divergent"  # authored says FAIL


def test_compute_outcomes_reconcile_no_authored(tmp_path: Path):
    """reconcile=no_authored when no authored entry exists for the test."""
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    (store_dir / "history").mkdir()

    yaml_text = textwrap.dedent(f"""\
        schema_version: 4
        name: test-study
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
        runs:
        - name: run-a
          emitter:
            store: {store_dir}
        """)
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    (study_dir / "study.yaml").write_text(yaml_text)

    with patch("viva_emitters.RunReader") as mock_cls:
        mock_cls.open.return_value = _fake_reader_for_obs_val()
        se.compute_outcomes(study_dir)

    doc = yaml.safe_load((study_dir / "study.yaml").read_text())
    co = doc["runs"][0]["computed_outcomes"]
    assert co["scalar-test"]["reconcile"] == "no_authored"


def test_compute_outcomes_idempotent(study_with_real_store):
    """Second compute_outcomes call produces byte-identical output (no write)."""
    study_dir, _store = study_with_real_store

    with patch("viva_emitters.RunReader") as mock_cls:
        mock_cls.open.return_value = _fake_reader_for_obs_val()
        se.compute_outcomes(study_dir)

    text_after_first = (study_dir / "study.yaml").read_text()

    with patch("viva_emitters.RunReader") as mock_cls:
        mock_cls.open.return_value = _fake_reader_for_obs_val()
        se.compute_outcomes(study_dir)

    text_after_second = (study_dir / "study.yaml").read_text()
    assert text_after_first == text_after_second


def test_compute_outcomes_unresolvable_store_sets_status(tmp_path: Path):
    """Runs with unresolvable stores get computed_outcomes._status=store_unresolved."""
    yaml_text = textwrap.dedent("""\
        schema_version: 4
        name: test-study
        tests:
        - name: t1
          measure:
            kind: range_check_per_generation
            path: obs.val
            window: full_lineage_from_gen_0
          pass_if:
            op: range
            low: 0
            high: 100
        runs:
        - name: run-a
          parquet: /nonexistent/path/to/store
        """)
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    (study_dir / "study.yaml").write_text(yaml_text)

    se.compute_outcomes(study_dir)

    doc = yaml.safe_load((study_dir / "study.yaml").read_text())
    run = doc["runs"][0]
    assert "computed_outcomes" in run
    assert run["computed_outcomes"]["_status"] == "store_unresolved"


def test_compute_outcomes_returns_summary(study_with_real_store):
    """compute_outcomes returns a dict with runs_evaluated, tests_code, tests_agent."""
    study_dir, _store = study_with_real_store
    with patch("viva_emitters.RunReader") as mock_cls:
        mock_cls.open.return_value = _fake_reader_for_obs_val()
        summary = se.compute_outcomes(study_dir)

    assert isinstance(summary, dict)
    assert "runs_evaluated" in summary
    assert "tests_code" in summary
    assert "tests_agent" in summary
    assert summary["runs_evaluated"] == 1
    # scalar-test → code, agent-test → agent
    assert summary["tests_code"] == 1
    assert summary["tests_agent"] == 1


def test_compute_outcomes_no_runs_noop(tmp_path: Path):
    """study.yaml with no runs block writes nothing and returns zero counts."""
    yaml_text = textwrap.dedent("""\
        schema_version: 4
        name: test-study
        tests: []
        """)
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    (study_dir / "study.yaml").write_text(yaml_text)
    original = (study_dir / "study.yaml").read_text()

    summary = se.compute_outcomes(study_dir)

    assert summary["runs_evaluated"] == 0
    # File should be unchanged (no runs → no writes needed)
    after = (study_dir / "study.yaml").read_text()
    assert after == original


# ===========================================================================
# TASK 3: CLI smoke test
# ===========================================================================

def test_compute_outcomes_cli_exists():
    """compute_outcomes_cli is importable from study_evaluator."""
    from discovery_engine.study_evaluator import compute_outcomes_cli
    assert callable(compute_outcomes_cli)


def test_compute_outcomes_cli_study_dir(study_with_real_store):
    """CLI with --study-dir produces non-zero exit 0 on a real study."""
    from click.testing import CliRunner
    from discovery_engine.study_evaluator import compute_outcomes_cli

    study_dir, _store = study_with_real_store

    with patch("viva_emitters.RunReader") as mock_cls:
        mock_cls.open.return_value = _fake_reader_for_obs_val()
        runner = CliRunner()
        result = runner.invoke(compute_outcomes_cli, ["--study-dir", str(study_dir)])

    assert result.exit_code == 0, result.output
    assert "runs evaluated" in result.output or "run" in result.output
