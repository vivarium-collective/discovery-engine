"""G2 — the engine grades a bare-string measure via the study's composite module + the SHARED
viva_emitters resolver (viva-emitters #52), so it grades byte-identically to the workbench instead
of returning ungraded (G1). Here the resolver is stubbed (viva_emitters #52 not yet installed);
the real byte-identity is pinned by viva_emitters' own test_grade_bare_string_measure_is_byte_identical.
"""
from __future__ import annotations

import sys
import types

import pytest

from discovery_engine import study_evaluator as se


@pytest.fixture
def stub_resolver(monkeypatch):
    """Inject a fake viva_emitters.measure_resolver.grade_bare_string_measure that returns the
    composite function's value — the engine's job is to resolve + compare, not to compute."""
    def _factory(value):
        mod = types.ModuleType("viva_emitters.measure_resolver")
        mod.grade_bare_string_measure = lambda reader, composite_module, name: value
        monkeypatch.setitem(sys.modules, "viva_emitters.measure_resolver", mod)
    return _factory


def _test(value_op=("<=", 0.1)):
    op, val = value_op
    return {"name": "lysis-bounded", "measure": "lysis_probability",
            "pass_if": {"op": op, "value": val}}


def test_bare_string_measure_grades_pass(stub_resolver):
    stub_resolver(0.04)                                   # composite fn returns 0.04
    res = se.evaluate_test(_test(("<=", 0.1)), reader=object(), composite_module=object())
    assert res["evaluated_by"] == "code" and res["result"] == "PASS"   # 0.04 <= 0.1


def test_bare_string_measure_grades_fail(stub_resolver):
    stub_resolver(0.6)
    res = se.evaluate_test(_test(("<=", 0.1)), reader=object(), composite_module=object())
    assert res["evaluated_by"] == "code" and res["result"] == "FAIL"   # 0.6 !<= 0.1


def test_no_composite_module_degrades_to_agent():
    res = se.evaluate_test(_test(), reader=object(), composite_module=None)
    assert res["evaluated_by"] == "agent"


def test_no_reader_is_needs_rerun():
    res = se.evaluate_test(_test(), reader=None, composite_module=object())
    assert res["evaluated_by"] == "needs_rerun"


def test_resolver_unavailable_degrades_not_crash():
    # no stub injected -> the real import fails (viva_emitters.measure_resolver not installed) ->
    # honest agent bucket, never a crash
    sys.modules.pop("viva_emitters.measure_resolver", None)
    res = se.evaluate_test(_test(), reader=object(), composite_module=object())
    assert res["evaluated_by"] == "agent" and "measure_resolver" in res["reason"]


def test_evaluate_study_resolves_module_and_grades_end_to_end(stub_resolver, monkeypatch):
    stub_resolver(0.04)
    # stub the composite-module import so no real workspace package is needed
    monkeypatch.setattr(se, "_import_composite_module", lambda cid, ws: types.ModuleType("fake_composite"))
    spec = {"name": "mw-s1",
            "conditions": {"baseline": {"composite": "pkg.composites.x.x"}},
            "behavior_tests": [_test(("<=", 0.1))]}
    out = se.evaluate_study(spec, reader=object(), ws_root="/ws")
    assert out["lysis-bounded"]["evaluated_by"] == "code"
    assert out["lysis-bounded"]["result"] == "PASS"
