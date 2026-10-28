"""Regression tests for the study-evaluator robustness fixes (2026-06-17):

1. NaN-skip in flat-value reductions (a 0/0 ratio at cell birth must not
   poison a mean → previously produced .nan).
2. De-duplicated join keys in multi-observable expressions (a 1-tick birth-stub
   agent repeats a (generation, abs_time) key → previously blew the inner join
   up many-to-many and injected misaligned/NaN values).
3. Incomplete-generation dropping in per-generation reductions (a truncated
   final generation must not drag a steady-state average out of band).
"""
import math

import polars as pl

from discovery_engine.study_evaluator import (
    _clean_floats,
    _complete_generations,
    _eval_expression,
    _apply_op,
    _apply_window,
)


def _series(values, gen=1, t0=0.0):
    n = len(values)
    return pl.DataFrame({
        "generation": [gen] * n,
        "time": [t0 + i for i in range(n)],
        "abs_time": [t0 + i for i in range(n)],
        "value": [float(v) for v in values],
    })


def test_clean_floats_drops_nan_and_null():
    s = pl.Series("value", [1.0, float("nan"), 3.0, None, 5.0], dtype=pl.Float64)
    assert _clean_floats(s) == [1.0, 3.0, 5.0]


def test_complete_generations_drops_truncated_final():
    # gens 3-12 have ~100 ticks, gen 13 (truncated) has 5 → dropped.
    counts = {g: 100 for g in range(3, 13)}
    counts[13] = 5
    keep = _complete_generations(counts)
    assert 13 not in keep
    assert set(range(3, 13)) <= keep


def test_complete_generations_keeps_all_when_few():
    counts = {1: 10, 2: 5}  # <3 gens → nothing reliable to compare
    assert _complete_generations(counts) == {1, 2}


def test_eval_expression_dedups_duplicate_keys():
    # Each token series carries a duplicate (generation, abs_time) row (a birth
    # stub).  Inner join must NOT blow up many-to-many.
    a = _series([2.0, 2.0, 10.0])           # rows at abs_time 0,1,2
    a = pl.concat([a, a.tail(1)])            # duplicate the last key
    b = _series([8.0, 8.0, 90.0])
    b = pl.concat([b, b.tail(1)])
    out = _eval_expression("x / (x + y)", {"x": a.rename({"value": "value"}),
                                           "y": b})
    # 3 unique keys → 3 rows (not 4+ from a cartesian blow-up)
    assert len(out) == 3
    vals = out["value"].to_list()
    assert abs(vals[0] - 0.2) < 1e-9  # 2/(2+8)


def test_atp_fraction_nan_skip_passes():
    # A birth 0/0 NaN tick + in-band real ticks → range op must skip the NaN.
    frac = _series([float("nan"), 0.25, 0.26, 0.25])
    windowed = _apply_window(frac, "full_lineage_from_gen_0")
    out = _apply_op(windowed, {"op": "range", "low": 0.2, "high": 0.5},
                    "generation_average", "range")
    assert out["result"] == "PASS"
    assert not math.isnan(out["measured_value"])
