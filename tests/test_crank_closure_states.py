"""R4 surfacing: the crank report carries the five closure states and summarize renders them."""
from __future__ import annotations

from discovery_engine import discover
from discovery_engine.projection import engine as de
from discovery_engine.projection.closure_states import CLOSURE_STATES


def _cycle():
    inv = {"name": "i", "studies": ["s"]}
    studies = {"s": {"name": "s", "investigation": "i"}}
    return de.run_cycle(inv, studies) if hasattr(de, "run_cycle") else discover.run_cycle(inv, studies), inv


def test_crank_includes_the_five_closure_states():
    result, inv = _cycle()
    report = de.crank(result, inv)
    assert "closure_states" in report
    assert set(report["closure_states"]) == set(CLOSURE_STATES)
    # closure-derived axes are exact: an empty investigation is workflow-complete + resolved
    assert report["closure_states"]["workflow_complete"] is True
    assert report["closure_states"]["scientifically_resolved"] is True


def test_summarize_renders_a_states_line():
    result, inv = _cycle()
    text = de.summarize(de.crank(result, inv))
    assert "states:" in text
    assert "workflow complete" in text
    assert "scientifically resolved" in text
