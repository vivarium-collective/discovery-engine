"""Stage 1 (issue #98): config-selection assertions in study_evaluator.

Covers the `config_value` measure, the `equals` op (categorical + numeric with
tolerance), `config:`-referenced expected values (assert an emitted observable
equals the CONFIGURED value), and `_resolve_run_config` (declared params for the
run a test targets).
"""
from __future__ import annotations

import polars as pl

from discovery_engine import study_evaluator as se


class FakeReader:
    def __init__(self, data: dict[str, pl.DataFrame]):
        self._data = data

    def series(self, name: str) -> pl.DataFrame:
        if name not in self._data:
            raise KeyError(name)
        return self._data[name]

    def observables(self) -> list[str]:
        return sorted(self._data)


def _const_series(value: float, n: int = 5) -> pl.DataFrame:
    """Single-generation series with a constant value → windowed mean == value."""
    rows = [
        {"generation": 0, "time": float(i), "abs_time": float(i), "value": float(value)}
        for i in range(n)
    ]
    return pl.DataFrame(rows, schema={
        "generation": pl.Int64, "time": pl.Float64,
        "abs_time": pl.Float64, "value": pl.Float64,
    })


# ── _resolve_run_config ──────────────────────────────────────────────────────

def test_resolve_run_config_baseline_v4():
    spec = {"conditions": {"baseline": {"composite": "x",
            "params": {"geometry": {"kla_correlation": "wells-riley"}}}}}
    cfg = se._resolve_run_config(spec, {"name": "t"})
    assert cfg["geometry"]["kla_correlation"] == "wells-riley"


def test_resolve_run_config_variant_merges_over_baseline():
    spec = {"conditions": {
        "baseline": {"composite": "x", "params": {"interval_s": 10, "mode": "a"}},
        "variants": [{"name": "half", "params": {"interval_s": 5}}],
    }}
    cfg = se._resolve_run_config(spec, {"name": "t", "given": {"run": "variant", "variant": "half"}})
    assert cfg["interval_s"] == 5 and cfg["mode"] == "a"


def test_resolve_run_config_v3_baseline_list():
    spec = {"baseline": [{"name": "b", "composite": "x", "params": {"k": 3}}]}
    assert se._resolve_run_config(spec, {"name": "t"})["k"] == 3


# ── config_value measure ─────────────────────────────────────────────────────

def _cv(path, pass_if):
    return {"name": "t", "measure": {"kind": "config_value", "path": path}, "pass_if": pass_if}


def test_config_value_equals_categorical_pass():
    out = se.evaluate_test(
        _cv("geometry.kla_correlation", {"op": "equals", "value": "wells-riley"}),
        reader=None, config={"geometry": {"kla_correlation": "wells-riley"}})
    assert out["result"] == "PASS" and out["evaluated_by"] == "code"


def test_config_value_equals_categorical_fail():
    out = se.evaluate_test(
        _cv("geometry.kla_correlation", {"op": "equals", "value": "wells-riley"}),
        reader=None, config={"geometry": {"kla_correlation": "gaussian"}})
    assert out["result"] == "FAIL"


def test_config_value_equals_numeric_tolerance():
    out = se.evaluate_test(
        _cv("interval_s", {"op": "equals", "value": 10.0, "tolerance_fraction": 0.01}),
        reader=None, config={"interval_s": 10.02})
    assert out["result"] == "PASS"


def test_config_value_in_set():
    out = se.evaluate_test(_cv("mode", {"op": "in_set", "set": ["a", "b"]}),
                           reader=None, config={"mode": "b"})
    assert out["result"] == "PASS"


def test_config_value_comparator():
    out = se.evaluate_test(_cv("n_seeds", {"op": ">", "value": 4}),
                           reader=None, config={"n_seeds": 8})
    assert out["result"] == "PASS"


def test_config_value_missing_field_routes_to_agent():
    out = se.evaluate_test(_cv("nope.here", {"op": "equals", "value": 1}),
                           reader=None, config={"x": 1})
    assert out["evaluated_by"] == "agent"


# ── observable == configured value (config: reference) ───────────────────────

def _obs_eq_config_spec(observed: float, configured: float):
    return {
        "conditions": {"baseline": {"composite": "x",
                       "params": {"coupling": {"interval_s": configured}}}},
        "behavior_tests": [{
            "name": "emitted-matches-config",
            "measure": {"kind": "range_check_per_generation",
                        "path": "obs.interval", "window": "full_lineage_from_gen_0"},
            "pass_if": {"op": "equals", "config": "coupling.interval_s",
                        "tolerance_fraction": 0.01},
        }],
    }


def test_observable_equals_configured_value_pass():
    reader = FakeReader({"obs.interval": _const_series(20.0)})
    res = se.evaluate_study(_obs_eq_config_spec(observed=20.0, configured=20.0), reader)
    assert res["emitted-matches-config"]["result"] == "PASS"


def test_observable_equals_configured_value_fail():
    reader = FakeReader({"obs.interval": _const_series(9.0)})
    res = se.evaluate_study(_obs_eq_config_spec(observed=9.0, configured=20.0), reader)
    assert res["emitted-matches-config"]["result"] == "FAIL"


def test_equals_op_registered():
    assert se._op_supported("equals")
