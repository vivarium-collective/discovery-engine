"""G1 from code-21's eukaryogenesis build→run trial: a bare-STRING `measure` (a composite-observable
function `name(rows)->float`, the M-track/workbench convention) must not CRASH the engine.

Before: `run_readiness` did `m = t.get("measure") or {}` — a non-empty string is truthy, so `m` kept
the string and `m.get("kind")` raised `AttributeError: 'str' object has no attribute 'get'`
(discover.py), and `evaluate_test` crashed the same way. That blocked the autonomous run/grade path
for the convention the workbench itself uses. G1 makes both sites handle a bare-string measure
gracefully (readiness-neutral; honestly ungraded). Actually GRADING it (import the composite module,
call `name(rows)`) is the follow-up (G2), a cross-package convergence.
"""
from __future__ import annotations

from discovery_engine.discover import run_readiness
from discovery_engine.study_evaluator import evaluate_test


def _study_with_bare_string_measure() -> dict:
    return {
        "name": "mw-s1",
        "observables": [],
        "behavior_tests": [
            {"name": "lysis-bounded", "measure": "lysis_probability",
             "pass_if": {"op": "<=", "value": 0.1}},
            # a normal dict measure alongside it still works
            {"name": "config-check", "measure": {"kind": "config_value", "path": "p"},
             "pass_if": {"op": ">=", "value": 0}},
        ],
    }


def test_run_readiness_does_not_crash_on_bare_string_measure():
    # before G1 this raised AttributeError at discover.py (m.get on a str)
    out = run_readiness(_study_with_bare_string_measure(), ws_root=None)
    assert isinstance(out, dict)
    assert "ready" in out and "observables" in out
    # the bare-string measure needs no DECLARED observable (computed from the raw trajectory),
    # so it must not be reported as a missing-observable gap
    assert "lysis_probability" not in (out["observables"].get("missing") or [])


def test_evaluate_test_bare_string_measure_is_ungraded_not_a_crash():
    res = evaluate_test({"name": "lysis-bounded", "measure": "lysis_probability",
                         "pass_if": {"op": "<=", "value": 0.1}}, reader=None)
    # honest: not graded by code (needs composite-function resolution), but NO crash
    assert res.get("evaluated_by") != "code"
    assert res.get("result") != "PASS"
