"""Tests for discovery_engine.narrative — Narrate + Audit (RFC-0005 §5, §7.3).

narrate(GE, question, audience) -> N: an ordered projection of the argument graph
(topological over prerequisite+support, ties by id), units = study-level claims,
arm claims excluded. A narrative is a *projection* of GE, never a requirement that
GE be linear.

audit(N, GE) -> gaps: an adjacent transition with no support/prerequisite path is
an unsupported transition; each gap names the missing evidence and a candidate
follow-up study (the investigate<->narrate loop-closer).
"""
from __future__ import annotations

from discovery_engine.narrative import narrate, audit


def _claim(cid, verdict="supported", **extra):
    c = {"proposition": f"{cid} holds", "verdict": verdict, "evidence_refs": [], "derived": True}
    c.update(extra)
    return cid, c


def _graph(claims, edges):
    return {"claims": dict(claims), "edges": list(edges)}


def _e(a, b, t):
    return {"from": a, "to": b, "type": t}


# ---------------------------------------------------------------------------
# narrate — ordered projection
# ---------------------------------------------------------------------------

class TestNarrate:
    def test_prerequisite_orders_before(self):
        g = _graph([_claim("claim:a"), _claim("claim:b")],
                   [_e("claim:a", "claim:b", "prerequisite")])
        order = [u["claim_id"] for u in narrate(g)["units"]]
        assert order == ["claim:a", "claim:b"]

    def test_support_orders_before(self):
        g = _graph([_claim("claim:x"), _claim("claim:y")],
                   [_e("claim:y", "claim:x", "support")])  # y supports x -> y first
        order = [u["claim_id"] for u in narrate(g)["units"]]
        assert order == ["claim:y", "claim:x"]

    def test_deterministic_tie_break_by_id(self):
        g = _graph([_claim("claim:b"), _claim("claim:a"), _claim("claim:c")], [])
        order = [u["claim_id"] for u in narrate(g)["units"]]
        assert order == ["claim:a", "claim:b", "claim:c"]

    def test_arm_claims_are_not_narrative_units(self):
        g = _graph([_claim("claim:a"), _claim("claim:a:arm1", arm_of="claim:a")], [])
        ids = [u["claim_id"] for u in narrate(g)["units"]]
        assert ids == ["claim:a"]

    def test_units_preserve_evidence_refs(self):
        g = _graph([_claim("claim:a", evidence_refs=["run1", "f1"])], [])
        assert narrate(g)["units"][0]["evidence_refs"] == ["run1", "f1"]

    def test_open_questions_from_unassessed_and_inconclusive(self):
        g = _graph([_claim("claim:a", verdict="unassessed"),
                    _claim("claim:b", verdict="inconclusive"),
                    _claim("claim:c", verdict="supported")], [])
        oq = narrate(g)["open_questions"]
        assert any("claim:a" in q["claim_id"] for q in oq)
        assert any("claim:b" in q["claim_id"] for q in oq)
        assert all(q["claim_id"] != "claim:c" for q in oq)

    def test_limitations_from_rebuttals(self):
        g = _graph([_claim("claim:a", rebuttals=["only in a narrow corner"])], [])
        assert "only in a narrow corner" in narrate(g)["limitations"]

    def test_question_and_audience_carried(self):
        g = _graph([_claim("claim:a")], [])
        n = narrate(g, question="Why?", audience="reviewers")
        assert n["question"] == "Why?" and n["audience"] == "reviewers"


# ---------------------------------------------------------------------------
# audit — unsupported transitions become follow-up requests
# ---------------------------------------------------------------------------

class TestAudit:
    def test_supported_chain_has_no_gaps(self):
        g = _graph([_claim("claim:a"), _claim("claim:b")],
                   [_e("claim:a", "claim:b", "prerequisite")])
        assert audit(narrate(g), g) == []

    def test_transitive_support_path_has_no_gap(self):
        g = _graph([_claim("claim:a"), _claim("claim:b"), _claim("claim:c")],
                   [_e("claim:a", "claim:b", "support"), _e("claim:b", "claim:c", "support")])
        # ordered a,b,c; a->c reachable transitively; adjacent pairs both direct
        assert audit(narrate(g), g) == []

    def test_independent_adjacent_claims_are_not_a_gap(self):
        # Two independent claims with NO edge land adjacent only because of the
        # topological tie-break — the reading never asserts a transition between them,
        # so it is a linearization artifact, not a gap. (Previously over-reported.)
        g = _graph([_claim("claim:a"), _claim("claim:b")], [])
        assert audit(narrate(g), g) == []

    def test_declared_unsupported_transition_is_a_gap_with_followup(self):
        # A declared dependency that is neither a supporting path nor a challenge is a
        # genuine unsupported transition the reading leans on without justification.
        g = _graph([_claim("claim:a"), _claim("claim:b")],
                   [_e("claim:a", "claim:b", "informs")])  # declared, non-supporting, non-challenge
        gaps = audit(narrate(g), g)
        assert len(gaps) == 1
        gap = gaps[0]
        assert gap["from"] == "claim:a" and gap["to"] == "claim:b"
        assert "missing_evidence" in gap
        fu = gap["followup"]
        assert fu["id"] and fu["title"] and fu["motivation"]
        assert fu["parent_study"] == "b"  # derived from the target claim's slug

    def test_verdict_escalation_flagged(self):
        # Escalation is annotated on a gap that is a real asserted (declared) transition.
        g = _graph([_claim("claim:a", verdict="inconclusive"),
                    _claim("claim:b", verdict="supported")],
                   [_e("claim:a", "claim:b", "informs")])
        gap = audit(narrate(g), g)[0]
        assert gap["verdict_escalation"] is True

    def test_challenge_edge_is_not_a_narrative_gap(self):
        # A challenge edge is NOT a supporting link, but it is tracked on the
        # open-challenge axis (an unadjudicated alternative/refutation) — so it is not
        # re-raised as a narrative gap (that was a double-count).
        g = _graph([_claim("claim:a"), _claim("claim:b")],
                   [_e("claim:a", "claim:b", "challenge")])
        assert audit(narrate(g), g) == []
