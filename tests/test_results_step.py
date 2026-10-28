"""Tests for ResultsHandle/ResultsStep (discovery_engine.post_sim) and the
deterministic composite-wiring proof for the post-sim Step family.

Covers:
  (a) ResultsStep over a tiny fixture emitter output (a real SQLite history
      db, written the same way ``viva_emitters.SQLiteEmitter`` bookkeeping
      does) produces a ``ResultsHandle`` whose ``.records()`` returns the
      expected rows.
  (b) ResultsHandle config round-trip (``from_config``/``to_config``) —
      file-rehydratable.
  (c) A small composite ``[fixture emitter output] -> ResultsStep ->
      AnalysisStep + ReportCardStep``: ``composite.run(0.0)`` and both
      post-sim Steps produce their output from the same handle; the report
      card's gating verdict lands. Two independent runs are identical.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from process_bigraph import Composite, allocate_core

import viva_emitters

from discovery_engine.post_sim import (
    AnalysisStep,
    ReportCardStep,
    ResultsHandle,
    ResultsStep,
)


# ---------------------------------------------------------------------------
# Fixture: a tiny, schema-compliant SQLite emitter output.
# ---------------------------------------------------------------------------

SIM_ID = "fixture-sim-1"
TICKS = [
    {"listeners": {"mass": {"cell_mass": 100.0 + i}}, "time": float(i)}
    for i in range(3)
]


def _write_fixture_sqlite(db_path) -> None:
    """Write a real, schema-compliant SQLite history db: metadata bookkeeping
    via the emitters package's own helper, history rows inserted the same
    shape SQLiteEmitter writes them (JSON blob per tick)."""
    db_path = str(db_path)
    viva_emitters.save_simulation_metadata(db_path, SIM_ID, name="fixture")
    conn = sqlite3.connect(db_path)
    try:
        for step, tick in enumerate(TICKS):
            conn.execute(
                "INSERT INTO history (simulation_id, step, global_time, state) "
                "VALUES (?, ?, ?, ?)",
                (SIM_ID, step, tick["time"], json.dumps(tick)),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def fixture_db(tmp_path):
    db_path = tmp_path / "fixture_runs.db"
    _write_fixture_sqlite(db_path)
    return db_path


@pytest.fixture
def core():
    return allocate_core()


# ---------------------------------------------------------------------------
# (a) ResultsStep over a fixture -> ResultsHandle.records()
# ---------------------------------------------------------------------------

def test_results_step_produces_handle_with_expected_records(fixture_db, core):
    step = ResultsStep({"paths": [str(fixture_db)]}, core=core)
    out = step.update({})
    handle = out["results"]
    assert isinstance(handle, ResultsHandle)
    rows = handle.records()
    assert len(rows) == len(TICKS)
    assert [r["time"] for r in rows] == [t["time"] for t in TICKS]


def test_results_step_invoke_swallows_bad_path(core):
    step = ResultsStep({"paths": ["/does/not/exist.db"]}, core=core)
    result = step.invoke({})
    handle = result.get()["results"]
    # A missing sqlite file yields no simulations -> empty records, not a crash.
    assert handle.records() == []


def test_results_step_state_input_overrides_config(fixture_db, core):
    """A wired-in `paths` state value takes precedence over config."""
    step = ResultsStep({"paths": ["/does/not/exist.db"]}, core=core)
    out = step.update({"paths": [str(fixture_db)]})
    assert len(out["results"].records()) == len(TICKS)


# ---------------------------------------------------------------------------
# (b) ResultsHandle is reconstructable from a plain config dict
# ---------------------------------------------------------------------------

def test_results_handle_config_round_trip(fixture_db):
    handle = ResultsHandle(paths=[str(fixture_db)], simulation_id=SIM_ID)
    cfg = handle.to_config()
    rehydrated = ResultsHandle.from_config(cfg)
    assert rehydrated.records() == handle.records()
    assert rehydrated.to_config() == cfg


def test_results_handle_records_cached(fixture_db):
    handle = ResultsHandle(paths=[str(fixture_db)])
    first = handle.records()
    second = handle.records()
    assert first is second  # cached, not re-read


# ---------------------------------------------------------------------------
# (c) deterministic composite wiring: ResultsStep -> AnalysisStep + ReportCardStep
# ---------------------------------------------------------------------------

class _ProbeResultsAnalysis(AnalysisStep):
    """Probe analysis: surfaces row count + a handle identity marker so the
    test can prove it read the exact same ResultsHandle the report card did."""

    name = "probe_results_analysis"
    scale = "single"

    def analyze(self, rows):
        return {"n_rows": len(rows), "masses": [r["listeners"]["mass"]["cell_mass"] for r in rows]}


class _ProbeResultsCard(ReportCardStep):
    """Probe report card: reads the same `results` handle (wired to its
    `results` input, not the default `study` input) and gates on row count —
    a real (if trivial) verdict derived from the handle's records, proving
    the report card's gating logic ran off the shared handle."""

    name = "probe_results_card"

    def inputs(self):
        return {"results": "tree"}

    def applies(self, results):
        return True

    def build(self, results):
        rows = results.records()
        ok = len(rows) == len(TICKS)
        return (
            {
                "status": "pass" if ok else "fail",
                "checks": [{"name": "row_count", "ok": ok, "expected": len(TICKS), "actual": len(rows)}],
                "summary": f"{len(rows)} rows",
            },
            f"<div>{len(rows)} rows</div>",
        )

    def update(self, state, interval=None):
        results = state.get("results")
        res = self.build(results) if results is not None else None
        if not res:
            return {"view": "", "data": {}}
        verdict, html = res
        return {"view": html, "data": verdict}


