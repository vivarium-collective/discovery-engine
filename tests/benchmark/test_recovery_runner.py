from discovery_engine.benchmark.recovery_runner import RECOVERY_CONFIGS, run_recovery_benchmark
from discovery_engine.benchmark.recovery_scenario import build_recovery_scenario


def test_full_runs_in_band(tmp_path):
    r = run_recovery_benchmark(build_recovery_scenario(tmp_path), RECOVERY_CONFIGS["full"])
    assert r.ran is True and r.gap_status == "built"
    assert 0.3 <= r.observed <= 0.8


def test_full_decoy_out_of_band(tmp_path):
    r = run_recovery_benchmark(build_recovery_scenario(tmp_path), RECOVERY_CONFIGS["full_decoy"])
    assert r.ran is True and r.gap_status == "built"
    assert r.observed is not None and not (0.3 <= r.observed <= 0.8)


def test_no_acquire_does_not_run(tmp_path):
    r = run_recovery_benchmark(build_recovery_scenario(tmp_path), RECOVERY_CONFIGS["no_acquire"])
    assert r.ran is False and r.observed is None
