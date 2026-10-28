"""Tests for discovery_engine.study_evaluator (Tasks 1–4).

Run with: .venv/bin/python -m pytest tests/test_study_evaluator.py -v
"""
from __future__ import annotations

import polars as pl
import pytest

from discovery_engine import study_evaluator as se


# ---------------------------------------------------------------------------
# Fake reader for unit tests
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
        return sorted(self._data.keys())


def _make_series(gen_vals: list[tuple[int, float]]) -> pl.DataFrame:
    """Build a minimal [generation, time, abs_time, value] series from (gen, value) pairs."""
    rows = []
    time_offset = 0.0
    prev_gen = None
    for i, (g, v) in enumerate(gen_vals):
        if prev_gen != g:
            time_offset = 0.0
        t = float(i)
        rows.append({"generation": g, "time": time_offset, "abs_time": float(i), "value": v})
        time_offset += 1.0
        prev_gen = g
    return pl.DataFrame(rows, schema={
        "generation": pl.Int64,
        "time": pl.Float64,
        "abs_time": pl.Float64,
        "value": pl.Float64,
    })


def _make_gen_series(n_gens: int = 3, ticks_per_gen: int = 5) -> pl.DataFrame:
    """Build a multi-gen series with linearly increasing values per generation."""
    rows = []
    t_abs = 0.0
    for g in range(n_gens):
        for t in range(ticks_per_gen):
            rows.append({
                "generation": g,
                "time": float(t),
                "abs_time": t_abs,
                "value": float(g * 100 + t * 10),
            })
            t_abs += 1.0
    return pl.DataFrame(rows, schema={
        "generation": pl.Int64,
        "time": pl.Float64,
        "abs_time": pl.Float64,
        "value": pl.Float64,
    })


# ===========================================================================
# TASK 1: Skeleton + bucket classifier
# ===========================================================================

def test_non_run_data_kind_routes_to_agent():
    out = se.evaluate_test(
        {"name": "t", "measure": {"kind": "tooling"}, "pass_if": {"op": "eq", "value": True}},
        reader=None,
    )
    assert out["evaluated_by"] == "agent"


def test_missing_measure_routes_to_agent():
    out = se.evaluate_test({"name": "t"}, reader=None)
    assert out["evaluated_by"] == "agent"


def test_run_data_kind_not_immediately_bucketed():
    """A run-data kind with a valid path/op doesn't get agent-bucketed for kind alone."""
    # This test uses a real-looking spec but with reader=None — it will still
    # fail later in the pipeline but NOT at the kind-check step.
    out = se.evaluate_test(
        {"name": "t", "measure": {"kind": "range_check_per_generation"}, "pass_if": {"op": "eq", "value": 1}},
        reader=None,
    )
    # Should fail somewhere later (missing path, or series resolution error), not at kind.
    assert out["evaluated_by"] in ("agent", "needs_rerun")


def test_evaluate_study_returns_dict_keyed_by_test_name():
    spec = {
        "tests": [
            {"name": "t1", "measure": {"kind": "tooling"}, "pass_if": {"op": "eq", "value": True}},
            {"name": "t2"},
        ]
    }
    result = se.evaluate_study(spec, reader=None)
    assert set(result.keys()) == {"t1", "t2"}
    assert result["t1"]["evaluated_by"] == "agent"
    assert result["t2"]["evaluated_by"] == "agent"


def test_evaluate_study_behavior_tests_key():
    """evaluate_study also reads 'behavior_tests' key."""
    spec = {
        "behavior_tests": [
            {"name": "bt1", "measure": {"kind": "biological"}},
        ]
    }
    result = se.evaluate_study(spec, reader=None)
    assert "bt1" in result


def test_all_run_data_kinds_present():
    """Spot-check the closed RUN_DATA_KINDS set has expected members."""
    for kind in ("range_check_per_generation", "generation_average", "rate_match",
                 "derived_scalar", "per_gen", "periodicity_check"):
        assert kind in se.RUN_DATA_KINDS


# ===========================================================================
# TASK 2: Path/expression resolver
# ===========================================================================

