"""Question-directed narrative scoping (RFC-0005 §5): narrate(focus=...) projects
one knowledge graph into several question-scoped readings, and narrative_questions
reads the authored lenses that drive it."""
from __future__ import annotations

from discovery_engine.projection.narrative import narrate, narrative_questions


def _claim(cid, verdict="supported"):
    return cid, {"proposition": f"{cid} holds", "verdict": verdict, "evidence_refs": []}


def _graph(*cids, edges=()):
    return {"claims": dict(_claim(c) for c in cids), "edges": list(edges)}


def _e(a, b, t="prerequisite"):
    return {"from": a, "to": b, "type": t}


class TestNarrateFocus:
    def test_no_focus_projects_all(self):
        g = _graph("claim:a", "claim:b", "claim:c")
        ids = {u["claim_id"] for u in narrate(g)["units"]}
        assert ids == {"claim:a", "claim:b", "claim:c"}
        assert narrate(g)["focus"] is None

    def test_focus_scopes_to_subset(self):
        g = _graph("claim:a", "claim:b", "claim:c")
        n = narrate(g, focus=["claim:a", "claim:b"])
        assert {u["claim_id"] for u in n["units"]} == {"claim:a", "claim:b"}
        assert n["focus"] == ["claim:a", "claim:b"]

    def test_focus_accepts_bare_slug(self):
        g = _graph("claim:a", "claim:b")
        ids = {u["claim_id"] for u in narrate(g, focus=["a"])["units"]}
        assert ids == {"claim:a"}

    def test_focus_keeps_induced_order(self):
        g = _graph("claim:a", "claim:b", "claim:c",
                   edges=[_e("claim:a", "claim:b"), _e("claim:b", "claim:c")])
        order = [u["claim_id"] for u in narrate(g, focus=["claim:b", "claim:c"])["units"]]
        assert order == ["claim:b", "claim:c"]  # a dropped; b before c

    def test_two_focus_sets_give_distinct_narratives(self):
        g = _graph("claim:a", "claim:b", "claim:c", "claim:d")
        a = {u["claim_id"] for u in narrate(g, focus=["claim:a", "claim:b"])["units"]}
        b = {u["claim_id"] for u in narrate(g, focus=["claim:c", "claim:d"])["units"]}
        assert a != b and a.isdisjoint(b)


class TestNarrativeQuestions:
    def test_reads_narrative_block(self):
        spec = {"narrative": {"questions": [
            {"id": "form", "question": "How does it form?", "studies": ["s1", "s2"]},
            {"question": "How does it persist?", "focus": ["claim:s3"]},
        ]}}
        qs = narrative_questions(spec)
        assert qs[0] == {"id": "form", "question": "How does it form?",
                         "focus": ["claim:s1", "claim:s2"]}
        assert qs[1]["id"] == "q2" and qs[1]["focus"] == ["claim:s3"]

    def test_reads_top_level_fallback(self):
        spec = {"narrative_questions": [{"question": "Q?", "studies": ["s1"]}]}
        assert narrative_questions(spec)[0]["focus"] == ["claim:s1"]

    def test_empty_when_none(self):
        assert narrative_questions({}) == []
        assert narrative_questions({"narrative": {}}) == []

    def test_skips_malformed(self):
        spec = {"narrative": {"questions": ["bad", {"question": ""}, {"question": "ok"}]}}
        qs = narrative_questions(spec)
        assert len(qs) == 1 and qs[0]["question"] == "ok"
