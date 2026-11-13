from discovery_engine.benchmark.ablation import CONFIGS


def test_full_has_acquire_model():
    assert CONFIGS["full"].registry().get("acquire_model") is not None


def test_no_acquire_model_drops_only_it():
    r = CONFIGS["no_acquire_model"].registry()
    assert r.get("acquire_model") is None
    assert r.get("critique") is not None