def _basic_reader():
    """Fake reader with two scalar observables."""
    a = _make_gen_series(3, 5)
    b = _make_gen_series(3, 5)
    b = b.with_columns((pl.col("value") * 2).alias("value"))
    return FakeReader({"obs.a": a, "obs.b": b})


def test_single_observable_resolves_to_series():
    reader = _basic_reader()
    df = se._resolve_series("obs.a", reader)
    assert set(df.columns) == {"generation", "time", "abs_time", "value"}
    assert len(df) == 15  # 3 gens × 5 ticks


def test_expression_a_div_b_resolves():
    reader = _basic_reader()
    df = se._resolve_series("obs.a / obs.b", reader)
    assert set(df.columns) == {"generation", "time", "abs_time", "value"}
    # obs.b = 2 * obs.a, so obs.a / obs.b = 0.5 everywhere (except when value=0)
    non_zero = df.filter(pl.col("value").is_not_nan() & (df["value"] != 0))
    # For t=0 of gen 0 both are 0/0 which may be nan; ignore those
    valid = df.filter(pl.col("value").is_not_nan())
    # Should have ~0.5 for non-zero rows
    if len(valid) > 0:
        assert abs(float(valid["value"].mean()) - 0.5) < 0.01


def test_unknown_observable_raises_observable_not_found():
    reader = _basic_reader()
    with pytest.raises(se.ObservableNotFound):
        se._resolve_series("obs.unknown", reader)


def test_expression_with_unknown_token_routes_to_agent():
    """A path containing an unresolvable token routes evaluate_test to agent."""
    reader = _basic_reader()
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "range_check_per_generation", "path": "obs.unknown", "window": "full_lineage_from_gen_0"},
            "pass_if": {"op": "range", "low": 0, "high": 100},
        },
        reader=reader,
    )
    assert out["evaluated_by"] == "agent"


def test_annotated_path_with_junk_tokens_routes_to_agent():
    """A path like 'obs.a (some annotation text)' routes to agent because 'some', 'annotation',
    'text' are not observables."""
    reader = _basic_reader()
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {
                "kind": "range_check_per_generation",
                "path": "obs.a (some annotation text)",
                "window": "full_lineage_from_gen_0",
            },
            "pass_if": {"op": "range", "low": 0, "high": 1000},
        },
        reader=reader,
    )
    assert out["evaluated_by"] == "agent"


def test_expression_with_literal_is_supported():
    """obs.a / 2 is a valid expression; 2 is a literal, not an unknown observable."""
    reader = _basic_reader()
    df = se._resolve_series("obs.a / 2", reader)
    assert set(df.columns) == {"generation", "time", "abs_time", "value"}
    # All values should be half of obs.a values
    a_df = reader.series("obs.a")
    # For rows where a > 0, result should be a/2
    expected = a_df.filter(pl.col("value") > 0)["value"].mean() / 2
    result = df.filter(pl.col("value") > 0)["value"].mean()
    assert abs(float(result - expected)) < 0.1


# ===========================================================================
# TASK 3: Windowing
# ===========================================================================

def _make_3gen_series():
    """Build a simple 3-gen series with 10 ticks each: values 0..9 per gen."""
    rows = []
    t_abs = 0.0
    for g in range(3):
        for t in range(10):
            rows.append({
                "generation": g,
                "time": float(t),
                "abs_time": t_abs,
                "value": float(t),  # 0..9 per gen
            })
            t_abs += 1.0
    return pl.DataFrame(rows, schema={
        "generation": pl.Int64,
        "time": pl.Float64,
        "abs_time": pl.Float64,
        "value": pl.Float64,
    })


def test_window_full_lineage_returns_all_rows():
    series = _make_3gen_series()
    kind, data = se._apply_window(series, "full_lineage_from_gen_0")
    assert kind == "flat"
    assert len(data) == 30


def test_window_from_generation_2_filters():
    series = _make_3gen_series()
    kind, data = se._apply_window(series, "from_generation_2")
    assert kind == "flat"
    # Only gen 2 rows
    assert len(data) == 10
    assert data["generation"].unique().to_list() == [2]


