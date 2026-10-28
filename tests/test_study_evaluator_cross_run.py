"""Stage 2 (issue #98): cross-run assertions (run_delta + given.compare_to)."""
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


def _series(times: list[float], values: list[float]) -> pl.DataFrame:
    return pl.DataFrame(
        [{"generation": 0, "time": float(t), "abs_time": float(t), "value": float(v)}
         for t, v in zip(times, values)],
        schema={"generation": pl.Int64, "time": pl.Float64,
                "abs_time": pl.Float64, "value": pl.Float64},
    )


# ── _run_delta (pure computation) ────────────────────────────────────────────

def test_run_delta_identical_is_zero():
    a = _series([0, 1, 2, 3], [1, 2, 3, 4])
    assert se._run_delta(a, a, "time", "max_abs_diff") == 0.0


def test_run_delta_constant_offset_max():
    a = _series([0, 1, 2, 3], [1, 2, 3, 4])
    b = _series([0, 1, 2, 3], [1.1, 2.1, 3.1, 4.1])
    assert abs(se._run_delta(a, b, "time", "max_abs_diff") - 0.1) < 1e-9


def test_run_delta_time_align_interpolates_onto_shared_grid():
    # both lie on value == abs_time; compare is coarser but interpolates exactly
    a = _series([0, 1, 2, 3, 4], [0, 1, 2, 3, 4])
    b = _series([0, 2, 4], [0, 2, 4])
    assert se._run_delta(a, b, "time", "max_abs_diff") < 1e-9


def test_run_delta_metrics():
    a = _series([0, 1, 2], [0, 0, 0])
    b = _series([0, 1, 2], [0, 3, 4])   # diffs: 0, 3, 4
    assert se._run_delta(a, b, "index", "max_abs_diff") == 4.0
    assert se._run_delta(a, b, "index", "final_abs_diff") == 4.0
    assert abs(se._run_delta(a, b, "index", "mean_abs_diff") - (7 / 3)) < 1e-9
    assert abs(se._run_delta(a, b, "index", "rmse") - (25 / 3) ** 0.5) < 1e-9


def test_run_delta_non_overlapping_time_is_none():
    a = _series([0, 1, 2], [1, 1, 1])
    b = _series([10, 11, 12], [1, 1, 1])
    assert se._run_delta(a, b, "time", "max_abs_diff") is None


# ── _evaluate_run_delta via evaluate_test ────────────────────────────────────

def _xrun_test(metric="max_abs_diff", tol=0.05):
    return {
        "name": "do-converges",
        "given": {"compare_to": {"run": "baseline"}},
        "measure": {"kind": "run_delta", "of": {"readout": "obs.do"},
                    "align": "time", "metric": metric},
        "pass_if": {"op": "<", "value": tol},
    }


def test_run_delta_evaluate_pass():
    primary = FakeReader({"obs.do": _series([0, 1, 2], [5.0, 5.0, 5.0])})
    compare = FakeReader({"obs.do": _series([0, 1, 2], [5.0, 5.01, 4.99])})
    out = se.evaluate_test(_xrun_test(), primary, run_opener=lambda sel: compare)
    assert out["result"] == "PASS" and out["evaluated_by"] == "code"


def test_run_delta_evaluate_fail():
    primary = FakeReader({"obs.do": _series([0, 1, 2], [5.0, 5.0, 5.0])})
    compare = FakeReader({"obs.do": _series([0, 1, 2], [5.0, 5.2, 4.8])})
    out = se.evaluate_test(_xrun_test(), primary, run_opener=lambda sel: compare)
    assert out["result"] == "FAIL"


def test_run_delta_without_opener_needs_rerun():
    primary = FakeReader({"obs.do": _series([0, 1], [1, 1])})
    out = se.evaluate_test(_xrun_test(), primary, run_opener=None)
    assert out["evaluated_by"] == "needs_rerun"


