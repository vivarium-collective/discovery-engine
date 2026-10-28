from discovery_engine.study_evaluator import _expected_from_pass_if


def test_band_ops_map_to_band():
    for op in ("range", "in_range", "in_range_every_generation", "generation_average_in_range"):
        e = _expected_from_pass_if({"op": op, "low": 0.6, "high": 0.8}, op)
        assert e.kind == "band" and e.low == 0.6 and e.high == 0.8


def test_comparator_ops_map_to_value():
    assert _expected_from_pass_if({"op": "<=", "value": 5}, "<=") == \
        __import__("discovery_engine.test_contract", fromlist=["value"]).value(5, op="<=")
    assert _expected_from_pass_if({"op": "max_le", "value": 5}, "max_le").op == "<="
    assert _expected_from_pass_if({"op": "min_ge", "threshold": 2}, "min_ge").op == ">="
    assert _expected_from_pass_if({"op": "at_most", "value": 5}, "at_most").op == "<="
    assert _expected_from_pass_if({"operator": "greater-than", "threshold": 2}, "greater-than").op == ">="


def test_tolerance_ops_map_to_value_approx():
    # `==` with an absolute `tolerance` matches _apply_op's _equals_ok (absolute
    # bound: abs(m-t) <= tol) -> a band [target-tol, target+tol], not a relative ~=.
    e = _expected_from_pass_if({"op": "==", "value": 2.0, "tolerance": 0.1}, "==")
    assert e.kind == "band" and e.low == 1.9 and e.high == 2.1
    # `tolerance_fraction` is the RELATIVE option _equals_ok honors separately,
    # and takes precedence over `tolerance` when both are present.
    ef = _expected_from_pass_if({"op": "==", "value": 2.0, "tolerance_fraction": 0.1}, "==")
    assert ef.kind == "value" and ef.op == "~=" and ef.value == 2.0 and ef.tol == 0.1
    efboth = _expected_from_pass_if(
        {"op": "==", "value": 2.0, "tolerance": 0.1, "tolerance_fraction": 0.25}, "==")
    assert efboth.kind == "value" and efboth.op == "~=" and efboth.tol == 0.25   # fraction wins
    # No tolerance at all -> exact equality.
    exact = _expected_from_pass_if({"op": "==", "value": 2.0}, "==")
    assert exact.kind == "value" and exact.op == "==" and exact.value == 2.0

    m = _expected_from_pass_if({"op": "median_within_tolerance", "target": 60, "tolerance_fraction": 0.1},
                               "median_within_tolerance")
    assert m.op == "~=" and m.value == 60 and m.tol == 0.1
    cv = _expected_from_pass_if({"op": "cv_below", "cv_threshold": 0.2}, "cv_below")
    assert cv.op == "<=" and cv.value == 0.2


def test_periodic_doubling_default_tolerance_matches_apply_op():
    # _apply_op defaults the missing `tolerance` to 0.2 (not 0.05).
    e = _expected_from_pass_if({"op": "periodic_doubling_every_generation"},
                               "periodic_doubling_every_generation")
    assert e.kind == "value" and e.op == "~=" and e.value == 2.0 and e.tol == 0.2
    e2 = _expected_from_pass_if({"op": "periodic_doubling_every_generation", "tolerance": 0.05},
                                "periodic_doubling_every_generation")
    assert e2.tol == 0.05


def test_rises_within_cycle_unmapped():
    # measured_value for rises_within_cycle is a per-generation rise-delta dict
    # (peak - early), not the pass-fraction -- grading it against min_fraction
    # would contradict `result`. Left unmapped -> no axis (coverage-safe).
    assert _expected_from_pass_if({"op": "rises_within_cycle", "min_fraction": 0.6},
                                  "rises_within_cycle") is None


def test_categorical_ops_map_to_predicate():
    assert _expected_from_pass_if({"op": "in_set", "set": [1, 2]}, "in_set").kind == "predicate"
    assert _expected_from_pass_if({"op": "!=", "value": 0}, "!=").kind == "predicate"
    assert _expected_from_pass_if({"op": "exactly_one_initiation_per_generation"},
                                  "exactly_one_initiation_per_generation").kind == "predicate"


def test_unknown_op_returns_none():
    assert _expected_from_pass_if({"op": "ratio_at_most", "value": 1}, "ratio_at_most") is None