def test_window_every_generation_groups():
    series = _make_3gen_series()
    kind, data = se._apply_window(series, "every_generation")
    assert kind == "per_gen_all"
    assert set(data.keys()) == {0, 1, 2}
    for g, df in data.items():
        assert len(df) == 10


def test_window_peak_of_each_cycle():
    series = _make_3gen_series()
    kind, data = se._apply_window(series, "peak_of_each_cycle")
    assert kind == "per_gen_scalar"
    # Max value per gen is 9 (last tick)
    for g in range(3):
        assert data[g] == pytest.approx(9.0)


def test_window_gen_steady_state_from_gen_3():
    rows = []
    t_abs = 0.0
    for g in range(5):
        for t in range(5):
            rows.append({"generation": g, "time": float(t), "abs_time": t_abs, "value": float(g)})
            t_abs += 1.0
    series = pl.DataFrame(rows, schema={
        "generation": pl.Int64, "time": pl.Float64, "abs_time": pl.Float64, "value": pl.Float64
    })
    kind, data = se._apply_window(series, "gen_steady_state")
    assert kind == "flat"
    assert data["generation"].min() == 3


def test_window_peak_of_each_cycle_from_gen_N():
    series = _make_3gen_series()
    kind, data = se._apply_window(series, "peak_of_each_cycle_from_gen_1")
    assert kind == "per_gen_scalar"
    assert 0 not in data  # gen 0 excluded
    assert 1 in data and 2 in data


def test_unsupported_window_raises():
    # per_minute_full_lineage is now supported (Task 2); use a truly-unsupported spec.
    series = _make_3gen_series()
    with pytest.raises(se.WindowNotSupported):
        se._apply_window(series, "per_second_full_lineage_xyz")


def test_unsupported_window_routes_to_agent():
    reader = _basic_reader()
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "range_check_per_generation", "path": "obs.a", "window": "unsupported_window_xyz"},
            "pass_if": {"op": "range", "low": 0, "high": 1000},
        },
        reader=reader,
    )
    assert out["evaluated_by"] == "agent"


# ===========================================================================
# TASK 4: Measure reductions + pass_if operators
# ===========================================================================

def _reader_with_series(**kwargs) -> FakeReader:
    """Build a fake reader from keyword {name: series} pairs."""
    return FakeReader({k: v for k, v in kwargs.items()})


# -- range / in_range --

def test_range_op_pass():
    series = _make_gen_series(1, 5)  # values: 0, 10, 20, 30, 40 → mean = 20
    reader = _reader_with_series(**{"obs.val": series})
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "range_check_per_generation", "path": "obs.val", "window": "full_lineage_from_gen_0"},
            "pass_if": {"op": "range", "low": 0, "high": 50},
        },
        reader=reader,
    )
    assert out["result"] == "PASS"
    assert out["evaluated_by"] == "code"
    assert out["measured_value"] == pytest.approx(20.0)


def test_range_op_fail():
    series = _make_gen_series(1, 5)  # mean = 20
    reader = _reader_with_series(**{"obs.val": series})
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "range_check_per_generation", "path": "obs.val", "window": "full_lineage_from_gen_0"},
            "pass_if": {"op": "range", "low": 50, "high": 100},
        },
        reader=reader,
    )
    assert out["result"] == "FAIL"


# -- in_range_every_generation --

def test_in_range_every_generation_pass():
    """All gen means in [0, 100] → PASS."""
    series = _make_gen_series(3, 5)
    # gen 0: vals 0,10,20,30,40 → mean=20; gen 1: 100,110,120,130,140 → mean=120; gen 2: 200,... → mean=220
    # Need all gen means in [0, 250]
    reader = _reader_with_series(**{"obs.val": series})
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "range_check_per_generation", "path": "obs.val", "window": "full_lineage_from_gen_0"},
            "pass_if": {"op": "in_range_every_generation", "low": 0, "high": 250},
        },
        reader=reader,
    )
    assert out["result"] == "PASS"
    assert out["evaluated_by"] == "code"
    # measured_value should be a dict of gen → mean
    assert isinstance(out["measured_value"], dict)


