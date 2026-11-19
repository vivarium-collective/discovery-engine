"""Declarative benchmark manifest: load/validate, budget + skill bridges, and it drives a run."""
import pytest

from discovery_engine.benchmark.manifest import (
    from_dict,
    load_named,
    validate_manifest,
)

_MIN = {"id": "b", "question": "q?", "fixture": {}, "budget": {}}


def test_bundled_benchmark_1_loads_and_validates():
    m = load_named("benchmark_01_metabolic_regulator")
    assert m.id == "benchmark_01_metabolic_regulator"
    assert m.budget["generation_calls"] == 24
    assert "hypothesize" in m.allowed_skills
    assert "reviewed_finding" in m.required_artifacts


def test_strict_schema_rejects_unknown_top_level_key():
    with pytest.raises(ValueError, match="invalid benchmark manifest"):
        validate_manifest({**_MIN, "budget": {}, "nonsense": 1})


def test_to_budget_caps_match():
    m = from_dict({**_MIN, "budget": {"simulation_runs": 5, "generation_calls": 3}})
    b = m.to_budget()
    assert b.simulation_runs == 5 and b.generation_calls == 3 and b.model_builds is None


def test_registry_restricts_to_allowed_skills():
    full = from_dict({**_MIN, "allowed_skills": ["hypothesize", "critique", "acquire_model"]})
    one = from_dict({**_MIN, "allowed_skills": ["hypothesize"]})
    none = from_dict({**_MIN, "allowed_skills": []})
    assert set(full.registry().kinds()) == {"hypothesize", "critique", "acquire_model"}
    assert set(one.registry().kinds()) == {"hypothesize"}
    assert set(none.registry().kinds()) == set()


def test_manifest_drives_run_and_honors_allowed_skills(tmp_path):
    """A Manifest is a drop-in for the ablation config: run_benchmark consumes it, and
    dropping `acquire_model` from allowed_skills drops the recovered mechanism proposal."""
    from discovery_engine.benchmark.metrics import score
    from discovery_engine.benchmark.runner import run_benchmark
    from discovery_engine.benchmark.scenario import build_scenario

    full = from_dict({**_MIN, "allowed_skills": ["hypothesize", "critique", "acquire_model"]})
    dropped = from_dict({**_MIN, "allowed_skills": ["hypothesize", "critique"]})  # no acquire_model

    (tmp_path / "full").mkdir()
    (tmp_path / "drop").mkdir()
    sc_full = build_scenario(tmp_path / "full", name="full")
    sc_drop = build_scenario(tmp_path / "drop", name="drop")
    r_full = run_benchmark(sc_full, full)
    r_drop = run_benchmark(sc_drop, dropped)

    assert score(r_full, sc_full.ground_truth).mechanism_proposed        # full recovers it
    assert not score(r_drop, sc_drop.ground_truth).mechanism_proposed    # dropping the skill loses it
