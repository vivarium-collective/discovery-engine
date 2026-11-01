"""DE 2.0 #3 — the generative loop: a refuted finding auto-spawns a builds-on follow-up
study + a synthesis frontier study, surfaced through suggest_studies -> select_for_launch.

A finding-level refutation is ``status: contradicts`` (the live finding-status enum) or a
``refuted`` verdict; "refuted" alone is the study/claim verdict.
"""
from __future__ import annotations

from discovery_engine.discover import suggest_studies
from discovery_engine.study_seeding import AUTONOMOUS, select_for_launch


def _suggest(studies: dict) -> list[dict]:
    return suggest_studies({}, {"studies": list(studies)}, studies)


def _gen(suggestions):
    return [s for s in suggestions if s["source"] in ("refuted_finding", "synthesis_frontier")]


def test_refuted_finding_spawns_followup_and_synthesis():
    studies = {"s1": {"name": "s1", "findings": [
        {"id": "f1", "status": "contradicts", "statement": "X does not hold under flow",
         "next_action": "Test whether Y holds instead", "explanation": "because of mechanism Z"}]}}
    sugg = _suggest(studies)
    followups = [s for s in sugg if s["source"] == "refuted_finding"]
    synth = [s for s in sugg if s["source"] == "synthesis_frontier"]
    assert len(followups) == 1
    assert followups[0]["parent_study"] == "s1"
    assert followups[0]["status"] == "ready"            # a refutation is a concrete signal
    assert len(synth) == 1 and synth[0]["status"] == "ready"


def test_followup_seed_is_prepopulated_from_the_finding():
    studies = {"s1": {"findings": [
        {"id": "f1", "status": "contradicts", "statement": "X fails",
         "next_action": "Measure Y at low pH", "explanation": "mechanism M"}]}}
    fu = [s for s in _suggest(studies) if s["source"] == "refuted_finding"][0]
    seed = fu["seed"]
    assert seed["seeded_from"]["study"] == "s1"
    assert seed["seeded_from"]["finding"] == "f1"
    assert seed["purpose"]                               # question/mechanism derived from the finding


def test_refuted_verdict_also_triggers():
    studies = {"s1": {"findings": [{"id": "f1", "verdict": "refuted", "statement": "nope"}]}}
    assert any(s["source"] == "refuted_finding" for s in _suggest(studies))


def test_no_refutations_means_no_generative_suggestions():
    studies = {"s1": {"findings": [{"id": "f1", "status": "confirms", "statement": "holds"}]}}
    assert _gen(_suggest(studies)) == []


def test_one_synthesis_study_across_many_refutations():
    studies = {
        "s1": {"findings": [{"id": "a", "status": "contradicts", "statement": "p"}]},
        "s2": {"findings": [{"id": "b", "status": "contradicts", "statement": "q"}]},
    }
    sugg = _suggest(studies)
    assert len([s for s in sugg if s["source"] == "refuted_finding"]) == 2
    assert len([s for s in sugg if s["source"] == "synthesis_frontier"]) == 1   # exactly one


def test_generative_suggestions_launch_under_autonomous():
    studies = {"s1": {"findings": [
        {"id": "f1", "status": "contradicts", "statement": "X fails", "next_action": "test Y"}]}}
    gen = _gen(_suggest(studies))
    sel = select_for_launch(gen, autonomy=AUTONOMOUS)
    assert len(sel["launch"]) == len(gen) == 3 - 1    # 1 followup + 1 synthesis = 2
    assert not sel["held"]


def test_findings_without_ids_are_skipped_cleanly():
    studies = {"s1": {"findings": [{"status": "contradicts", "statement": "no id here"}]}}
    assert _gen(_suggest(studies)) == []               # no id -> cannot seed -> skipped
