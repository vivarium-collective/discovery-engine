"""Benchmark 1 acceptance — the first 'it moved the number'.

Assembles the whole substrate: the manifest + budget (C1/C4), the generation seam that
surfaces the discrepancy and lets the oracle filler acquire the missing mechanism (C2), the
held-out quantitative EvidenceRecord (C3), and the weighted rubric + hard gates (C5/C6). The
full engine recovers the concealed mechanism and predicts the held-out conditions; a
deterministic-only run (no generative skills) does neither, and scores lower.
"""
from discovery_engine.benchmark.gates import check_gates
from discovery_engine.benchmark.manifest import from_dict, load_named
from discovery_engine.benchmark.metrics import score
from discovery_engine.benchmark.rubric import categories_from, score_rubric
from discovery_engine.benchmark.runner import run_benchmark
from discovery_engine.benchmark.scenario import build_scenario
from discovery_engine.benchmark.scenarios.metabolic_regulator import (
    heldout_score,
    regulated,
    unregulated,
)

_DOC = {"id": "b1", "question": "Which mechanism explains the perturbed growth response?",
        "fixture": {}, "budget": {"generation_calls": 24}}


def _run(tmp_path, sub, allowed_skills):
    (tmp_path / sub).mkdir()
    sc = build_scenario(tmp_path / sub, name=sub)
    cfg = from_dict({**_DOC, "id": f"b1-{sub}", "allowed_skills": allowed_skills})
    r = run_benchmark(sc, cfg)
    return r, score(r, sc.ground_truth)


def test_full_beats_deterministic_on_mechanism_and_holdout(tmp_path):
    # the manifest carries the bounded run's budget
    budget = load_named("benchmark_01_metabolic_regulator").to_budget()
    assert budget.generation_calls == 24

    r_full, m_full = _run(tmp_path, "full", ["hypothesize", "critique", "acquire_model"])
    r_det, m_det = _run(tmp_path, "det", [])          # deterministic-only: no generative skills

    # 1. mechanism recovery: full recovers the concealed mechanism; deterministic-only does not
    assert m_full.mechanism_proposed
    assert not m_det.mechanism_proposed

    # 2. held-out: the recovered (regulated) model beats the candidate (unregulated) on unseen conditions
    h_full = heldout_score(regulated)                 # full recovered the regulation
    h_det = heldout_score(unregulated)                # deterministic-only stuck with the candidate
    assert h_full.beats(h_det)
    assert h_full.mean_abs_residual < 1e-9 and h_det.mean_abs_residual > 0.1

    # 3. hard gates clean for both runs (no unreviewed promotion; a held-out report is present)
    g_full = check_gates({"inv": r_full.final_inv, "holdout": h_full})
    g_det = check_gates({"inv": r_det.final_inv, "holdout": h_det})
    assert g_full == [] and g_det == []

    # 4. the weighted rubric: full beats deterministic-only
    rs_full = score_rubric(categories_from(metrics=m_full, holdout=h_full, gate_violations=g_full))
    rs_det = score_rubric(categories_from(metrics=m_det, holdout=h_det, gate_violations=g_det))
    assert rs_full.beats(rs_det)
    assert rs_full.total > 0.5
