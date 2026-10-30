"""Study refinement (DE 2.0) — improve an existing study: replicates, sweep, fidelity.

Gate: the engine derives what a thin study needs from its own rigor/fidelity flags
and produces a refined spec that adds seeds, sweep variants, and/or a higher-fidelity
model — the symmetric partner to study_seeding.
"""
from __future__ import annotations

from discovery_engine import study_refinement as R


def _thin_study():
    return {
        "name": "s4-energetic",
        "baseline": [{"name": "baseline",
                      "composite": "viva_genesis.composites.bioenergetic_internalization.bioenergetic_internalization",
                      "params": {"catalyst_efficiency": 1.0, "v_start": 0.4}}],
        "runs": [{"name": "run-1"}],   # single run → under-replicated, no sweep
    }


def test_needs_flags_underreplication_and_no_sweep():
    needs = R.refinement_needs({"name": "eukaryogenesis"}, _thin_study())
    kinds = {n["kind"] for n in needs}
    assert R.REPLICATE in kinds and R.SWEEP in kinds


def test_refine_adds_replicates():
    out, changes = R.refine_study(_thin_study(), needs=[{"kind": R.REPLICATE}])
    assert out["robustness"]["n_replicates"] >= 3
    assert len(out["robustness"]["seeds"]) >= 3
    assert any(c["kind"] == R.REPLICATE for c in changes)


def test_refine_adds_sweep_variants_around_baseline_param():
    out, changes = R.refine_study(_thin_study(), needs=[{"kind": R.SWEEP}])
    names = [v["name"] for v in out.get("variants", [])]
    assert len(names) == 2                                    # 0.5x and 2x
    assert out["robustness"]["parameter_sweep"] is True
    # swept the first numeric baseline param (catalyst_efficiency) around 1.0 → 0.5 / 2.0
    vals = sorted(v["params"]["catalyst_efficiency"] for v in out["variants"])
    assert vals == [0.5, 2.0]


def test_raise_fidelity_swaps_composite_when_given_else_notes_gap():
    out, changes = R.refine_study(_thin_study(), needs=[{"kind": R.RAISE_FIDELITY}],
                                  higher_fidelity_composite="pkg.composites.hi_fi.hi_fi")
    assert out["baseline"][0]["composite"].endswith("hi_fi")
    # without a composite, it records the gap rather than silently doing nothing
    _, changes2 = R.refine_study(_thin_study(), needs=[{"kind": R.RAISE_FIDELITY}])
    assert any("not applied" in c["detail"] for c in changes2)