def test_run_delta_without_compare_to_routes_to_agent():
    primary = FakeReader({"obs.do": _series([0, 1], [1, 1])})
    test = _xrun_test()
    test["given"] = {}
    out = se.evaluate_test(test, primary, run_opener=lambda sel: primary)
    assert out["evaluated_by"] == "agent"


def test_run_delta_unresolvable_compare_routes_to_agent():
    primary = FakeReader({"obs.do": _series([0, 1], [1, 1])})
    out = se.evaluate_test(_xrun_test(), primary, run_opener=lambda sel: None)
    assert out["evaluated_by"] == "agent"


# ── _select_run_entry (run-selection convention, Stage 2b) ───────────────────

def test_select_run_entry_variant_by_variant_field():
    runs = [{"name": "b", "canonical": True}, {"name": "v", "variant": "half"}]
    assert se._select_run_entry(runs, {"run": "variant", "variant": "half"})["name"] == "v"


def test_select_run_entry_variant_by_name():
    runs = [{"name": "b", "canonical": True}, {"name": "half"}]
    assert se._select_run_entry(runs, {"run": "variant", "variant": "half"})["name"] == "half"


def test_select_run_entry_baseline_prefers_canonical():
    runs = [{"name": "v", "variant": "x"}, {"name": "b", "canonical": True}]
    assert se._select_run_entry(runs, {"run": "baseline"})["name"] == "b"


def test_select_run_entry_baseline_falls_back_to_no_variant():
    runs = [{"name": "v", "variant": "x"}, {"name": "b"}]
    assert se._select_run_entry(runs, {"run": "baseline"})["name"] == "b"


def test_select_run_entry_unresolvable_variant_is_none():
    runs = [{"name": "b", "canonical": True}]
    assert se._select_run_entry(runs, {"run": "variant", "variant": "nope"}) is None


# ── compute_outcomes cross-run integration (Stage 2b) ────────────────────────

def test_compute_outcomes_cross_run_attaches_to_primary(tmp_path):
    from unittest.mock import patch
    import yaml

    base_store = tmp_path / "base_store"; (base_store / "history").mkdir(parents=True)
    var_store = tmp_path / "var_store"; (var_store / "history").mkdir(parents=True)
    study_dir = tmp_path / "study"; study_dir.mkdir()
    (study_dir / "study.yaml").write_text(
        "name: xrun-study\n"
        "conditions:\n  baseline: {composite: x, params: {}}\n"
        "runs:\n"
        f"- {{name: baseline-run, canonical: true, emitter: {{store: {base_store}}}}}\n"
        f"- {{name: interval-half, variant: interval-half, emitter: {{store: {var_store}}}}}\n"
        "behavior_tests:\n"
        "- name: do-converges\n"
        "  given: {run: variant, variant: interval-half, compare_to: {run: baseline}}\n"
        "  measure: {kind: run_delta, of: {readout: 'obs.do'}, align: time, metric: max_abs_diff}\n"
        "  pass_if: {op: '<', value: 0.05}\n",
        encoding="utf-8",
    )

    def reader_for(store, kind=None):
        if str(store) == str(base_store):
            return FakeReader({"obs.do": _series([0, 1, 2], [5.0, 5.0, 5.0])})
        return FakeReader({"obs.do": _series([0, 1, 2], [5.0, 5.01, 4.99])})

    with patch("viva_emitters.RunReader") as mock_cls:
        mock_cls.open.side_effect = reader_for
        se.compute_outcomes(study_dir)

    doc = yaml.safe_load((study_dir / "study.yaml").read_text())
    var_run = next(r for r in doc["runs"] if r.get("variant") == "interval-half")
    base_run = next(r for r in doc["runs"] if r.get("canonical"))
    # cross-run outcome attaches to the PRIMARY (variant) run, once
    assert var_run["computed_outcomes"]["do-converges"]["result"] == "PASS"
    # and NOT duplicated onto the baseline run
    assert "do-converges" not in (base_run.get("computed_outcomes") or {})
