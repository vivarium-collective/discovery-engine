"""Tests for the G1 gate-class machinery in discovery_engine.study_verdict.

The review's central M1 fix: a ``regression_pin`` (threshold set AFTER
observing the run — a rerun guard) must never be conflated with an
``acceptance_criterion`` (a pre-stated directional prior). Covers:

- classify_gates: mixed gate_class specs bucket correctly; expected-fail
  controls (expected_result/classification/control markers) always win;
  narrated vs unclassified split; kind-carrier fallback.
- preregistration_gate_alignment: a pre-stated acceptance_criterion is
  recognised as pre-registered; a post-hoc regression_pin is exempt; the
  existing preregistration_status keys pass through unchanged.
"""
from __future__ import annotations

from discovery_engine.study_verdict import (
    classify_gates,
    is_expected_fail,
    preregistration_gate_alignment,
    preregistration_status,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _spec(tests: list[dict], outcomes: dict | None = None, **extra) -> dict:
    spec: dict = {"name": "s", "behavior_tests": tests}
    if outcomes is not None:
        spec["runs"] = [{"name": "r1", "status": "completed", "outcomes": outcomes}]
    spec.update(extra)
    return spec


MIXED_TESTS = [
    {"name": "pin-a", "gate_class": "regression_pin", "pass_if": {"low": 1}},
    {"name": "pin-b", "gate_class": "regression_pin", "pass_if": {"low": 2}},
    {"name": "acc-a", "gate_class": "acceptance_criterion", "pass_if": {"high": 5}},
    {"name": "acc-b", "gate_class": "acceptance_criterion", "pass_if": {"high": 9}},
    # expected-fail control — designed to fail; must never count as acceptance
    {"name": "telepathic-ribosome", "gate_class": "acceptance_criterion",
     "expected_result": "fail", "pass_if": {"high": 1}},
    # coded check, author has not declared a gate_class yet
    {"name": "uncommitted-class", "pass_if": {"low": 3}},
    # no coded check, no gate_class — a narrated judgement
    {"name": "story-only", "description": "looks biologically plausible"},
]


# ---------------------------------------------------------------------------
# classify_gates — bucketing
# ---------------------------------------------------------------------------

class TestClassifyGates:
    def test_mixed_spec_buckets_correctly(self):
        c = classify_gates(_spec(MIXED_TESTS))
        assert c["committed_pins"] == ["pin-a", "pin-b"]
        assert c["acceptance_criteria"] == ["acc-a", "acc-b"]
        assert c["expected_fail"] == ["telepathic-ribosome"]
        assert c["unclassified"] == ["uncommitted-class"]
        assert c["narrated"] == ["story-only"]

    def test_buckets_are_exclusive_and_exhaustive(self):
        c = classify_gates(_spec(MIXED_TESTS))
        names = [n for bucket in c.values() for n in bucket]
        assert sorted(names) == sorted(t["name"] for t in MIXED_TESTS)
        assert len(names) == len(set(names))

    def test_expected_fail_overrides_any_gate_class(self):
        # Even a pin marked as a negative control lands in expected_fail.
        tests = [{"name": "neg-pin", "gate_class": "regression_pin",
                  "control": "negative", "pass_if": {"low": 1}}]
        c = classify_gates(_spec(tests))
        assert c["expected_fail"] == ["neg-pin"]
        assert c["committed_pins"] == []

    def test_all_three_expected_fail_markers_recognised(self):
        assert is_expected_fail({"expected_result": "fail"}) is True
        assert is_expected_fail({"expected_result": "FAILED"}) is True
        assert is_expected_fail({"classification": "diagnostic"}) is True
        assert is_expected_fail({"control": "negative"}) is True
        assert is_expected_fail({"classification": "primary"}) is False
        assert is_expected_fail({}) is False
        assert is_expected_fail(None) is False

    def test_kind_carrier_fallback(self):
        # tests-spec carries the value under ``kind``; canonical values are
        # honoured there, other kind values (report_card) are a different axis.
        tests = [{"name": "k-pin", "kind": "regression_pin"},
                 {"name": "k-acc", "kind": "acceptance_criterion"},
                 {"name": "k-card", "kind": "report_card", "pass_if": {"low": 1}}]
        c = classify_gates(_spec(tests))
        assert c["committed_pins"] == ["k-pin"]
        assert c["acceptance_criteria"] == ["k-acc"]
        assert c["unclassified"] == ["k-card"]

    def test_gate_class_field_beats_kind(self):
        tests = [{"name": "t", "gate_class": "acceptance_criterion",
                  "kind": "regression_pin"}]
        assert classify_gates(_spec(tests))["acceptance_criteria"] == ["t"]

    def test_tolerates_empty_and_none_spec(self):
        empty = {"committed_pins": [], "acceptance_criteria": [],
                 "expected_fail": [], "narrated": [], "unclassified": []}
        assert classify_gates({}) == empty
        assert classify_gates(None) == empty

    def test_unknown_gate_class_value_falls_through(self):
        tests = [{"name": "weird", "gate_class": "whatever", "pass_if": {"low": 1}}]
        c = classify_gates(_spec(tests))
        assert c["unclassified"] == ["weird"]


# ---------------------------------------------------------------------------
# preregistration_gate_alignment — pins exempt, acceptance pre-stated
# ---------------------------------------------------------------------------

class TestPreregistrationGateAlignment:
    def test_prestated_acceptance_is_recognised(self):
        spec = _spec(
            [{"name": "acc", "gate_class": "acceptance_criterion",
              "pass_if": {"high": 5}},
             {"name": "pin", "gate_class": "regression_pin",
              "pass_if": {"low": 1}}],
            preregistered={"registered_at": "2026-01-01",
                           "thresholds": {"acc": {"high": 5}}},
        )
        res = preregistration_gate_alignment(spec)
        assert res["preregistered"] is True
        assert res["acceptance_prestated"] is True
        assert res["unregistered_acceptance"] == []
        # the pin is NOT in thresholds — and that's fine: it is exempt
        assert res["pins_exempt"] == ["pin"]

    def test_posthoc_regression_pin_needs_no_prestating(self):
        # A study with ONLY pins and no prereg block: nothing to flag.
        spec = _spec([{"name": "pin", "gate_class": "regression_pin",
                       "pass_if": {"low": 1}}])
        res = preregistration_gate_alignment(spec)
        assert res["preregistered"] is False
        assert res["acceptance_prestated"] is None  # no acceptance gates at all
        assert res["unregistered_acceptance"] == []
        assert res["pins_exempt"] == ["pin"]

    def test_posthoc_acceptance_is_flagged(self):
        # acceptance_criterion with NO prereg block → not pre-stated.
        spec = _spec([{"name": "acc", "gate_class": "acceptance_criterion",
                       "pass_if": {"high": 5}}])
        res = preregistration_gate_alignment(spec)
        assert res["acceptance_prestated"] is False
        assert res["unregistered_acceptance"] == ["acc"]

    def test_moved_threshold_is_not_prestated(self):
        # A prereg entry exists but the live pass_if no longer matches it —
        # the threshold moved after the fact, so it is NOT pre-stated.
        spec = _spec(
            [{"name": "acc", "gate_class": "acceptance_criterion",
              "pass_if": {"high": 9}}],
            preregistered={"thresholds": {"acc": {"high": 5}}},
        )
        res = preregistration_gate_alignment(spec)
        assert res["acceptance_prestated"] is False
        assert res["unregistered_acceptance"] == ["acc"]

    def test_mixed_prestated_and_posthoc_acceptance(self):
        spec = _spec(
            [{"name": "acc-pre", "gate_class": "acceptance_criterion",
              "pass_if": {"high": 5}},
             {"name": "acc-post", "gate_class": "acceptance_criterion",
              "pass_if": {"low": 2}}],
            preregistered={"thresholds": {"acc-pre": {"high": 5}}},
        )
        res = preregistration_gate_alignment(spec)
        assert res["acceptance_prestated"] is False
        assert res["unregistered_acceptance"] == ["acc-post"]

    def test_existing_keys_pass_through_unchanged(self):
        spec = _spec(
            [{"name": "acc", "gate_class": "acceptance_criterion",
              "pass_if": {"high": 5}}],
            preregistered={"registered_at": "2026-01-01",
                           "thresholds": {"acc": {"high": 5}}},
        )
        spec["runs"] = [{"name": "r", "status": "complete",
                         "timestamp": "2026-05-01"}]
        base = preregistration_status(spec)
        enriched = preregistration_gate_alignment(spec)
        for k, v in base.items():
            assert enriched[k] == v
        assert base["registered_before_run"] is True
        assert base["criteria_match"] is True

    def test_preregistration_status_output_is_byte_identical(self):
        # The sibling must not perturb the original function's shape.
        res = preregistration_status({"name": "s"})
        assert res == {"preregistered": False,
                       "registered_before_run": None,
                       "criteria_match": None}

    def test_tolerates_empty_spec(self):
        res = preregistration_gate_alignment({})
        assert res["preregistered"] is False
        assert res["acceptance_prestated"] is None
        assert res["unregistered_acceptance"] == []
        assert res["pins_exempt"] == []