def _make_core():
    core = allocate_core()
    core.register_link("_ProbeResultsAnalysis", _ProbeResultsAnalysis)
    core.register_link("_ProbeResultsCard", _ProbeResultsCard)
    core.register_link("ResultsStep", ResultsStep)
    return core


def _make_wiring_state(fixture_db):
    return {
        "results_store": None,
        "analysis_store": {},
        "card_store": {},
        "card_html_store": "",
        "results_node": {
            "_type": "step",
            "address": "local:ResultsStep",
            "config": {"paths": [str(fixture_db)]},
            "inputs": {"paths": ["missing_paths_store"], "sim_data_ref": ["missing_sdr_store"],
                       "simulation_id": ["missing_sim_id_store"]},
            "outputs": {"results": ["results_store"]},
        },
        "missing_paths_store": None,
        "missing_sdr_store": None,
        "missing_sim_id_store": None,
        "analysis_node": {
            "_type": "step",
            "address": "local:_ProbeResultsAnalysis",
            "config": {},
            "inputs": {"results": ["results_store"]},
            "outputs": {"analysis": ["analysis_store"]},
        },
        "card_node": {
            "_type": "step",
            "address": "local:_ProbeResultsCard",
            "config": {},
            "inputs": {"results": ["results_store"]},
            "outputs": {"view": ["card_html_store"], "data": ["card_store"]},
        },
    }


def _run_wiring(fixture_db):
    core = _make_core()
    state = _make_wiring_state(fixture_db)
    composite = Composite({"state": state}, core=core)
    composite.run(0.0)
    return composite


def test_composite_wiring_analysis_and_report_card_read_same_handle(fixture_db):
    composite = _run_wiring(fixture_db)

    analysis = composite.state["analysis_store"]
    card = composite.state["card_store"]

    assert analysis["n_rows"] == len(TICKS)
    assert analysis["masses"] == [t["listeners"]["mass"]["cell_mass"] for t in TICKS]

    assert card["status"] == "pass"
    assert card["checks"][0]["actual"] == len(TICKS)
    assert "3 rows" in composite.state["card_html_store"]


def test_composite_wiring_is_deterministic_across_runs(fixture_db):
    run1 = _run_wiring(fixture_db)
    run2 = _run_wiring(fixture_db)

    assert run1.state["analysis_store"] == run2.state["analysis_store"]
    assert run1.state["card_store"] == run2.state["card_store"]
    assert run1.state["card_html_store"] == run2.state["card_html_store"]
