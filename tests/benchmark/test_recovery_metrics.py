from types import SimpleNamespace

from discovery_engine.benchmark.recovery_metrics import RecoveryMetrics, score_recovery
from discovery_engine.benchmark.recovery_runner import (
    RECOVERY_CONFIGS, RecoveryResult, run_recovery_benchmark)
from discovery_engine.benchmark.recovery_scenario import build_recovery_scenario

GT = SimpleNamespace(true_value=0.55, band={"op": "range", "low": 0.3, "high": 0.8})


def _res(observed, ran=True):
    return RecoveryResult(config_name="x", observed=observed, gap_status="", ran=ran,
                          final_study=None)


def test_score_unit():
    a = score_recovery(_res(0.5), GT)
    assert a.mechanism_recovered is True and abs(a.held_out_error - 0.05) < 1e-9
    b = score_recovery(_res(0.9), GT)
    assert b.mechanism_recovered is False and abs(b.held_out_error - 0.35) < 1e-9
    c = score_recovery(_res(None, ran=False), GT)
    assert c == RecoveryMetrics(False, None, False)


def test_full_recovered_small_error(tmp_path):
    sc = build_recovery_scenario(tmp_path)
    m = score_recovery(run_recovery_benchmark(sc, RECOVERY_CONFIGS["full"]), sc.ground_truth)
    assert m.mechanism_recovered is True and m.held_out_error < 0.25


def test_decoy_not_recovered_larger_error_than_full(tmp_path):
    (tmp_path / "f").mkdir(); (tmp_path / "d").mkdir()
    sc_f = build_recovery_scenario(tmp_path / "f"); sc_d = build_recovery_scenario(tmp_path / "d")
    full = score_recovery(run_recovery_benchmark(sc_f, RECOVERY_CONFIGS["full"]), sc_f.ground_truth)
    decoy = score_recovery(run_recovery_benchmark(sc_d, RECOVERY_CONFIGS["full_decoy"]), sc_d.ground_truth)
    assert decoy.mechanism_recovered is False
    assert decoy.held_out_error > full.held_out_error


def test_no_acquire_not_recovered(tmp_path):
    sc = build_recovery_scenario(tmp_path)
    m = score_recovery(run_recovery_benchmark(sc, RECOVERY_CONFIGS["no_acquire"]), sc.ground_truth)
    assert m.mechanism_recovered is False and m.ran is False and m.held_out_error is None


def test_deterministic(tmp_path):
    (tmp_path / "a").mkdir(); (tmp_path / "b").mkdir()
    sc1 = build_recovery_scenario(tmp_path / "a"); sc2 = build_recovery_scenario(tmp_path / "b")
    assert score_recovery(run_recovery_benchmark(sc1, RECOVERY_CONFIGS["full"]), sc1.ground_truth) == \
           score_recovery(run_recovery_benchmark(sc2, RECOVERY_CONFIGS["full"]), sc2.ground_truth)
