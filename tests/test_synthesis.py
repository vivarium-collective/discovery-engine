"""Tests for discovery_engine.synthesis — the gate synthesis spine + open calls.

Formalizes the Plan-progress and Decisions tabs onto the canonical structure
(RFC-0005): a plan gate's verdict is rolled up from its member studies' canonical
verdicts (one vocabulary), and the open calls unify authored decisions with the
derived open items (audit gaps, open-question claims, verdict divergences).
"""
from __future__ import annotations

from discovery_engine.synthesis import gate_verdict, synthesize_gates, open_calls


# --- gate_verdict ----------------------------------------------------------

class TestGateVerdict:
    def test_all_supported(self):
        assert gate_verdict(["supported", "supported"]) == "supported"

    def test_all_refuted(self):
        assert gate_verdict(["refuted", "refuted"]) == "refuted"

    def test_homogeneous_inconclusive_or_qualified_map_to_self(self):
        assert gate_verdict(["inconclusive"]) == "inconclusive"
        assert gate_verdict(["qualified", "qualified"]) == "qualified"

    def test_mixed_is_qualified(self):
        assert gate_verdict(["supported", "refuted"]) == "qualified"
        assert gate_verdict(["supported", "qualified"]) == "qualified"

    def test_unassessed_dropped_then_empty_is_unassessed(self):
        assert gate_verdict(["unassessed", None]) == "unassessed"
        assert gate_verdict([]) == "unassessed"
        # unassessed members don't drag a supported gate down
        assert gate_verdict(["supported", "unassessed"]) == "supported"


# --- synthesize_gates ------------------------------------------------------

def _claims(**slug_verdict):
    return {f"claim:{s}": {"verdict": v} for s, v in slug_verdict.items()}


class TestSynthesizeGates:
    def test_rolls_up_member_verdicts_and_carries_spine(self):
        inv = {"plan_gates": [
            {"id": "A1", "name": "Network persistence", "criterion": "network persists",
             "member_studies": ["s1", "s2"]},
            {"id": "A2", "name": "Autocatalysis", "extends": "A1", "off_spine": True,
             "member_studies": ["s3"]},
        ]}
        claims = _claims(s1="supported", s2="supported", s3="refuted")
        gates = synthesize_gates(inv, claims)
        a1 = next(g for g in gates if g["id"] == "A1")
        a2 = next(g for g in gates if g["id"] == "A2")
        assert a1["verdict"] == "supported"
        assert a1["criterion"] == "network persists"
        assert [m["slug"] for m in a1["members"]] == ["s1", "s2"]
        assert a2["verdict"] == "refuted" and a2["extends"] == "A1" and a2["off_spine"] is True

    def test_no_gates_yields_empty(self):
        assert synthesize_gates({}, {}) == []


# --- open_calls ------------------------------------------------------------

class TestOpenCalls:
    def test_authored_decisions_become_calls(self):
        inv = {"executive": {"decisions_needed": [
            {"question": "Supply an H2 prior", "context": "the floor turns on it"}]}}
        calls = open_calls(inv, {}, [])
        d = next(c for c in calls if c["kind"] == "decision")
        assert d["title"] == "Supply an H2 prior" and d["action"] == "resolve"

    def test_audit_gaps_become_followup_calls(self):
        gaps = [{"from": "claim:a", "to": "claim:b", "missing_evidence": "link a to b",
                 "followup": {"id": "discriminate-b"}}]
        calls = open_calls({}, {}, gaps)
        g = next(c for c in calls if c["kind"] == "narrative_gap")
        assert "a → b" in g["title"] and "discriminate-b" in g["action"]
        assert g["refs"] == ["claim:a", "claim:b"]

    def test_open_question_and_divergence_claims(self):
        claims = {
            "claim:a": {"proposition": "A?", "verdict": "inconclusive"},
            "claim:b": {"proposition": "B", "verdict": "supported", "diverges": True},
            "claim:c": {"proposition": "C", "verdict": "supported"},
            "claim:m:arm1": {"proposition": "arm", "verdict": "unassessed", "arm_of": "claim:m"},
        }
        calls = open_calls({}, claims, [])
        kinds = {c["refs"][0]: c["kind"] for c in calls if c["refs"]}
        assert kinds.get("claim:a") == "open_question"
        assert kinds.get("claim:b") == "divergence"
        assert "claim:c" not in kinds           # a clean supported claim is no call
        assert "claim:m:arm1" not in kinds      # arm claims are not surfaced
