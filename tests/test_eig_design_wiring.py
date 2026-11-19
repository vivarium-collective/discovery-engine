"""EIG wiring: rank_study_experiments over a study's declared predictions, and the
expected-information fallback in design_discriminating_study."""
from discovery_engine.phases import experiment_design as D
from discovery_engine.phases.experiment_utility import rank_study_experiments


def _overlapping_inv():
    # two live hypotheses on observable "x" under the SAME regime, bands that OVERLAP
    # (0.35..0.5) so no disjoint pair exists — but still separate enough to discriminate.
    return {"studies": ["s"], "hypotheses": [
        {"id": "low", "study": "s", "status": "open",
         "predictions": [{"observable": "x", "when": {"dose": "hi"},
                          "expected": {"op": "range", "low": 0.0, "high": 0.5}}]},
        {"id": "high", "study": "s", "status": "open",
         "predictions": [{"observable": "x", "when": {"dose": "hi"},
                          "expected": {"op": "range", "low": 0.35, "high": 1.0}}]},
    ]}


def test_rank_study_experiments_recommends_the_separating_regime():
    out = rank_study_experiments(_overlapping_inv(), "s")
    assert out["outcome"] == "experiment"
    assert out["condition"]["observable"] == "x"
    assert out["condition"]["when"] == {"dose": "hi"}
    assert 0.3 <= out["score"] < 0.5          # partial separation, not fully disjoint


def test_rank_study_experiments_abstains_when_bands_coincide():
    inv = {"studies": ["s"], "hypotheses": [
        {"id": "a", "study": "s", "status": "open",
         "predictions": [{"observable": "x", "expected": {"op": "range", "low": 0.0, "high": 1.0}}]},
        {"id": "b", "study": "s", "status": "open",
         "predictions": [{"observable": "x", "expected": {"op": "range", "low": 0.0, "high": 1.0}}]},
    ]}
    out = rank_study_experiments(inv, "s")
    assert out["outcome"] == "stop" and out["reason"] == "no_informative_experiment"


def test_design_falls_back_to_expected_information_when_not_disjoint():
    # the old disjoint-band path returns None for overlapping bands; the fallback designs it
    design = D.design_discriminating_study(_overlapping_inv(), "s")
    assert design is not None
    assert design["selection"]["method"] == "expected-information"
    assert design["measurements"] == ["x"]
    assert design["selection"]["score"] >= 0.3
    assert {h for h in design["hypothesis_contrast"]} == {"low", "high"}


def test_disjoint_design_still_uses_disjoint_band_method():
    inv = {"studies": ["s"], "hypotheses": [
        {"id": "lo", "study": "s", "status": "open",
         "predictions": [{"observable": "x", "expected": {"op": "range", "low": 0.0, "high": 0.2}}]},
        {"id": "hi", "study": "s", "status": "open",
         "predictions": [{"observable": "x", "expected": {"op": "range", "low": 0.8, "high": 1.0}}]},
    ]}
    design = D.design_discriminating_study(inv, "s")
    assert design is not None and design["selection"]["method"] == "disjoint-band"


def test_single_hypothesis_still_yields_no_design():
    inv = {"studies": ["s"], "hypotheses": [
        {"id": "only", "study": "s", "status": "open",
         "predictions": [{"observable": "x", "expected": {"op": ">=", "value": 0.5}}]}]}
    assert D.design_discriminating_study(inv, "s") is None
