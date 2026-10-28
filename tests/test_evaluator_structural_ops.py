"""Tests for the structural/trajectory ops added 2026-06-17 so that
division-count, max-oriC (re-init), and within-cycle trend tests become
spine-computable (previously agent-only)."""
import polars as pl

from discovery_engine.study_evaluator import _apply_op


def _flat(values, gen=1):
    n = len(values)
    return ("flat", pl.DataFrame({
        "generation": [gen] * n,
        "time": list(range(n)),
        "abs_time": list(range(n)),
        "value": [float(v) for v in values],
    }))


def _per_gen(gen_to_values):
    data = {}
    for g, vals in gen_to_values.items():
        n = len(vals)
        data[g] = pl.DataFrame({
            "generation": [g] * n,
            "time": list(range(n)),
            "abs_time": list(range(n)),
            "value": [float(v) for v in vals],
        })
    return ("per_gen_all", data)


def test_max_le_passes_when_never_exceeds():
    # oriC oscillates 1<->2, never re-initiates → max 2 <= 2
    out = _apply_op(_flat([1, 2, 2, 1, 2]), {"op": "max_le", "value": 2},
                    "snapshot_window", "max_le")
    assert out["result"] == "PASS" and out["measured_value"] == 2.0


def test_max_le_fails_on_reinit_spike():
    # a re-initiation pushes oriC to 4 → max 4 > 2 → FAIL (mean-based ops miss this)
    out = _apply_op(_flat([1, 2, 4, 2, 1]), {"op": "max_le", "value": 2},
                    "snapshot_window", "max_le")
    assert out["result"] == "FAIL" and out["measured_value"] == 4.0


def test_min_ge_basic():
    out = _apply_op(_flat([3.1, 3.5, 3.2]), {"op": "min_ge", "value": 3.0},
                    "snapshot_window", "min_ge")
    assert out["result"] == "PASS"
    out2 = _apply_op(_flat([3.1, 2.9, 3.2]), {"op": "min_ge", "value": 3.0},
                     "snapshot_window", "min_ge")
    assert out2["result"] == "FAIL"


def test_rises_within_cycle_passes_on_gradual_fill():
    # two gens that fill gradually (low early -> high late)
    out = _apply_op(
        _per_gen({3: [0.0, 0.1, 0.2, 0.6, 0.7, 0.8],
                  4: [0.05, 0.1, 0.3, 0.6, 0.75, 0.9]}),
        {"op": "rises_within_cycle", "min_rise": 0.05, "min_fraction": 0.6},
        "trajectory_over_cycle", "rises_within_cycle",
    )
    assert out["result"] == "PASS"


def test_rises_within_cycle_fails_when_prefilled():
    # pre-filled at birth, flat/declining → does not rise
    out = _apply_op(
        _per_gen({3: [0.8, 0.8, 0.7, 0.6, 0.5, 0.4],
                  4: [0.8, 0.75, 0.7, 0.6, 0.55, 0.5]}),
        {"op": "rises_within_cycle", "min_rise": 0.05, "min_fraction": 0.6},
        "trajectory_over_cycle", "rises_within_cycle",
    )
    assert out["result"] == "FAIL"
