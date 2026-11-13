from discovery_engine.benchmark.ablation import CONFIGS


def test_full_has_acquire_model():
    assert CONFIGS["full"].registry().get("acquire_model") is not None


def test_no_acquire_model_drops_only_it():
    r = CONFIGS["no_acquire_model"].registry()
    assert r.get("acquire_model") is None
    assert r.get("critique") is not None


def test_full_decoy_keeps_full_registry_but_overrides_oracle():
    cfg = CONFIGS["full_decoy"]
    # Same skills as full (no drops) ...
    assert cfg.drop_skills == frozenset()
    assert cfg.registry().get("acquire_model") is not None
    # ... but a custom oracle factory, unlike the default configs.
    assert cfg.oracle_factory is not None
    assert CONFIGS["full"].oracle_factory is None
