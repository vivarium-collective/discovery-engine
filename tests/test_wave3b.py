"""Wave 3b tests — hypotheses support, lifecycle floor, threshold sensitivity,
and the new rigor dimensions (threshold_provenance, metric_calibration,
generality) + claim_scope WARN (critique #6/#16/#25/#9/#20/#21/#22)."""
from discovery_engine import hypotheses as H
from discovery_engine.rigor import study_rigor, investigation_rigor, threshold_sensitivity, GAP, WARN, OK
from discovery_engine.study_verdict import lifecycle_floor, lifecycle_below_floor


def _sev(scorecard, dim_id):
    return next(d["severity"] for d in scorecard["dimensions"] if d["id"] == dim_id)


# ---------------------------------------------------------------------------
# #16 score_support — quantitative finding match
# ---------------------------------------------------------------------------

def test_score_support_supports_and_weakens():
    hyp = {
        "id": "h1",
        "predictions": [
            {"observable": "closure_gap", "expected": {"low": 0.0, "high": 0.1}},
            {"observable": "survival_advantage", "expected": {"op": "<=", "value": 0.0}},
        ],
    }
    studies = [
        {"name": "s1", "findings": [
            {"statement": "tight closure", "evidence": {"measure": "closure_gap", "observed": 0.04}},
        ]},
        {"name": "s2", "findings": [
            {"statement": "it survives better", "evidence": {"measure": "survival_advantage", "observed": 0.7}},
        ]},
    ]
    log = H.score_support(hyp, studies)
    by_study = {e["study"]: e["delta"] for e in log}
    assert by_study["s1"] == "supports"      # 0.04 in [0, 0.1]
    assert by_study["s2"] == "weakens"       # 0.7 not <= 0.0
    assert all(e["discriminating"] for e in log)


def test_score_support_ignores_unmatched_or_nonnumeric():
    hyp = {"id": "h", "predictions": [{"observable": "closure_gap", "expected": {"low": 0, "high": 1}}]}
    studies = [
        {"name": "s", "findings": [
            {"statement": "other measure", "evidence": {"measure": "precariousness", "observed": 0.5}},
            {"statement": "no observed", "evidence": {"measure": "closure_gap"}},
        ]},
    ]
    assert H.score_support(hyp, studies) == []


# ---------------------------------------------------------------------------
# #6 rollup_support — fold alternate_hypotheses by hypothesis_id
# ---------------------------------------------------------------------------

def test_rollup_support_folds_alternates_and_is_pure():
    inv = {"hypotheses": [
        {"id": "h1", "statement": "closure without viability",
         "predictions": [{"observable": "closure_gap", "expected": {"low": 0, "high": 0.1}}]},
    ]}
    studies = [
        {"name": "s1",
         "findings": [{"statement": "tight", "evidence": {"measure": "closure_gap", "observed": 0.05}}],
         "discovery_implications": {"alternate_hypotheses": [
             {"claim": "plain diffusion", "hypothesis_id": "h1", "status": "excluded",
              "evidence_against": ["control still closes"]},
         ]}},
    ]
    out = H.rollup_support(inv, studies)
    # purity: input untouched
    assert "support_log" not in inv["hypotheses"][0]
    log = out["hypotheses"][0]["support_log"]
    deltas = sorted(e["delta"] for e in log)
    assert deltas == ["excludes", "supports", "weakens"]
    summary = H.support_summary(log)
    assert summary == {"supports": 1, "weakens": 1, "excludes": 1, "net": 1}


def test_rollup_support_no_hypotheses_is_noop():
    assert H.rollup_support({"x": 1}, [])["x"] == 1


# ---------------------------------------------------------------------------
# hypothesis_competition investigation dim
# ---------------------------------------------------------------------------

def test_hypothesis_competition_dim():
    # no hypotheses → GAP
    ir = investigation_rigor({}, [])
    assert _sev(ir, "hypothesis_competition") == GAP

    inv = {"hypotheses": [
        {"id": "a", "predictions": [{"observable": "m", "expected": {"low": 0, "high": 1}}]},
        {"id": "b", "predictions": [{"observable": "m", "expected": {"op": ">", "value": 5}}]},
    ]}
    studies = [{"name": "s", "findings": [
        {"statement": "x", "evidence": {"measure": "m", "observed": 0.5}},
    ]}]
    ir2 = investigation_rigor(inv, studies)
    # both hypotheses get a support_log entry from the same finding → OK
    assert _sev(ir2, "hypothesis_competition") == OK


# ---------------------------------------------------------------------------
# #25 lifecycle_floor
# ---------------------------------------------------------------------------