def test_in_range_every_generation_fail():
    """gen 2 mean (220) exceeds high=200 → FAIL."""
    series = _make_gen_series(3, 5)
    reader = _reader_with_series(**{"obs.val": series})
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "range_check_per_generation", "path": "obs.val", "window": "full_lineage_from_gen_0"},
            "pass_if": {"op": "in_range_every_generation", "low": 0, "high": 100},
        },
        reader=reader,
    )
    assert out["result"] == "FAIL"


# -- scalar comparators --

def test_lte_comparator_pass():
    series = _make_gen_series(1, 5)  # mean = 20
    reader = _reader_with_series(**{"obs.val": series})
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "generation_average", "path": "obs.val", "window": "full_lineage_from_gen_0"},
            "pass_if": {"op": "<=", "value": 30},
        },
        reader=reader,
    )
    assert out["result"] == "PASS"


def test_gte_comparator_fail():
    series = _make_gen_series(1, 5)  # mean = 20
    reader = _reader_with_series(**{"obs.val": series})
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "generation_average", "path": "obs.val", "window": "full_lineage_from_gen_0"},
            "pass_if": {"op": ">=", "value": 100},
        },
        reader=reader,
    )
    assert out["result"] == "FAIL"


def test_eq_op_alias():
    series = _make_gen_series(1, 1)  # single tick: value=0
    # mean = 0
    series = pl.DataFrame({
        "generation": [0],
        "time": [0.0],
        "abs_time": [0.0],
        "value": [42.0],
    })
    reader = _reader_with_series(**{"obs.val": series})
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "generation_average", "path": "obs.val", "window": "full_lineage_from_gen_0"},
            "pass_if": {"op": "eq", "value": 42},
        },
        reader=reader,
    )
    assert out["result"] == "PASS"


# -- cv_below --

def test_cv_below_pass():
    """Low-variance series → CV < threshold → PASS."""
    vals = [100.0, 101.0, 99.0, 100.5, 100.2]
    series = pl.DataFrame({
        "generation": [0] * len(vals),
        "time": list(range(len(vals))),
        "abs_time": list(range(len(vals))),
        "value": vals,
    })
    reader = _reader_with_series(**{"obs.val": series})
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "range_check_per_generation", "path": "obs.val", "window": "full_lineage_from_gen_0"},
            "pass_if": {"op": "cv_below", "cv_threshold": 0.05},
        },
        reader=reader,
    )
    assert out["result"] == "PASS"
    assert out["evaluated_by"] == "code"
    assert isinstance(out["measured_value"], float)


def test_cv_below_fail():
    """High-variance series → CV > threshold → FAIL."""
    vals = [1.0, 100.0, 1.0, 100.0, 1.0]
    series = pl.DataFrame({
        "generation": [0] * len(vals),
        "time": list(range(len(vals))),
        "abs_time": list(range(len(vals))),
        "value": vals,
    })
    reader = _reader_with_series(**{"obs.val": series})
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "range_check_per_generation", "path": "obs.val", "window": "full_lineage_from_gen_0"},
            "pass_if": {"op": "cv_below", "cv_threshold": 0.05},
        },
        reader=reader,
    )
    assert out["result"] == "FAIL"


# -- median_within_tolerance --

def test_median_within_tolerance_pass():
    vals = [0.9, 1.0, 1.1, 0.95, 1.05]
    series = pl.DataFrame({
        "generation": [0] * len(vals),
        "time": list(range(len(vals))),
        "abs_time": list(range(len(vals))),
        "value": vals,
    })
    reader = _reader_with_series(**{"obs.val": series})
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "rate_match", "path": "obs.val", "window": "full_lineage_from_gen_0"},
            "pass_if": {"op": "median_within_tolerance", "target": 1.0, "tolerance_fraction": 0.5},
        },
        reader=reader,
    )
    assert out["result"] == "PASS"
    assert out["evaluated_by"] == "code"


