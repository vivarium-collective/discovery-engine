# tests/test_test_contract.py
import json
import math
from discovery_engine.test_contract import Expected, value, band, predicate, check, sanitize

def test_band_inside_edge_outside():
    a = check("flux", "Acetate flux", 3.2, band(2.5, 4.0), units="mM/h", cite="Nanchen2006")
    assert a["verdict"] == "within_tol"
    assert a["value"] == 3.2 and a["units"] == "mM/h" and a["citation"] == "Nanchen2006"
    assert math.isclose(a["margin"], 0.7)          # min(3.2-2.5, 4.0-3.2)=0.7
    edge = check("f", "f", 2.5, band(2.5, 4.0))
    assert edge["verdict"] == "within_tol" and math.isclose(edge["margin"], 0.0)
    out = check("f", "f", 5.0, band(2.5, 4.0))
    assert out["verdict"] == "mismatch" and out["margin"] < 0

def test_value_reltol():
    a = check("g", "g", 1.02, value(1.0, tol=0.05))   # |1.02-1|=0.02 <= 0.05
    assert a["verdict"] == "within_tol" and a["margin"] > 0
    b = check("g", "g", 1.10, value(1.0, tol=0.05))
    assert b["verdict"] == "mismatch"

def test_value_comparison_ops():
    assert check("n", "n", 3, value(1, op=">="))["verdict"] == "within_tol"
    assert check("n", "n", 0, value(1, op=">="))["verdict"] == "mismatch"
    assert check("n", "n", 1, value(2, op="<="))["verdict"] == "within_tol"

def test_directional_never_mismatch():
    a = check("d", "d", 5.0, band(0.0, 1.0), severity="directional")
    assert a["verdict"] == "drift" and a["margin"] < 0 and a["severity"] == "directional"

def test_predicate_uses_caller_verdict():
    a = check("p", "p", "n/a", predicate("cell divides"), verdict="within_tol")
    assert a["verdict"] == "within_tol" and a["margin"] is None
    b = check("p", "p", None, predicate("x"))
    assert b["verdict"] == "ungraded" and b["margin"] is None

def test_axis_has_all_v2_keys_and_expected_roundtrips():
    a = check("k", "K", 1.0, band(0.0, 2.0), knob=["kcat"], detail="obs")
    for key in ("id","label","verdict","value","meter","detail","expected","margin","severity","units","knob","citation"):
        assert key in a
    assert a["expected"] == {"kind":"band","value":None,"low":0.0,"high":2.0,"op":"~=","tol":0.05,"statement":None,"units":None}
    assert a["knob"] == ["kcat"]

def test_sanitize_nonfinite():
    assert sanitize({"m": float("nan"), "xs": [float("inf"), 1.0]}) == {"m": None, "xs": [None, 1.0]}

def test_check_nonfinite_observed_is_ungraded():
    a = check("x", "X", float("nan"), band(0.0, 1.0))
    assert a["verdict"] == "ungraded" and a["margin"] is None and a["meter"] is None
    json.dumps(a, allow_nan=False)  # must not raise
