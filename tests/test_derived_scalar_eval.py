import polars as pl
import pytest

import discovery_engine.study_evaluator as se


class FakeReader:
    """Knows only the observables in `series_map`; everything else 404s."""
    def __init__(self, series_map):
        self._m = series_map

    def series(self, token):
        if token in self._m:
            return self._m[token]
        raise KeyError(token)

    def select(self, sel):  # never used in these tests
        raise KeyError(sel)


def _flat(value):
    return pl.DataFrame({
        "generation": [1], "time": [0.0], "abs_time": [0.0], "value": [value],
    })


def test_derived_scalar_via_registry(monkeypatch):
    # field is NOT emitted; a workspace computer supplies it.
    monkeypatch.setattr(
        se, "load_workspace_derived_scalars",
        lambda ws_root: {"growth_is_monotonic": lambda r, t, w: 1.0},
    )
    reader = FakeReader({})  # no emitted observables
    test = {
        "name": "growth-monotonic",
        "measure": {"kind": "derived_scalar", "field": "growth_is_monotonic"},
        "pass_if": {"op": "range", "low": 1.0, "high": 1.0},
    }
    out = se.evaluate_test(test, reader, ws_root="/tmp/ws")
    assert out["evaluated_by"] == "code"
    assert out["result"] == "PASS"
    assert out["measured_value"] == 1.0
    assert "axis" in out


def test_unresolved_field_no_computer_is_agent(monkeypatch):
    monkeypatch.setattr(se, "load_workspace_derived_scalars", lambda ws_root: {})
    reader = FakeReader({})
    test = {
        "name": "x",
        "measure": {"kind": "derived_scalar", "field": "not_emitted"},
        "pass_if": {"op": "range", "low": 1.0, "high": 1.0},
    }
    out = se.evaluate_test(test, reader, ws_root="/tmp/ws")
    assert out["evaluated_by"] == "agent"


def test_emitted_field_ignores_registry(monkeypatch):
    # Regression: an emitted observable must NOT consult the registry.
    called = {"n": 0}
    def _reg(ws_root):
        called["n"] += 1
        return {}
    monkeypatch.setattr(se, "load_workspace_derived_scalars", _reg)
    reader = FakeReader({"cell_mass": _flat(2.0)})
    test = {
        "name": "mass",
        "measure": {"kind": "derived_scalar", "field": "cell_mass"},
        "pass_if": {"op": "range", "low": 1.0, "high": 3.0},
    }
    out = se.evaluate_test(test, reader, ws_root="/tmp/ws")
    assert out["evaluated_by"] == "code"
    assert out["result"] == "PASS"
    assert called["n"] == 0  # registry never consulted for an emitted field
