"""Tests for study_evaluator Tasks 1 & 2 — bulk-id/literal-index fallback + per_minute window.

Task 1: _resolve_series falls back to reader.select for bulk ids and literal indices.
Task 2: per_minute_full_lineage rate window.

Run with: .venv/bin/python -m pytest tests/test_study_evaluator_via_readouts.py -v
"""
from __future__ import annotations

import polars as pl
import pytest

from discovery_engine import study_evaluator as se


# ---------------------------------------------------------------------------
# Fake reader that supports both series() and select()
# ---------------------------------------------------------------------------

def _make_series(rows: list[tuple[int, float, float, float]]) -> pl.DataFrame:
    """Build a [generation, time, abs_time, value] DataFrame from (gen, time, abs_time, value) tuples."""
    return pl.DataFrame(
        [{"generation": g, "time": t, "abs_time": at, "value": v} for g, t, at, v in rows],
        schema={
            "generation": pl.Int64,
            "time": pl.Float64,
            "abs_time": pl.Float64,
            "value": pl.Float64,
        },
    )


class FakeReaderWithSelect:
    """Minimal RunReader stub supporting both series() and select()."""

    def __init__(
        self,
        scalar_data: dict[str, pl.DataFrame] | None = None,
        bulk_data: dict[str, pl.DataFrame] | None = None,
        vector_data: dict[tuple[str, int], pl.DataFrame] | None = None,
    ):
        self._scalar = scalar_data or {}
        self._bulk = bulk_data or {}
        # vector_data: (observable, index) → DataFrame
        self._vector = vector_data or {}

    def series(self, name: str) -> pl.DataFrame:
        if name not in self._scalar:
            raise KeyError(name)
        return self._scalar[name]

    def select(self, index_by: dict) -> pl.DataFrame:
        type_ = index_by["type"]
        if type_ == "bulk_id":
            value = str(index_by["value"])
            if value not in self._bulk:
                raise KeyError(f"bulk_id {value!r} not in catalog")
            return self._bulk[value]
        if type_ == "literal_index":
            obs = index_by.get("observable")
            idx = int(index_by["value"])
            key = (obs, idx)
            if key not in self._vector:
                raise KeyError(f"{obs}[{idx}] not found")
            return self._vector[key]
        raise ValueError(f"Unknown index_by type: {type_!r}")

    def observables(self) -> list[str]:
        return sorted(self._scalar.keys())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bulk_series(val: float, n_gens: int = 2, ticks_per_gen: int = 3) -> pl.DataFrame:
    """Build a uniform bulk-count series."""
    rows = []
    abs_t = 0.0
    for g in range(n_gens):
        for t in range(ticks_per_gen):
            rows.append((g, float(t), abs_t, val))
            abs_t += 1.0
    return _make_series(rows)


def _make_reader_bulk(ids_vals: dict[str, float]) -> FakeReaderWithSelect:
    """Build a reader where each token is a bulk molecule ID (NAME[c] style)."""
    return FakeReaderWithSelect(
        bulk_data={tok: _bulk_series(val) for tok, val in ids_vals.items()}
    )


# ===========================================================================
# TASK 1a: Bulk-ID token fallback
# ===========================================================================


def test_resolve_series_bulk_id_single_token():
    """A bare bulk-id path (e.g. MONOMER0-160[c]) resolves via select(bulk_id)."""
    reader = _make_reader_bulk({"MONOMER0-160[c]": 100.0})
    df = se._resolve_series("MONOMER0-160[c]", reader)
    assert list(df.columns) == ["generation", "time", "abs_time", "value"]
    assert not df.is_empty()
    assert float(df["value"].mean()) == pytest.approx(100.0)


def test_resolve_series_bulk_id_expression():
    """A multi-bulk-id arithmetic expression resolves correctly via select(bulk_id)."""
    # A[c]=100, B[c]=200, C[c]=50 → A / (B + A + C) = 100 / 350 ≈ 0.2857
    reader = _make_reader_bulk({"A[c]": 100.0, "B[c]": 200.0, "C[c]": 50.0})
    df = se._resolve_series("A[c] / (B[c] + A[c] + C[c])", reader)
    assert list(df.columns) == ["generation", "time", "abs_time", "value"]
    assert not df.is_empty()
    expected = 100.0 / (200.0 + 100.0 + 50.0)
    assert float(df["value"].mean()) == pytest.approx(expected, rel=1e-5)


def test_resolve_series_bulk_id_fraction_in_zero_one():
    """Bulk expression fraction is in [0, 1]."""
    reader = _make_reader_bulk({
        "MONOMER0-160[c]": 300.0,
        "PD03831[c]": 200.0,
        "MONOMER0-4565[c]": 100.0,
    })
    df = se._resolve_series(
        "MONOMER0-160[c] / (PD03831[c] + MONOMER0-160[c] + MONOMER0-4565[c])",
        reader,
    )
    assert not df.is_empty()
    for v in df["value"].to_list():
        assert 0.0 <= v <= 1.0, f"Fraction out of [0,1]: {v}"
    expected = 300.0 / (200.0 + 300.0 + 100.0)
    assert float(df["value"].mean()) == pytest.approx(expected, rel=1e-5)


