"""run_readiness honors a derived measure's `field` as its identifier (matching the grader) and
diagnoses an unresolved derived computer accurately — not the misleading "no measure.path".

Regression guard for a dogfood finding on viva-island-biogeography: studies whose behavior tests
used ``measure: {kind: derived_scalar, field: <name>}`` were reported "behavior tests with no
measure.path (will SKIP)", sending the author to add a `path`, when the real gap was an
unregistered derived-scalar computer (the test already graded SKIP in the agent bucket).
"""
from __future__ import annotations

from discovery_engine.discover import run_readiness


def _study_derived_field_graded_skip() -> dict:
    return {
        "name": "s-derived",
        "observables": [],
        "behavior_tests": [
            {"name": "corr-test",
             "measure": {"kind": "derived_scalar", "field": "capacity_area_corr"},
             "pass_if": {"op": ">", "value": 0.9}},
        ],
        # a recorded run whose grade SKIPPED (agent bucket) because the computer was unresolvable
        "runs": [{"name": "r1", "outcomes": {"corr-test": "x"},
                  "computed_outcomes": {"corr-test": {
                      "evaluated_by": "agent",
                      "reason": "observable 'capacity_area_corr' not resolvable"}}}],
    }


def test_derived_field_graded_skip_is_unresolved_not_no_path():
    rr = run_readiness(_study_derived_field_graded_skip(), ws_root=None)
    # the field-based derived measure is NOT mis-flagged "no measure.path"
    assert "corr-test" not in (rr.get("unevaluable_tests") or [])
    # it IS surfaced accurately as an unresolved derived computer (ground truth from the SKIP grade)
    assert "corr-test" in (rr.get("unresolved_measures") or [])
    assert any("registered evaluator" in g or "register_derived_scalars" in g
               for g in (rr.get("gaps") or []))


def test_measure_with_no_path_or_field_is_still_unevaluable():
    # a measure whose kind needs a readout but declares NEITHER path NOR field is genuinely
    # ungradeable — that case must still be flagged.
    spec = {"name": "s-nopath", "observables": [],
            "behavior_tests": [{"name": "t", "measure": {"kind": "readout"},
                                "pass_if": {"op": ">", "value": 0}}]}
    rr = run_readiness(spec, ws_root=None)
    assert "t" in (rr.get("unevaluable_tests") or [])


def test_derived_field_not_flagged_when_it_graded_normally():
    # a derived field test that graded by CODE (resolved fine) must not be flagged unresolved.
    spec = {"name": "s-ok", "observables": [],
            "behavior_tests": [{"name": "ok-test",
                                "measure": {"kind": "derived_scalar", "field": "z"},
                                "pass_if": {"op": ">", "value": 0}}],
            "runs": [{"name": "r1", "outcomes": {"ok-test": "x"},
                      "computed_outcomes": {"ok-test": {"evaluated_by": "code", "result": "PASS"}}}]}
    rr = run_readiness(spec, ws_root=None)
    assert "ok-test" not in (rr.get("unresolved_measures") or [])
    assert "ok-test" not in (rr.get("unevaluable_tests") or [])
