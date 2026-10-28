"""Tests for the review-integration rigor dims (G2 / G3 / G6).

Each dim is CONDITIONALLY appended (like the confirmatory-only preregistration
dim): it only appears for the claim class it polices, so the dimension count of
every other study is unchanged. For each dim: a spec that GAPs (missing field),
a spec that passes (field present) — proving the dim is real, not vacuous.
"""
from discovery_engine.rigor import study_rigor, GAP, WARN, OK


def _sev(scorecard, dim_id):
    return next(d["severity"] for d in scorecard["dimensions"] if d["id"] == dim_id)


def _dim_ids(scorecard):
    return {d["id"] for d in scorecard["dimensions"]}


def _detail(scorecard, dim_id):
    return next(d["detail"] for d in scorecard["dimensions"] if d["id"] == dim_id)


# ---------------------------------------------------------------------------
# Back-compat: the new dims are silent everywhere they don't apply.
# ---------------------------------------------------------------------------

def test_new_dims_silent_on_empty_and_plain_specs():
    # The empty-spec dimension count is unchanged (the existing invariant).
    assert study_rigor({})["score"]["total"] == 12
    assert study_rigor(None)["score"]["total"] == 12
    # A plain non-causal, non-equivalence, non-converting study gets none of them.
    sc = study_rigor({"name": "s", "runs": [{"name": "r1"}],
                      "findings": [{"statement": "It works."}]})
    for dim_id in ("statistical_power", "held_out_generalization", "conservation_ledger"):
        assert dim_id not in _dim_ids(sc)


def test_g2_does_not_bite_deterministic_or_noncausal_studies():
    # Deterministic (no stochastic flag, single run) + explicit causal claim_type
    # → still silent: G2 only bites STOCHASTIC contrasts.
    det = {"findings": [{"statement": "x", "claim_type": "causal"}],
           "runs": [{"name": "r1"}]}
    assert "statistical_power" not in _dim_ids(study_rigor(det))
    # Stochastic + interpretation finding but NO declared arm contrast and no
    # causal claim_type → silent (conservative detection).
    interp = {"robustness": {"seeds": [0, 1, 2, 3, 4]},
              "findings": [{"statement": "x", "tier": "interpretation",
                            "evidence": {"from_test": "t"}}]}
    assert "statistical_power" not in _dim_ids(study_rigor(interp))


# ---------------------------------------------------------------------------
# G2 — Statistical power for causal stochastic claims.
# ---------------------------------------------------------------------------

def _causal_base(**extra):
    spec = {
        "name": "causal-study",
        "robustness": {"n_replicates": 3, "seeds": [0, 1, 2]},
        "arms": ["treatment", "control"],
        "findings": [{"statement": "arm A beats arm B", "claim_type": "causal",
                      "tier": "interpretation", "evidence": {"from_test": "contrast"}}],
    }
    spec.update(extra)
    return spec


def test_g2_gap_when_no_statistics_declared():
    # n=3 (the old replication floor) and no statistics block → GAP naming the
    # missing pieces — the >=3 floor is NOT statistical power.
    sc = study_rigor(_causal_base())
    assert _sev(sc, "statistical_power") == GAP
    d = _detail(sc, "statistical_power")
    assert "n>=20" in d
    assert "p-value" in d
    assert "drift-null" in d
    assert "ensemble" in d


def test_g2_warn_when_test_declared_but_underpowered():
    # A declared rank test with a p-value but only 5 per arm and no drift-null
    # → WARN listing the specific missing pieces.
    sc = study_rigor(_causal_base(
        robustness={"n_replicates": 5, "seeds": [0, 1, 2, 3, 4]},
        statistics={"test": "mann-whitney-u", "p_value": 0.01, "effect_size": 0.4,
                    "gate_on": "ensemble"},
    ))
    assert _sev(sc, "statistical_power") == WARN
    d = _detail(sc, "statistical_power")
    assert "n>=20" in d and "found 5" in d
    assert "drift-null" in d


def test_g2_gap_when_gated_on_single_flagship_seed():
    # Full stats but the gate reads one flagship seed → GAP (actively wrong).
    sc = study_rigor(_causal_base(
        statistics={"test": "mann-whitney-u", "p_value": 0.001, "effect_size": 0.6,
                    "n_per_arm": 24, "null_arm": "drift-null", "gate_on": "flagship_seed"},
    ))
    assert _sev(sc, "statistical_power") == GAP
    assert "single seed" in _detail(sc, "statistical_power")


def test_g2_ok_when_fully_powered():
    sc = study_rigor(_causal_base(
        controls=[{"name": "drift-null arm", "kind": "null",
                   "hypothesis": "no effect under drift alone",
                   "observed": "no-effect", "result": "PASS"}],
        statistics={"test": "mann-whitney-u", "p_value": 0.003, "effect_size": 0.61,
                    "n_per_arm": 24, "gate_on": "ensemble"},
    ))
    assert _sev(sc, "statistical_power") == OK


