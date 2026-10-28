"""Tests for discovery_engine.knowledge_store — the derived Concept + canonical
Verdict knowledge projection.

Covers:
- canonical_verdict: maps the coded roll_up_verdict vocabulary into the canonical
  five, keeps the Progress and Verdict axes INDEPENDENT (execution success never
  sets a verdict), honours an authored study verdict (authored wins), and flags
  divergence.
- derive_knowledge: per-member canonical verdicts + one derived singleton Concept
  per Study (grouping is the slice-2 consolidate act, not an auto-inference);
  a member with no study spec is skipped, not fatal.
- merge_knowledge: authored wins, derived fills, empty never clobbers.
"""
from __future__ import annotations

from discovery_engine.knowledge_store import (
    _study_behaviors,
    canonical_verdict,
    derive_knowledge,
    merge_knowledge,
)


class TestBehaviorsUnionAcrossRuns:
    """A gate recorded in a sibling run must be seen, not reported PENDING."""

    def test_gate_recorded_in_a_non_selected_run_is_seen(self):
        # two runs both flagged canonical; the motifs gate PASSES in the first, but the
        # single-canonical-run lookup would select the second (last wins) and miss it.
        spec = {
            "name": "s", "behavior_tests": [{"name": "motifs"}, {"name": "flux"}],
            "runs": [
                {"name": "net", "status": "completed", "canonical": True,
                 "outcomes": {"motifs": {"result": "PASS"}}},
                {"name": "proto", "status": "completed", "canonical": True,
                 "outcomes": {"flux": {"result": "PASS"}}},
            ],
        }
        beh = _study_behaviors(spec)
        assert beh["motifs"] == "PASS" and beh["flux"] == "PASS"

    def test_canonical_run_wins_on_a_genuine_conflict(self):
        spec = {
            "name": "s", "behavior_tests": [{"name": "g"}],
            "runs": [
                {"name": "old", "status": "completed", "outcomes": {"g": {"result": "FAIL"}}},
                {"name": "canon", "status": "completed", "canonical": True,
                 "outcomes": {"g": {"result": "PASS"}}},
            ],
        }
        assert _study_behaviors(spec)["g"] == "PASS"


# ---------------------------------------------------------------------------
# Helpers — mirror tests/test_study_verdict.py so specs drive roll_up_verdict
# ---------------------------------------------------------------------------

def _spec(tests, outcomes=None, *, has_runs=True, **extra):
    runs = []
    if has_runs:
        run = {"name": "r1", "status": "completed"}
        if outcomes is not None:
            run["outcomes"] = outcomes
        runs = [run]
    spec = {"name": "s", "behavior_tests": tests, "runs": runs}
    spec.update(extra)
    return spec


def _named(*names):
    return [{"name": n} for n in names]


def _pass(*names):
    return _spec(_named(*names), {n: {"result": "PASS"} for n in names})


def _fail(*names):
    return _spec(_named(*names), {n: {"result": "FAIL"} for n in names})


# ---------------------------------------------------------------------------
# canonical_verdict — the mapping + the two-axis decoupling
# ---------------------------------------------------------------------------

class TestCanonicalVerdictDerived:
    def test_passing_tests_map_to_supported(self):
        cv = canonical_verdict(_pass("t1", "t2"))
        assert cv["verdict"] == "supported"
        assert cv["verdict_source"] == "derived"
        assert cv["diverges"] is False

    def test_failing_tests_map_to_refuted(self):
        assert canonical_verdict(_fail("t1"))["verdict"] == "refuted"

    def test_partial_maps_to_qualified(self):
        spec = _spec(_named("t1", "t2"),
                     {"t1": {"result": "PASS"}, "t2": {"result": "partial"}})
        assert canonical_verdict(spec)["verdict"] == "qualified"

    def test_no_runs_maps_to_unassessed(self):
        cv = canonical_verdict(_spec(_named("t1"), has_runs=False))
        assert cv["verdict"] == "unassessed"


class TestCanonicalVerdictAuthoredWins:
    def test_authored_verdict_overrides_derived(self):
        # tests pass (derived=supported) but the author recorded inconclusive
        cv = canonical_verdict(_pass("t1") | {"verdict": "inconclusive"})
        assert cv["verdict"] == "inconclusive"
        assert cv["verdict_source"] == "authored"
        assert cv["diverges"] is True

    def test_authored_agreeing_does_not_diverge(self):
        cv = canonical_verdict(_pass("t1") | {"verdict": "supported"})
        assert cv["verdict"] == "supported"
        assert cv["verdict_source"] == "authored"
        assert cv["diverges"] is False

    def test_qualified_refines_supported_is_not_a_divergence(self):
        # tests pass (derived=supported); the author qualified it — same answer, a caveat,
        # not a conflict. Must NOT flag a divergence.
        cv = canonical_verdict(_pass("t1") | {"verdict": "qualified"})
        assert cv["verdict"] == "qualified" and cv["diverges"] is False

    def test_qualified_vs_refuted_is_a_divergence(self):
        # tests fail (derived=refuted); the author says it holds-with-caveats — a genuine
        # conflict on the answer.
        cv = canonical_verdict(_fail("t1") | {"verdict": "qualified"})
        assert cv["verdict"] == "qualified" and cv["diverges"] is True