from discovery_engine.study_evaluator import _grade_axis_from_outcome


def _outcome(result, measured_value):
    return {"result": result, "measured_value": measured_value, "evaluated_by": "code"}


def test_scalar_band_axis_has_signed_margin():
    test = {"name": "atp", "description": "ATP fraction", "cites": ["Kurokawa 1999"],
            "measure": {"units": "fraction"}}
    ax = _grade_axis_from_outcome(test, {"op": "in_range", "low": 0.6, "high": 0.8}, "in_range",
                                  _outcome("FAIL", 0.54))
    assert ax["verdict"] == "mismatch"
    assert ax["margin"] == -0.06 or abs(ax["margin"] - (-0.06)) < 1e-9   # 0.54 - 0.6
    assert ax["severity"] == "hard" and ax["citation"] == "Kurokawa 1999"
    assert ax["units"] == "fraction" and ax["value"] == 0.54


def test_per_generation_keeps_worst_generation():
    test = {"name": "band_every_gen"}
    ax = _grade_axis_from_outcome(test, {"op": "in_range_every_generation", "low": 0.6, "high": 0.8},
                                  "in_range_every_generation",
                                  _outcome("FAIL", {"0": 0.7, "1": 0.5, "2": 0.72}))
    assert ax["verdict"] == "mismatch"                 # gen 1 (0.5) fails
    assert ax["value"] == 0.5                           # worst generation's value
    assert ax["detail"]["per_generation"] == {"0": 0.7, "1": 0.5, "2": 0.72}
    assert ax["detail"]["worst_generation"] == "1"


def test_predicate_axis_verdict_from_result():
    test = {"name": "seeds"}
    ok = _grade_axis_from_outcome(test, {"op": "in_set", "set": [4]}, "in_set", _outcome("PASS", 4))
    assert ok["verdict"] == "within_tol" and ok["margin"] is None
    bad = _grade_axis_from_outcome(test, {"op": "in_set", "set": [4]}, "in_set", _outcome("FAIL", 3))
    assert bad["verdict"] == "mismatch"


def test_soft_severity_flows_through():
    test = {"name": "s", "severity": "soft"}
    ax = _grade_axis_from_outcome(test, {"op": "<=", "value": 5}, "<=", _outcome("PASS", 3))
    assert ax["severity"] == "soft" and ax["verdict"] == "within_tol"


def test_unmapped_op_yields_no_axis():
    assert _grade_axis_from_outcome({"name": "x"}, {"op": "ratio_at_most", "value": 1},
                                    "ratio_at_most", _outcome("PASS", 0.5)) is None


def test_evaluate_test_attaches_axis_and_preserves_result(monkeypatch):
    import discovery_engine.study_evaluator as se

    # Stub the measurement layer so the test is pure: _apply_op returns a known
    # code outcome; evaluate_test must attach the axis and keep result/measured_value.
    def fake_apply_op(windowed, pass_if, kind, op, config=None):
        return se._code_outcome("FAIL", 0.54, "derived/in_range", "0.54 below [0.6,0.8]")

    monkeypatch.setattr(se, "_resolve_series", lambda path, reader: object())
    monkeypatch.setattr(se, "_apply_window", lambda series, w: ("flat", object()))
    monkeypatch.setattr(se, "_is_empty_window", lambda windowed: False)
    monkeypatch.setattr(se, "_validate_window", lambda w: None)
    monkeypatch.setattr(se, "_apply_op", fake_apply_op)

    test = {"name": "atp", "description": "ATP fraction",
            "measure": {"kind": "derived", "formula": "x", "window": "full_lineage_from_gen_0"},
            "pass_if": {"op": "in_range", "low": 0.6, "high": 0.8}, "cites": ["Kurokawa 1999"]}
    out = se.evaluate_test(test, reader=object())
    assert out["result"] == "FAIL" and out["measured_value"] == 0.54   # unchanged
    assert out["evaluated_by"] == "code"
    assert out["axis"]["verdict"] == "mismatch"
    assert abs(out["axis"]["margin"] - (-0.06)) < 1e-9


def test_agent_bucket_has_no_axis(monkeypatch):
    import discovery_engine.study_evaluator as se
    out = se.evaluate_test({"measure": {"kind": "totally_unknown"}}, reader=object())
    assert out["evaluated_by"] == "agent" and "axis" not in out
