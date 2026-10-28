"""PR-B / B1: bands carry a unit and `check` reconciles it dimensionally.

Today `units=` on `check` is a display label only. Here a band can declare its
unit; when the observed value carries a (different) unit, `check` converts before
computing the margin — and an INCOMPATIBLE unit fails loudly instead of silently
passing. Version-tolerant: no units, or no registry, → unchanged behavior.
"""
from __future__ import annotations

from discovery_engine.test_contract import band, check


def test_band_stores_unit():
    assert band(1, 3, units="fg").units == "fg"


def test_convertible_units_reconciled_and_pass():
    # 2e-18 kg == 2 fg; band [1,3] fg → within tolerance
    ax = check("x", "mass", 2e-18, band(1, 3, units="fg"), units="kg")
    assert ax["verdict"] == "within_tol"


def test_convertible_units_out_of_band_mismatch():
    # 5e-18 kg == 5 fg; band [1,3] fg → out of band
    ax = check("x", "mass", 5e-18, band(1, 3, units="fg"), units="kg")
    assert ax["verdict"] == "mismatch"


def test_incompatible_units_fail_loudly():
    ax = check("x", "mass", 1.0, band(1, 3, units="fg"), units="mol")
    assert ax["verdict"] == "mismatch"
    assert "unit mismatch" in (ax.get("detail") or "").lower()


def test_no_units_unchanged():
    ax = check("x", "mass", 2.0, band(1, 3), units=None)
    assert ax["verdict"] == "within_tol"


def test_same_unit_no_conversion():
    ax = check("x", "mass", 2.0, band(1, 3, units="fg"), units="fg")
    assert ax["verdict"] == "within_tol"