def test_lifecycle_floor_observation_default():
    assert lifecycle_floor({"statement": "x"}, {}) == "observation"


def test_lifecycle_floor_candidate_explanation_for_claim():
    f = {"statement": "x", "tier": "interpretation"}
    assert lifecycle_floor(f, {}) == "candidate-explanation"


def test_lifecycle_floor_tested_vs_alternatives():
    f = {"statement": "x", "tier": "interpretation", "evidence": {"from_test": "survival"}}
    spec = {"alternative_hypotheses": [
        {"claim": "movement", "discriminated_by": "survival", "status": "excluded"}]}
    assert lifecycle_floor(f, spec) == "tested-vs-alternatives"


def test_lifecycle_floor_provisional_claim_on_replication():
    f = {"statement": "x", "tier": "observation"}
    spec = {"robustness": {"n_replicates": 4, "seeds": [0, 1, 2, 3]}}
    assert lifecycle_floor(f, spec) == "provisional-claim"


def test_lifecycle_floor_generalized_on_generality_axes():
    f = {"statement": "x", "generality": {"axes_tested": ["parameter_regime", "geometry"]}}
    assert lifecycle_floor(f, {}) == "generalized"


def test_lifecycle_below_floor_detects_underclaim():
    f = {"statement": "x", "tier": "interpretation", "lifecycle_state": "observation"}
    assert lifecycle_below_floor(f, {}) == "candidate-explanation"
    f2 = {"statement": "x", "tier": "interpretation", "lifecycle_state": "provisional-claim"}
    assert lifecycle_below_floor(f2, {}) is None
    # unknown / absent authored state → None (enum check is separate)
    assert lifecycle_below_floor({"tier": "interpretation"}, {}) is None


# ---------------------------------------------------------------------------
# #9 threshold_provenance dim + threshold_sensitivity
# ---------------------------------------------------------------------------

def test_threshold_provenance_dim():
    # numeric band, no source → GAP
    spec = {"behavior_tests": [{"name": "t", "pass_if": {"low": 0.2, "high": 0.5, "op": "in_range"}}]}
    assert _sev(study_rigor(spec), "threshold_provenance") == GAP
    # provenance.kind declared → OK
    spec2 = {"behavior_tests": [{"name": "t",
             "pass_if": {"low": 0.2, "high": 0.5, "op": "in_range",
                         "provenance": {"kind": "literature", "note": "Boesen 2024"}}}]}
    assert _sev(study_rigor(spec2), "threshold_provenance") == OK
    # cites link → OK
    spec3 = {"behavior_tests": [{"name": "t", "cites": ["boesen2024"],
             "pass_if": {"low": 0.2, "high": 0.5, "op": "in_range"}}]}
    assert _sev(study_rigor(spec3), "threshold_provenance") == OK
    # no numeric band at all → OK (nothing to source)
    assert _sev(study_rigor({}), "threshold_provenance") == OK


def test_threshold_sensitivity_across_cutoffs():
    spec = {
        "behavior_tests": [{"name": "growth", "pass_if": {"low": 0.4, "high": 0.6, "op": "in_range"}}],
        "runs": [{"name": "r", "status": "complete", "outcomes": {"growth": {"result": "PASS", "observed": 0.58}}}],
    }
    rows = threshold_sensitivity(spec, "growth")
    assert [r["delta"] for r in rows] == [-0.2, -0.1, 0.1, 0.2]
    by_delta = {r["delta"]: r["result"] for r in rows}
    # observed 0.58: at -10% band [0.36, 0.54] → FAIL; at +20% [0.48, 0.72] → PASS
    assert by_delta[-0.1] == "FAIL"
    assert by_delta[0.2] == "PASS"


def test_threshold_sensitivity_guards_missing_observed():
    spec = {"behavior_tests": [{"name": "g", "pass_if": {"low": 0, "high": 1, "op": "in_range"}}]}
    assert threshold_sensitivity(spec, "g") == []          # no recorded outcome
    assert threshold_sensitivity(spec, "absent") == []     # no such test


# ---------------------------------------------------------------------------
# #20 metric_calibration dim
# ---------------------------------------------------------------------------

