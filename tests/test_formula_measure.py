import polars as pl

import discovery_engine.study_evaluator as se


class FakeReader:
    def __init__(self, series_map):
        self._m = series_map

    def series(self, token):
        if token in self._m:
            return self._m[token]
        raise KeyError(token)

    def select(self, sel):
        raise KeyError(sel)


def _series(values):
    n = len(values)
    return pl.DataFrame({
        "generation": [1] * n,
        "time": [float(i) for i in range(n)],
        "abs_time": [float(i) for i in range(n)],
        "value": [float(v) for v in values],
    })


def test_formula_ratio_of_observables_grades():
    reader = FakeReader({
        "current": _series([2.0, 4.0]),
        "baseline": _series([1.0, 2.0]),
    })
    test = {
        "name": "mass-doubles",
        "measure": {"kind": "derived_scalar",
                    "formula": "current / baseline"},
        "pass_if": {"op": "range", "low": 1.9, "high": 2.1},
    }
    out = se.evaluate_test(test, reader, ws_root=None)
    assert out["evaluated_by"] == "code"
    assert out["result"] == "PASS"
