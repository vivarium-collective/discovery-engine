from discovery_engine.benchmark.ablation import CONFIGS
from discovery_engine.benchmark.metrics import score
from discovery_engine.benchmark.runner import run_benchmark
from discovery_engine.benchmark.scenario import build_scenario


def test_full_records_proposal(tmp_path):
    r = run_benchmark(build_scenario(tmp_path), CONFIGS["full"])
    assert len(r.recorded_proposals) >= 1


def test_deterministic(tmp_path):
    ms = []
    for n in ("a", "b"):
        d = tmp_path / n
        d.mkdir()
        sc = build_scenario(d)
        ms.append(score(run_benchmark(sc, CONFIGS["full"]), sc.ground_truth))
    assert ms[0] == ms[1]


def test_ablation_records_none_and_skips(tmp_path):
    r = run_benchmark(build_scenario(tmp_path), CONFIGS["no_acquire_model"])
    assert r.recorded_proposals == []
    assert any((s.get("kind") if isinstance(s, dict) else s) == "acquire_model" for s in r.skipped)


def test_decoy_records_wrong_mechanism(tmp_path):
    sc = build_scenario(tmp_path)
    cfg = CONFIGS["full_decoy"]
    r = run_benchmark(sc, cfg, oracle=cfg.oracle(sc.ground_truth))
    # A proposal IS recorded (the decoy is well-formed) ...
    assert len(r.recorded_proposals) >= 1
    # ... but its mechanism is the decoy, never the concealed truth.
    mechs = {p.get("mechanism") for p in r.recorded_proposals}
    assert sc.ground_truth.decoy_mechanism in mechs
    assert sc.ground_truth.concealed_mechanism not in mechs