def test_metric_calibration_dim():
    # no ladder → GAP
    assert _sev(study_rigor({}), "metric_calibration") == GAP

    controls = [
        {"name": "neg", "kind": "negative"},
        {"name": "pos", "kind": "positive"},
        {"name": "edge", "kind": "borderline"},
    ]
    # fail + pass, no borderline → WARN
    spec_warn = {"controls": controls,
                 "calibration_ladder": {"metric": "containment", "known_fail": "neg", "known_pass": "pos"}}
    assert _sev(study_rigor(spec_warn), "metric_calibration") == WARN
    # 3 rungs resolving to controls → OK
    spec_ok = {"controls": controls,
               "calibration_ladder": {"metric": "containment", "known_fail": "neg",
                                      "known_pass": "pos", "borderline": "edge"}}
    assert _sev(study_rigor(spec_ok), "metric_calibration") == OK
    # rung naming a non-existent control doesn't count → GAP (only 1 resolves)
    spec_gap = {"controls": [{"name": "neg", "kind": "negative"}],
                "calibration_ladder": {"metric": "m", "known_fail": "neg", "known_pass": "ghost"}}
    assert _sev(study_rigor(spec_gap), "metric_calibration") == GAP


# ---------------------------------------------------------------------------
# #22 generality dim
# ---------------------------------------------------------------------------

def test_generality_dim():
    assert _sev(study_rigor({}), "generality") == GAP
    # only a parameter sweep → WARN
    spec_warn = {"robustness": {"swept_param": "k_on"}}
    assert _sev(study_rigor(spec_warn), "generality") == WARN
    # two axes via finding → OK
    spec_ok = {"findings": [
        {"statement": "x", "generality": {"axes_tested": ["initial_conditions", "geometry"]}}]}
    assert _sev(study_rigor(spec_ok), "generality") == OK
    # sweep + one finding axis = two independent axes → OK
    spec_ok2 = {"robustness": {"parameter_sweep": True},
                "findings": [{"statement": "x", "generality": {"axes_tested": ["alt_implementation"]}}]}
    assert _sev(study_rigor(spec_ok2), "generality") == OK


# ---------------------------------------------------------------------------
# #21 claim_scope WARN
# ---------------------------------------------------------------------------

def test_claim_scope_overreach_warns():
    # theoretical scope on a single-instance result → claim_discipline downgraded to WARN
    spec = {"findings": [
        {"statement": "life becomes mind", "tier": "interpretation",
         "evidence": {"from_test": "agency"}, "claim_scope": "theoretical"}]}
    assert _sev(study_rigor(spec), "claim_discipline") == WARN

    # same finding but with generality evidence → stays OK
    spec_ok = {"findings": [
        {"statement": "life becomes mind", "tier": "interpretation",
         "evidence": {"from_test": "agency"}, "claim_scope": "theoretical",
         "generality": {"axes_tested": ["parameter_regime", "geometry"]}}]}
    assert _sev(study_rigor(spec_ok), "claim_discipline") == OK

    # a modest claim_scope never triggers the downgrade
    spec_modest = {"findings": [
        {"statement": "closure holds", "tier": "interpretation",
         "evidence": {"from_test": "closure"}, "claim_scope": "mechanism"}]}
    assert _sev(study_rigor(spec_modest), "claim_discipline") == OK


# ---------------------------------------------------------------------------
# Linter soft checks — enum validation + lifecycle floor (#21/#22/#25)
# ---------------------------------------------------------------------------

def _lint_findings(spec, tmp_path):
    from discovery_engine.report_linter import (
        _LintContext, _check_finding_scope_generality_lifecycle,
    )
    ctx = _LintContext(ws_root=tmp_path, slug="s", spec=spec)
    _check_finding_scope_generality_lifecycle(ctx)
    return {f.check for f in ctx.findings}


def test_linter_flags_unknown_enums(tmp_path):
    spec = {"findings": [
        {"statement": "x", "claim_scope": "world-domination",
         "generality": {"axes_tested": ["parameter_regime", "time_travel"], "level": "cosmic"},
         "lifecycle_state": "ascended"},
    ]}
    checks = _lint_findings(spec, tmp_path)
    assert "claim_scope_unknown" in checks
    assert "generality_axis_unknown" in checks
    assert "generality_level_unknown" in checks
    assert "lifecycle_state_unknown" in checks


def test_linter_flags_lifecycle_below_floor(tmp_path):
    spec = {"findings": [
        {"statement": "x", "tier": "interpretation",
         "evidence": {"from_test": "t"}, "lifecycle_state": "observation"},
    ]}
    assert "lifecycle_state_below_floor" in _lint_findings(spec, tmp_path)


def test_linter_silent_on_valid_or_absent(tmp_path):
    # valid enums at/above floor → no findings
    spec = {"findings": [
        {"statement": "x", "tier": "interpretation", "claim_scope": "mechanism",
         "generality": {"axes_tested": ["parameter_regime"], "level": "mechanism"},
         "lifecycle_state": "generalized"},
        {"statement": "y"},  # no wave-3b fields at all
    ]}
    assert _lint_findings(spec, tmp_path) == set()