class TestProgressVerdictDecoupling:
    def test_failed_execution_does_not_downgrade_authored_verdict(self):
        """Review focus: execution failed, author says supported — the verdict is
        NOT downgraded by progress; the two axes stay independent."""
        spec = _fail("t1") | {"execution_status": "failed", "verdict": "supported"}
        cv = canonical_verdict(spec)
        assert cv["verdict"] == "supported"        # verdict axis, authored
        assert cv["progress"] == "failed"          # progress axis, independent
        assert cv["verdict_source"] == "authored"
        assert cv["diverges"] is True              # supported vs derived refuted

    def test_completed_progress_projected(self):
        cv = canonical_verdict(_pass("t1") | {"execution_status": "completed"})
        assert cv["progress"] == "completed"

    def test_absent_execution_status_omits_progress(self):
        assert "progress" not in canonical_verdict(_pass("t1"))

    def test_invalid_execution_status_omits_progress(self):
        assert "progress" not in canonical_verdict(
            _pass("t1") | {"execution_status": "bogus"})


# ---------------------------------------------------------------------------
# derive_knowledge — verdicts for all members + singleton Concepts
# ---------------------------------------------------------------------------

class TestDeriveKnowledge:
    def test_verdicts_for_each_member(self):
        inv = {"studies": ["a", "b"]}
        studies = {"a": _pass("t1"), "b": _fail("t1")}
        k = derive_knowledge(inv, studies)
        assert set(k["verdicts"]) == {"a", "b"}
        assert k["verdicts"]["a"]["verdict"] == "supported"
        assert k["verdicts"]["b"]["verdict"] == "refuted"

    def test_singleton_concept_per_study(self):
        inv = {"studies": ["a"]}
        studies = {"a": _pass("t1") | {"question": "Does a hold?"}}
        k = derive_knowledge(inv, studies)
        assert set(k["concepts"]) == {"a"}
        c = k["concepts"]["a"]
        assert c["study_refs"] == ["a"]
        assert c["derived"] is True
        assert c["org_state"] == "exploring"
        assert c["synthesis"] is None
        assert c["question"] == "Does a hold?"

    def test_missing_study_is_skipped_not_fatal(self):
        inv = {"studies": ["a", "ghost"]}
        studies = {"a": _pass("t1")}  # 'ghost' has no spec
        k = derive_knowledge(inv, studies)
        assert set(k["verdicts"]) == {"a"}
        assert "ghost" not in k["concepts"]

    def test_falls_back_to_studies_by_slug_when_no_membership(self):
        studies = {"a": _pass("t1"), "b": _pass("t2")}
        k = derive_knowledge({}, studies)
        assert set(k["verdicts"]) == {"a", "b"}


# ---------------------------------------------------------------------------
# merge_knowledge — authored wins, derived fills, empty never clobbers
# ---------------------------------------------------------------------------

class TestMergeKnowledge:
    def test_authored_synthesis_wins_over_derived_none(self):
        derived = {"concepts": {"c1": {"question": "Q?", "synthesis": None}}, "verdicts": {}}
        authored = {"concepts": {"c1": {"synthesis": "Authored take."}}, "verdicts": {}}
        m = merge_knowledge(authored, derived)
        assert m["concepts"]["c1"]["synthesis"] == "Authored take."
        assert m["concepts"]["c1"]["question"] == "Q?"  # derived fills

    def test_empty_authored_value_never_clobbers(self):
        derived = {"concepts": {"c1": {"synthesis": "Derived take."}}, "verdicts": {}}
        authored = {"concepts": {"c1": {"synthesis": ""}}, "verdicts": {}}
        m = merge_knowledge(authored, derived)
        assert m["concepts"]["c1"]["synthesis"] == "Derived take."

    def test_authored_only_concept_is_added(self):
        derived = {"concepts": {}, "verdicts": {}}
        authored = {"concepts": {"new": {"question": "New?"}}, "verdicts": {}}
        m = merge_knowledge(authored, derived)
        assert m["concepts"]["new"]["question"] == "New?"


def test_canonical_verdict_reads_conclusion_verdict_when_verdict_absent():
    from discovery_engine.knowledge_store import canonical_verdict
    scalar = canonical_verdict({"conclusion_verdict": "supported"})
    assert scalar["verdict"] == "supported" and scalar["verdict_source"] == "authored"
    obj = canonical_verdict({"conclusion_verdict": {"verdict": "refuted"}})
    assert obj["verdict"] == "refuted"
