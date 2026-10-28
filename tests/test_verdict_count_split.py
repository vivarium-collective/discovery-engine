"""Regression guard for study_verdict.verdict_count_split.

d4d131b removed this as "dead code" — it is NOT dead. vivarium-workbench's
gate_rigor.verdict_count_split / behavior_test_card delegate to it for the honest
"pins N/N; acceptance M/K" ledger (#285); removing it silently reverted the
workbench to a "no classified gates" sentinel. Keep it, with this guard.
"""
from __future__ import annotations

from discovery_engine.study_verdict import verdict_count_split


def test_empty_spec_renders_the_zero_ledger_not_a_sentinel():
    out = verdict_count_split({})
    assert out["label"] == "pins: 0/0; acceptance: 0/0"
    assert out["regression_pins"] == {"total": 0, "pass": 0, "fail": 0}
    assert out["acceptance_criteria"] == {"total": 0, "pass": 0, "fail": 0}


def test_returns_the_documented_split_shape():
    out = verdict_count_split({})
    for key in ("regression_pins", "acceptance_criteria", "expected_fail",
                "narrated", "unclassified", "committed_rerunnable", "label"):
        assert key in out