def test_g2_triggers_on_interpretation_finding_with_declared_arms():
    # No explicit claim_type: a tier=interpretation finding + a declared arm
    # contrast + stochasticity is enough to put the claim in scope.
    spec = {
        "robustness": {"seeds": [0, 1, 2]},
        "contrast": "motile vs non-motile",
        "findings": [{"statement": "motility causes the survival gain",
                      "tier": "interpretation", "evidence": {"from_test": "t"}}],
    }
    assert "statistical_power" in _dim_ids(study_rigor(spec))


# ---------------------------------------------------------------------------
# G3 — Held-out generalization for substitutability / equivalence claims.
# ---------------------------------------------------------------------------

def _swap_base(**extra):
    spec = {
        "name": "swap-study",
        "findings": [{"statement": "the surrogate is substitutable for the "
                                   "mechanistic module — same interface, different mechanism",
                      "claim_type": "substitutability",
                      "evidence": {"from_test": "swap"}}],
    }
    spec.update(extra)
    return spec


def test_g3_silent_without_equivalence_claim():
    spec = {"findings": [{"statement": "growth rate is 0.4/h",
                          "tier": "observation"}]}
    assert "held_out_generalization" not in _dim_ids(study_rigor(spec))


def test_g3_gap_when_no_held_out_condition_declared():
    sc = study_rigor(_swap_base())
    assert _sev(sc, "held_out_generalization") == GAP
    d = _detail(sc, "held_out_generalization")
    assert "held-out" in d
    assert "surrogate calibration" in d


def test_g3_warn_when_test_equals_train():
    # Agreement only on the tuned condition → surrogate calibration, not yet
    # mechanism-independence.
    sc = study_rigor(_swap_base(
        held_out={"train": ["baseline"], "test": ["baseline"]},
        degrees_of_freedom={"free_parameters": 3, "matched_observables": 7},
    ))
    assert _sev(sc, "held_out_generalization") == WARN
    assert "surrogate calibration" in _detail(sc, "held_out_generalization")


def test_g3_warn_when_no_dof_statement():
    sc = study_rigor(_swap_base(
        held_out={"train": ["baseline"], "test": ["perturbed-nutrient"]},
    ))
    assert _sev(sc, "held_out_generalization") == WARN
    assert "degrees-of-freedom" in _detail(sc, "held_out_generalization")


def test_g3_ok_with_held_out_and_dof():
    sc = study_rigor(_swap_base(
        held_out={"train": ["baseline"], "test": ["perturbed-nutrient"]},
        degrees_of_freedom={"free_parameters": 3, "matched_observables": 7},
    ))
    assert _sev(sc, "held_out_generalization") == OK


def test_g3_detects_claim_from_finding_text():
    # No claim_type field — the finding text carries the equivalence signal.
    spec = {"findings": [{"statement": "the reduced model is a drop-in surrogate "
                                       "for the full network"}]}
    sc = study_rigor(spec)
    assert _sev(sc, "held_out_generalization") == GAP


# ---------------------------------------------------------------------------
# G6 — Conservation ledger across representation conversions.
# ---------------------------------------------------------------------------

def test_g6_silent_without_declared_conversion():
    spec = {"findings": [{"statement": "cells sort by adhesion"}]}
    assert "conservation_ledger" not in _dim_ids(study_rigor(spec))


def test_g6_gap_when_conversion_declared_without_ledger():
    sc = study_rigor({"representation_conversion": {
        "from": "lattice_pixels", "to": "particles", "quantity": "mass"}})
    assert _sev(sc, "conservation_ledger") == GAP
    d = _detail(sc, "conservation_ledger")
    assert "ledger" in d
    assert "lattice_pixels->particles" in d


def test_g6_warn_when_ledger_named_but_unverified():
    sc = study_rigor({"representation_conversion": {
        "from": "lattice_pixels", "to": "particles", "quantity": "mass",
        "ledger": {"test": "mass_ledger"}}})
    assert _sev(sc, "conservation_ledger") == WARN
    assert "PASS" in _detail(sc, "conservation_ledger")


def test_g6_ok_when_ledger_verified():
    sc = study_rigor({"representation_conversion": {
        "from": "lattice_pixels", "to": "particles", "quantity": "mass",
        "ledger": {"test": "mass_ledger", "result": "PASS"}}})
    assert _sev(sc, "conservation_ledger") == OK


def test_g6_supports_a_list_of_conversions():
    sc = study_rigor({"representation_conversions": [
        {"from": "field_mass", "to": "flux", "quantity": "mass",
         "ledger": {"test": "flux_ledger", "result": "PASS"}},
        {"from": "lattice_pixels", "to": "particles", "quantity": "volume"},
    ]})
    # One conversion is unledgered → the dim GAPs and names it.
    assert _sev(sc, "conservation_ledger") == GAP
    assert "lattice_pixels->particles" in _detail(sc, "conservation_ledger")


def test_g6_text_signal_prompts_declaration():
    # A finding that clearly describes converting a conserved quantity, with no
    # declared representation_conversion → GAP asking for the declaration.
    spec = {"findings": [{"statement": "we converted lattice pixels to particles "
                                       "for the off-lattice phase"}]}
    sc = study_rigor(spec)
    assert _sev(sc, "conservation_ledger") == GAP
    assert "representation_conversion" in _detail(sc, "conservation_ledger")
