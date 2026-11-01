"""Phase 1 of the unified graded-test ontology: the roll-ups read COMPUTED outcomes, not authored.

Before this, every roll-up (verdict/gate/acceptance/knowledge) read the authored
`canonical_outcomes`, never `computed_outcomes` — so a test computing FAIL still read `passed`
through gate/acceptance/verdict (the ✓met-vs-FAIL bug). `canonical_graded_outcomes` makes
computed authoritative; authored is only a tagged fallback when there's no computed grade.
"""
from __future__ import annotations

from discovery_engine.study_outcomes import canonical_graded_outcomes, graded_outcomes_for_run
from discovery_engine.study_verdict import roll_up_verdict


def _spec(computed_result, authored_result, **run_extra):
    run = {"name": "baseline", "status": "completed"}
    if authored_result is not None:
        run["outcomes"] = {"T": {"result": authored_result}}
    if computed_result is not None:
        run["computed_outcomes"] = {"T": {"result": computed_result, "evaluated_by": "code",
                                          "measured_value": 0.11}}
    run.update(run_extra)
    return {"behavior_tests": [{"name": "T", "measure": {"kind": "derived_scalar", "path": "x"},
                                "pass_if": {"op": ">=", "value": 0.5}}],
            "runs": [run]}


def test_computed_wins_over_authored():
    # computed FAIL, authored passed → the graded result is FAIL (the ✓met-vs-FAIL bug)
    g = canonical_graded_outcomes(_spec("FAIL", "passed"))
    assert g["T"]["result"] == "FAIL"
    # and the study roll-up verdict is NOT passed (it was "passed" before Phase 1)
    v = roll_up_verdict(_spec("FAIL", "passed"))
    assert v["result"] != "passed"


def test_authored_fallback_when_no_computed():
    # no computed grade → fall back to authored, tagged so a view can show "author-asserted"
    g = canonical_graded_outcomes(_spec(None, "passed"))
    assert g["T"]["result"] == "passed"
    assert g["T"]["evaluated_by"] == "authored"


def test_ungraded_computed_falls_back_to_authored():
    g = canonical_graded_outcomes(_spec("ungraded", "passed"))
    assert g["T"]["result"] == "passed"
    assert g["T"]["evaluated_by"] == "authored"


def test_meta_keys_skipped():
    spec = _spec("FAIL", None)
    spec["runs"][0]["computed_outcomes"]["_status"] = "store_less_graded"
    spec["runs"][0]["computed_outcomes"]["_grader"] = {"version": "2-storeless"}
    g = canonical_graded_outcomes(spec)
    assert "_status" not in g and "_grader" not in g
    assert g["T"]["result"] == "FAIL"


def test_mixed_timestamps_do_not_crash():
    # canonical-run selection must be str-safe (viva_workspace.canonical_run crashes on str vs float)
    spec = {"behavior_tests": [{"name": "T", "pass_if": {"op": ">=", "value": 0.5}}],
            "runs": [{"name": "a", "status": "completed", "timestamp": 1700000000.0,
                      "computed_outcomes": {"T": {"result": "PASS"}}},
                     {"name": "b", "status": "completed", "timestamp": "2026-01-01T00:00:00Z",
                      "computed_outcomes": {"T": {"result": "FAIL"}}}]}
    g = canonical_graded_outcomes(spec)  # must not raise
    assert g["T"]["result"] in ("PASS", "FAIL")


def test_graded_outcomes_for_run_unions_computed_first():
    run = {"computed_outcomes": {"A": {"result": "FAIL"}}, "outcomes": {"A": {"result": "passed"},
                                                                        "B": {"result": "passed"}}}
    g = graded_outcomes_for_run(run)
    assert g["A"]["result"] == "FAIL"         # computed wins
    assert g["B"]["result"] == "passed"       # authored-only fallback
    assert g["B"]["evaluated_by"] == "authored"
