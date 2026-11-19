"""The baseline roster + the full-vs-baseline comparison through the rubric + CI machinery."""
import pytest

from discovery_engine.benchmark.baselines import BASELINES, manifest_for


def test_roster_registries_match_allowed_skills():
    assert set(manifest_for("full").registry().kinds()) == {"hypothesize", "critique", "acquire_model"}
    assert set(manifest_for("deterministic_only").registry().kinds()) == set()
    assert "critique" not in manifest_for("no_critique").registry().kinds()
    assert "acquire_model" not in manifest_for("no_build").registry().kinds()
    assert set(BASELINES) == {"full", "deterministic_only", "no_critique", "no_build"}
    with pytest.raises(ValueError):
        manifest_for("no_design")       # Wave-B skill; not yet expressible


def test_full_beats_deterministic_through_the_rubric_matrix(tmp_path):
    from discovery_engine.benchmark.gates import check_gates
    from discovery_engine.benchmark.matrix import aggregate_matrix, beats_with_ci
    from discovery_engine.benchmark.metrics import score
    from discovery_engine.benchmark.rubric import categories_from, score_rubric
    from discovery_engine.benchmark.runner import run_benchmark
    from discovery_engine.benchmark.scenario import build_scenario
    from discovery_engine.benchmark.scenarios.metabolic_regulator import (
        heldout_score, regulated, unregulated)

    def rubric_for(sub, baseline, model):
        (tmp_path / sub).mkdir()
        sc = build_scenario(tmp_path / sub, name=sub)
        r = run_benchmark(sc, manifest_for(baseline))
        m = score(r, sc.ground_truth)
        h = heldout_score(model)
        g = check_gates({"inv": r.final_inv, "holdout": h})
        return score_rubric(categories_from(metrics=m, holdout=h, gate_violations=g))

    full = rubric_for("full", "full", regulated)                  # recovers + regulated model
    det = rubric_for("det", "deterministic_only", unregulated)    # neither

    assert full.beats(det)
    matrix = aggregate_matrix({"full": [full], "deterministic_only": [det]})
    assert beats_with_ci(matrix["full"], matrix["deterministic_only"])