def test_median_within_tolerance_fail():
    vals = [5.0, 5.5, 6.0]  # median ≈ 5.5, target = 1.0, rel_err = 4.5 >> 0.5
    series = pl.DataFrame({
        "generation": [0] * len(vals),
        "time": list(range(len(vals))),
        "abs_time": list(range(len(vals))),
        "value": vals,
    })
    reader = _reader_with_series(**{"obs.val": series})
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "rate_match", "path": "obs.val", "window": "full_lineage_from_gen_0"},
            "pass_if": {"op": "median_within_tolerance", "target": 1.0, "tolerance_fraction": 0.5},
        },
        reader=reader,
    )
    assert out["result"] == "FAIL"


# -- in_set --

def test_in_set_pass():
    series = pl.DataFrame({
        "generation": [0],
        "time": [0.0],
        "abs_time": [0.0],
        "value": [2.0],
    })
    reader = _reader_with_series(**{"obs.val": series})
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "snapshot_window", "path": "obs.val", "window": "full_lineage_from_gen_0"},
            "pass_if": {"op": "in_set", "set": [1, 2, 3]},
        },
        reader=reader,
    )
    assert out["result"] == "PASS"


# -- periodic_doubling_every_generation --

def _make_doubling_series(n_gens: int = 3) -> pl.DataFrame:
    """Series where each gen has values from 100 to ~200 (doubling)."""
    rows = []
    t_abs = 0.0
    for g in range(n_gens):
        for t in range(20):
            v = 100.0 + 5.0 * t  # 100..195, ratio = 195/100 = 1.95
            rows.append({"generation": g, "time": float(t), "abs_time": t_abs, "value": v})
            t_abs += 1.0
    return pl.DataFrame(rows, schema={
        "generation": pl.Int64, "time": pl.Float64, "abs_time": pl.Float64, "value": pl.Float64
    })


def test_periodic_doubling_every_generation_pass():
    series = _make_doubling_series(3)
    reader = _reader_with_series(**{"obs.val": series})
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "periodicity_check", "path": "obs.val", "window": "every_generation"},
            "pass_if": {"op": "periodic_doubling_every_generation", "tolerance": 0.3},
        },
        reader=reader,
    )
    assert out["result"] == "PASS"
    assert out["evaluated_by"] == "code"
    assert isinstance(out["measured_value"], dict)


def test_periodic_doubling_every_generation_fail():
    """Flat series → ratio ≈ 1.0 → FAIL (not doubling)."""
    rows = [{"generation": g, "time": float(t), "abs_time": float(g * 5 + t), "value": 100.0}
            for g in range(3) for t in range(5)]
    series = pl.DataFrame(rows, schema={
        "generation": pl.Int64, "time": pl.Float64, "abs_time": pl.Float64, "value": pl.Float64
    })
    reader = _reader_with_series(**{"obs.val": series})
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "periodicity_check", "path": "obs.val", "window": "every_generation"},
            "pass_if": {"op": "periodic_doubling_every_generation", "tolerance": 0.1},
        },
        reader=reader,
    )
    assert out["result"] == "FAIL"


# -- exactly_one_initiation_per_generation --

def _make_oric_series(n_gens: int = 3, pattern: list[int] | None = None) -> pl.DataFrame:
    """Build an oriC series: values 1 initially, then 2 mid-gen, then 1 at division."""
    if pattern is None:
        pattern = [1, 1, 1, 2, 2]  # mid-gen replication, sum = 7 per gen
    rows = []
    t_abs = 0.0
    for g in range(n_gens):
        for t, v in enumerate(pattern):
            rows.append({"generation": g, "time": float(t), "abs_time": t_abs, "value": float(v)})
            t_abs += 1.0
    return pl.DataFrame(rows, schema={
        "generation": pl.Int64, "time": pl.Float64, "abs_time": pl.Float64, "value": pl.Float64
    })


def test_exactly_one_initiation_per_generation_pass():
    """One initiation event per generation (value changes from 1 to 2 exactly once)."""
    # Build a series where each gen has exactly 1 initiation "event" (sum of events = 1)
    rows = []
    t_abs = 0.0
    for g in range(3):
        for t in range(5):
            v = 1.0 if t == 2 else 0.0  # exactly 1 event at t=2
            rows.append({"generation": g, "time": float(t), "abs_time": t_abs, "value": v})
            t_abs += 1.0
    series = pl.DataFrame(rows, schema={
        "generation": pl.Int64, "time": pl.Float64, "abs_time": pl.Float64, "value": pl.Float64
    })
    reader = _reader_with_series(**{"obs.val": series})
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "oric_initiations_per_generation", "path": "obs.val", "window": "every_generation"},
            "pass_if": {"op": "exactly_one_initiation_per_generation"},
        },
        reader=reader,
    )
    assert out["result"] == "PASS"
    assert out["evaluated_by"] == "code"