def test_resolve_series_bulk_id_not_in_catalog_raises_observable_not_found():
    """If select(bulk_id) also fails (id not in catalog), ObservableNotFound is raised."""
    reader = FakeReaderWithSelect(bulk_data={})  # empty catalog
    with pytest.raises(se.ObservableNotFound):
        se._resolve_series("MONOMER0-160[c]", reader)


def test_resolve_series_bulk_id_evaluate_test_end_to_end():
    """evaluate_test with a bulk-expression measure evaluates by code (not agent)."""
    reader = _make_reader_bulk({
        "MONOMER0-160[c]": 300.0,
        "PD03831[c]": 200.0,
        "MONOMER0-4565[c]": 100.0,
    })
    test = {
        "name": "atp-fraction",
        "measure": {
            "kind": "generation_average",
            "path": "MONOMER0-160[c] / (PD03831[c] + MONOMER0-160[c] + MONOMER0-4565[c])",
        },
        "pass_if": {"op": "in_range", "low": 0.0, "high": 1.0},
    }
    out = se.evaluate_test(test, reader)
    assert out["evaluated_by"] == "code", f"Expected code, got: {out}"
    assert "measured_value" in out
    assert 0.0 <= float(out["measured_value"]) <= 1.0


# ===========================================================================
# TASK 1b: Literal-index path fallback
# ===========================================================================


def test_resolve_series_literal_index_simple_path():
    """listeners.monomer_counts[3] resolves via select(literal_index)."""
    series_for_idx3 = _bulk_series(42.0, n_gens=2, ticks_per_gen=4)
    reader = FakeReaderWithSelect(
        vector_data={("listeners.monomer_counts", 3): series_for_idx3}
    )
    df = se._resolve_series("listeners.monomer_counts[3]", reader)
    assert list(df.columns) == ["generation", "time", "abs_time", "value"]
    assert not df.is_empty()
    assert float(df["value"].mean()) == pytest.approx(42.0)


def test_resolve_series_literal_index_not_available_raises():
    """A literal-index path that fails select also raises ObservableNotFound."""
    reader = FakeReaderWithSelect(vector_data={})  # no vectors registered
    with pytest.raises(se.ObservableNotFound):
        se._resolve_series("listeners.monomer_counts[3]", reader)


# ===========================================================================
# SP2b-iii: verdict-safe element-kind routing through readout_resolver
# ===========================================================================


def test_literal_index_routes_through_resolver():
    """A literal-index path is routed through the canonical readout_resolver
    (kind == "element") and produces the SAME selector / series as the old
    in-evaluator literal-index fast path — byte-identical select dict."""
    from discovery_engine.readout_resolver import resolve_readout, ResolvedReadout

    # The resolver classifies it as element and its select dict is byte-identical
    # to the dict the old fast path passed to reader.select.
    r = resolve_readout({"identifier": "listeners.monomer_counts[3]"})
    assert isinstance(r, ResolvedReadout) and r.kind == "element"
    assert r.to_select_dict() == {
        "type": "literal_index",
        "value": 3,
        "observable": "listeners.monomer_counts",
    }

    series_for_idx3 = _bulk_series(42.0, n_gens=2, ticks_per_gen=4)
    reader = FakeReaderWithSelect(
        vector_data={("listeners.monomer_counts", 3): series_for_idx3}
    )
    df = se._resolve_series("listeners.monomer_counts[3]", reader)
    assert df is not None and not df.is_empty()
    assert list(df.columns) == ["generation", "time", "abs_time", "value"]
    assert float(df["value"].mean()) == pytest.approx(42.0)


def test_bare_bulk_id_still_resolves_via_fallback():
    """The resolver gives up on a bare bracket bulk id (UnresolvedReadout), so
    _resolve_series must fall through to the unchanged evaluator body, which
    resolves it via the bulk_id select fallback — no regression."""
    from discovery_engine.readout_resolver import resolve_readout, UnresolvedReadout

    # Confirm the resolver does NOT claim this one (so the fast path falls through).
    r = resolve_readout({"identifier": "MONOMER0-160[c]"})
    assert isinstance(r, UnresolvedReadout)

    reader = _make_reader_bulk({"MONOMER0-160[c]": 100.0})
    df = se._resolve_series("MONOMER0-160[c]", reader)
    assert df is not None and not df.is_empty()
    assert float(df["value"].mean()) == pytest.approx(100.0)


# ===========================================================================
# TASK 1c: Never-guess preserved
# ===========================================================================


def test_resolve_series_absent_scalar_raises_observable_not_found():
    """A plain dotted path that's not in series() raises ObservableNotFound."""
    reader = FakeReaderWithSelect()  # empty reader
    with pytest.raises(se.ObservableNotFound):
        se._resolve_series("listeners.mass.cell_mass", reader)


def test_resolve_series_absent_token_in_expression_raises():
    """An expression containing an unresolvable token raises ObservableNotFound."""
    reader = _make_reader_bulk({"A[c]": 1.0})  # only A[c], not B[c]
    with pytest.raises(se.ObservableNotFound):
        se._resolve_series("A[c] + missing_token", reader)


