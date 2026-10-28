# tests/test_test_builder.py
from discovery_engine.test_contract import TestBuilder, check, band, value

def test_builder_groups_and_overall():
    doc = (TestBuilder(model_ref="m@abc")
           .add("Exchange fluxes", check("ac", "Acetate", 3.2, band(2.5, 4.0)))
           .add("Exchange fluxes", check("glc", "Glucose", 9.0, value(10.0, tol=0.05)))  # |9-10|=1 > 0.5 -> mismatch
           .add("Growth", check("mu", "Growth rate", 0.6, band(0.5, 0.9)))
           .build())
    assert doc["schema"] == "report_card_verdict/v2"
    assert doc["model_ref"] == "m@abc"
    assert set(doc["groups"]) == {"exchange_fluxes", "growth"}
    assert doc["groups"]["exchange_fluxes"]["verdict"] == "mismatch"   # worst of {within_tol, mismatch}
    assert doc["groups"]["growth"]["verdict"] == "within_tol"
    assert doc["overall"] == "mismatch"
    assert len(doc["groups"]["exchange_fluxes"]["axes"]) == 2
    assert doc["groups"]["exchange_fluxes"]["axes"][0]["id"] == "ac"

def test_builder_v1_reader_compatibility():
    # A v1 reader only touches overall + groups[g].verdict + axes[i].{id,label,verdict,value,meter,detail}
    doc = TestBuilder().add("G", check("a", "A", 1.0, band(0.0, 2.0))).build()
    ax = doc["groups"]["g"]["axes"][0]
    for k in ("id", "label", "verdict", "value", "meter", "detail"):
        assert k in ax
    assert doc["overall"] in ("within_tol", "drift", "mismatch", "ungraded")