def test_exactly_one_initiation_per_generation_fail():
    """Two initiation events per generation → FAIL."""
    rows = []
    t_abs = 0.0
    for g in range(3):
        for t in range(5):
            v = 1.0 if t in (1, 3) else 0.0  # 2 events per gen
            rows.append({"generation": g, "time": float(t), "abs_time": t_abs, "value": v})
            t_abs += 1.0
    series = pl.DataFrame(rows, schema={
        "generation": pl.Int64, "time": pl.Float64, "abs_time": pl.Float64, "value": pl.Float64
    })
    reader = _reader_with_series(**{"obs.val": series})
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "oric_initiations_per_generation", "path": "obs.val", "window": "every_generation"},
            "pass_if": {"op": "exactly_one_initiation_per_generation"},
        },
        reader=reader,
    )
    assert out["result"] == "FAIL"


# -- unsupported op → agent --

def test_unsupported_op_routes_to_agent():
    series = _make_gen_series(1, 5)
    reader = _reader_with_series(**{"obs.val": series})
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "range_check_per_generation", "path": "obs.val"},
            "pass_if": {"op": "unknown_op_xyz"},
        },
        reader=reader,
    )
    assert out["evaluated_by"] == "agent"


# -- never-guess: verify the module NEVER returns a PASS/FAIL for unresolvable inputs --

def test_never_guesses_pass_for_unknown_observable():
    reader = _basic_reader()
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "range_check_per_generation", "path": "obs.does_not_exist"},
            "pass_if": {"op": "range", "low": 0, "high": 1000},
        },
        reader=reader,
    )
    assert out["evaluated_by"] in ("agent", "needs_rerun")
    assert "result" not in out  # no PASS/FAIL fabricated


def test_never_guesses_pass_for_non_run_data_kind():
    out = se.evaluate_test(
        {"name": "t", "measure": {"kind": "visualization"}},
        reader=None,
    )
    assert out["evaluated_by"] == "agent"
    assert "result" not in out  # no PASS/FAIL fabricated


def test_never_guesses_pass_for_unsupported_window():
    # per_minute_full_lineage is now supported (Task 2); use a truly-unsupported spec.
    reader = _basic_reader()
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "range_check_per_generation", "path": "obs.a", "window": "per_second_full_lineage_xyz"},
            "pass_if": {"op": "range", "low": 0, "high": 1000},
        },
        reader=reader,
    )
    assert out["evaluated_by"] == "agent"
    assert "result" not in out


def test_never_guesses_pass_for_unsupported_op():
    reader = _basic_reader()
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "range_check_per_generation", "path": "obs.a"},
            "pass_if": {"op": "fancy_ml_check"},
        },
        reader=reader,
    )
    assert out["evaluated_by"] == "agent"
    assert "result" not in out


# -- outcome shape consistency --

def test_code_outcome_has_required_fields():
    series = _make_gen_series(1, 5)
    reader = _reader_with_series(**{"obs.val": series})
    out = se.evaluate_test(
        {
            "name": "t",
            "measure": {"kind": "range_check_per_generation", "path": "obs.val", "window": "full_lineage_from_gen_0"},
            "pass_if": {"op": "range", "low": 0, "high": 50},
        },
        reader=reader,
    )
    assert out["result"] in ("PASS", "FAIL", "PARTIAL")
    assert out["evaluated_by"] == "code"
    assert "measured_value" in out
    assert "operator" in out
    assert "detail" in out


def test_agent_outcome_has_required_fields():
    out = se.evaluate_test({"name": "t", "measure": {"kind": "model"}}, reader=None)
    assert out["evaluated_by"] == "agent"
    assert "reason" in out