def test_evaluate_test_absent_bulk_still_routes_to_agent():
    """Never-guess: if a bulk token is absent from reader, evaluate_test returns agent."""
    reader = FakeReaderWithSelect(bulk_data={})  # empty bulk catalog
    test = {
        "name": "absent-bulk",
        "measure": {
            "kind": "generation_average",
            "path": "MONOMER0-160[c]",
        },
        "pass_if": {"op": "in_range", "low": 0.0, "high": 1.0},
    }
    out = se.evaluate_test(test, reader)
    assert out["evaluated_by"] == "agent"
    assert "reason" in out


# ===========================================================================
# TASK 2: per_minute_full_lineage rate window
# ===========================================================================


def _rate_series(abs_times: list[float], values: list[float]) -> pl.DataFrame:
    """Build a series with given abs_times and values (single generation)."""
    return pl.DataFrame(
        [
            {"generation": 0, "time": at, "abs_time": at, "value": v}
            for at, v in zip(abs_times, values)
        ],
        schema={
            "generation": pl.Int64,
            "time": pl.Float64,
            "abs_time": pl.Float64,
            "value": pl.Float64,
        },
    )


def test_validate_window_per_minute_full_lineage():
    """per_minute_full_lineage must be accepted by _validate_window."""
    se._validate_window("per_minute_full_lineage")  # must not raise


def test_apply_window_per_minute_exact_values():
    """per_minute rate: Δvalue/Δtime_seconds × 60 = Δvalue/(Δtime_min)."""
    # abs_times in seconds: 0, 60, 120 → Δt = 60s each
    # values: 0, 1, 2 → Δv = 1 each step
    # rate = 1/60 * 60 = 1.0 events/min
    series = _rate_series(abs_times=[0.0, 60.0, 120.0], values=[0.0, 1.0, 2.0])
    kind, data = se._apply_window(series, "per_minute_full_lineage")
    assert kind == "flat"
    assert len(data) == 2, f"Expected 2 rate rows (first diff dropped), got {len(data)}"
    assert data["value"].to_list() == pytest.approx([1.0, 1.0], rel=1e-5)


def test_apply_window_per_minute_varying_rate():
    """Rate is correctly computed for varying Δvalue and Δtime."""
    # abs_times: 0, 30, 60 (in seconds)
    # values: 0, 3, 3  → rates: 3/30*60=6.0, 0/30*60=0.0
    series = _rate_series(abs_times=[0.0, 30.0, 60.0], values=[0.0, 3.0, 3.0])
    _, data = se._apply_window(series, "per_minute_full_lineage")
    rates = data["value"].to_list()
    assert len(rates) == 2
    assert rates[0] == pytest.approx(6.0, rel=1e-5)
    assert rates[1] == pytest.approx(0.0, abs=1e-9)


def test_apply_window_per_minute_guard_div_by_zero():
    """When Δtime=0, that row is dropped (guard against div-by-zero / inf)."""
    # abs_times: 0, 0, 60 → first diff has Δt=0
    series = _rate_series(abs_times=[0.0, 0.0, 60.0], values=[0.0, 5.0, 6.0])
    _, data = se._apply_window(series, "per_minute_full_lineage")
    # The Δt=0 row should be dropped (no inf/nan in result)
    assert not data["value"].is_infinite().any()
    assert not data["value"].is_nan().any()


def test_apply_window_per_minute_standard_shape():
    """per_minute_full_lineage returns [generation, time, abs_time, value]."""
    series = _rate_series(abs_times=[0.0, 60.0], values=[0.0, 1.0])
    _, data = se._apply_window(series, "per_minute_full_lineage")
    assert list(data.columns) == ["generation", "time", "abs_time", "value"]


def test_evaluate_test_per_minute_window_evaluates_by_code():
    """evaluate_test with per_minute_full_lineage window evaluates by code."""
    # 60 seconds per tick, value increments by 1 → rate = 1.0/min
    n_ticks = 10
    rows = [
        {"generation": 0, "time": float(i * 60), "abs_time": float(i * 60), "value": float(i)}
        for i in range(n_ticks)
    ]
    series = pl.DataFrame(rows, schema={
        "generation": pl.Int64, "time": pl.Float64, "abs_time": pl.Float64, "value": pl.Float64
    })
    reader = FakeReaderWithSelect(scalar_data={"myobs": series})
    test = {
        "name": "rate-test",
        "measure": {
            "kind": "rate_match",
            "path": "myobs",
            "window": "per_minute_full_lineage",
        },
        "pass_if": {"op": "median_within_tolerance", "target": 1.0, "tolerance_fraction": 0.1},
    }
    out = se.evaluate_test(test, reader)
    assert out["evaluated_by"] == "code", f"Expected code, got: {out}"
    assert out.get("result") in ("PASS", "FAIL")
    assert "measured_value" in out
    # Rate should be ~1.0/min
    assert abs(float(out["measured_value"]) - 1.0) < 0.01
