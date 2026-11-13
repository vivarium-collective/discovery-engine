from discovery_engine.benchmark.ablation import CONFIGS
from discovery_engine.benchmark.metrics import score
from discovery_engine.benchmark.runner import BenchmarkResult, run_benchmark
from discovery_engine.benchmark.scenario import build_scenario


def test_full_result_scores_true(tmp_path):
    sc = build_scenario(tmp_path)
    m = score(run_benchmark(sc, CONFIGS["full"], oracle=CONFIGS["full"].oracle(sc.ground_truth)),
              sc.ground_truth)
    assert m.mechanism_proposed is True and m.proposal_count >= 1
    assert m.overreach_critiqued is True


def test_degraded_result_no_keyerror(tmp_path):
    sc = build_scenario(tmp_path)
    m = score(BenchmarkResult("x", {}, [], 0, []), sc.ground_truth)
    assert m.mechanism_proposed is False and m.proposal_count == 0
    assert m.acquire_model_skipped is False
    assert m.overreach_critiqued is False
