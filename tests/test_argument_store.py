"""Tests for discovery_engine.argument_store — the derived Claim + argument-graph
projection (RFC-0005 §7, §10.3).

A Claim is the first-class epistemic assertion (proposition, scope, qualifiers,
warrants, evidence, rebuttals) carrying the canonical Verdict. The argument graph
(`edges`) links Claim→Claim with epistemic relations derived from
`parent_relations` — NOT from `parent_studies` (the organizational gate). Covers:

- canonical_claim: field derivation + fallback order, never-fabricate stub,
  verdict reuse of knowledge_store.canonical_verdict, warrants/evidence/rebuttals.
- derive_argument: one Claim per member (or per arm), edge classification from
  parent_relations, parent_studies stays OUT of the graph, motivates skipped.
- merge_argument: authored wins, derived fills, empty never clobbers, edges union.
"""
from __future__ import annotations

from discovery_engine.argument_store import (
    canonical_claim,
    derive_argument,
    merge_argument,
)


# ---------------------------------------------------------------------------
# Helpers — mirror test_knowledge_store so specs drive roll_up_verdict
# ---------------------------------------------------------------------------

def _spec(tests=None, outcomes=None, *, has_runs=True, **extra):
    tests = tests or []
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
# canonical_claim — field derivation
# ---------------------------------------------------------------------------

class TestClaimProposition:
    def test_claim_field_is_the_proposition(self):
        c = canonical_claim(_pass("t1") | {"claim": "X rescues Y."}, "s1")
        assert c["proposition"] == "X rescues Y."
        assert c["derived"] is True

    def test_falls_back_title_then_question(self):
        assert canonical_claim(_pass("t1") | {"title": "T"}, "s1")["proposition"] == "T"
        assert canonical_claim(_pass("t1") | {"question": "Q?"}, "s1")["proposition"] == "Q?"

    def test_missing_proposition_is_a_stub_never_fabricated(self):
        c = canonical_claim(_spec(has_runs=False), "ghost")
        assert "ghost" in c["proposition"]
        assert c["proposition_stub"] is True


class TestClaimVerdictReusesKnowledgeStore:
    def test_passing_is_supported(self):
        assert canonical_claim(_pass("t1"), "s1")["verdict"] == "supported"

    def test_authored_verdict_wins(self):
        c = canonical_claim(_pass("t1") | {"verdict": "qualified"}, "s1")
        assert c["verdict"] == "qualified"


class TestClaimScopeQualifiersEvidence:
    def test_scope_from_primary_limitation(self):
        c = canonical_claim(_pass("t1") | {"primary_limitation": "narrow corner"}, "s1")
        assert c["scope"] == "narrow corner"

    def test_qualifiers_confidence_and_evidence_tier(self):
        c = canonical_claim(
            _pass("t1") | {"confidence": "Investigating", "evidence_tier": "mechanistic_prediction"},
            "s1",
        )
        assert c["qualifiers"] == {"confidence": "Investigating",
                                   "evidence_tier": "mechanistic_prediction"}

    def test_evidence_refs_union(self):
        spec = _spec(
            _named("bt1"),
            {"bt1": {"result": "PASS"}},
            findings=[{"id": "f1", "status": "confirms", "statement": "ok"}],
            visualizations=[{"name": "fig1"}],
            primary_output="overlap volume",
        )
        refs = canonical_claim(spec, "s1")["evidence_refs"]
        assert {"f1", "r1", "bt1", "fig1", "overlap volume"} <= set(refs)

    def test_warrants_from_prereg_and_primary_tests(self):
        spec = _pass("t1") | {
            "preregistered": {"thresholds": {"t1": {"op": ">", "value": 1.0}}},
            "behavior_tests": [{"name": "t1", "classification": "primary",
                                "pass_if": "x>1", "cites": ["doi:1"]}],
        }
        w = canonical_claim(spec, "s1")["warrants"]
        assert "t1" in w["thresholds"]
        assert any(t["name"] == "t1" for t in w["primary_tests"])

    def test_rebuttals_from_findings_and_controls(self):
        spec = _pass("t1") | {
            "findings": [{"id": "r1f", "status": "refutes", "statement": "counter"}],
            "proposed_controls": ["no-gradient null"],
        }
        reb = canonical_claim(spec, "s1")["rebuttals"]
        assert "counter" in reb
        assert "no-gradient null" in reb


# ---------------------------------------------------------------------------
# derive_argument — claims + the argument graph
# ---------------------------------------------------------------------------

class TestDeriveClaims:
    def test_one_claim_per_member(self):
        inv = {"studies": ["a", "b"]}
        studies = {"a": _pass("t1"), "b": _fail("t1")}
        a = derive_argument(inv, studies)
        assert set(a["claims"]) == {"claim:a", "claim:b"}
        assert a["claims"]["claim:a"]["verdict"] == "supported"

    def test_missing_study_skipped_not_fatal(self):
        a = derive_argument({"studies": ["a", "ghost"]}, {"a": _pass("t1")})
        assert set(a["claims"]) == {"claim:a"}

    def test_falls_back_to_studies_by_slug(self):
        a = derive_argument({}, {"a": _pass("t1"), "b": _pass("t2")})
        assert set(a["claims"]) == {"claim:a", "claim:b"}


class TestDeriveEdges:
    def _edge(self, agraph, _from, _to):
        return next((e for e in agraph["edges"]
                     if e["from"] == _from and e["to"] == _to), None)

    def test_builds_on_is_prerequisite(self):
        studies = {"p": _pass("t1"),
                   "c": _pass("t1") | {"parent_relations": [{"study": "p", "relation": "builds-on"}]}}
        a = derive_argument({"studies": ["p", "c"]}, studies)
        e = self._edge(a, "claim:p", "claim:c")
        assert e and e["type"] == "prerequisite"

    def test_tests_is_support_when_supported_else_challenge(self):
        sup = {"p": _pass("t1"),
               "c": _pass("t1") | {"parent_relations": [{"study": "p", "relation": "tests"}]}}
        assert self._edge(derive_argument({}, sup), "claim:p", "claim:c")["type"] == "support"
        chal = {"p": _pass("t1"),
                "c": _fail("t1") | {"parent_relations": [{"study": "p", "relation": "tests"}]}}
        assert self._edge(derive_argument({}, chal), "claim:p", "claim:c")["type"] == "challenge"

    def test_refutes_and_alternative_to_are_challenge(self):
        studies = {"p": _pass("t1"),
                   "c": _pass("t1") | {"parent_relations": [
                       {"study": "p", "relation": "refutes"}]},
                   "d": _pass("t1") | {"parent_relations": [
                       {"study": "p", "relation": "alternative-to"}]}}
        a = derive_argument({}, studies)
        assert self._edge(a, "claim:p", "claim:c")["type"] == "challenge"
        assert self._edge(a, "claim:p", "claim:d")["type"] == "challenge"

    def test_motivates_is_not_a_claim_edge(self):
        studies = {"p": _pass("t1"),
                   "c": _pass("t1") | {"parent_relations": [{"study": "p", "relation": "motivates"}]}}
        a = derive_argument({}, studies)
        assert self._edge(a, "claim:p", "claim:c") is None

    def test_parent_studies_gate_is_not_an_argument_edge(self):
        """parent_studies is the organizational gate — it must never become a GE edge."""
        studies = {"p": _pass("t1"),
                   "c": _pass("t1") | {"parent_studies": [{"study": "p", "condition": "tests-passed"}]}}
        a = derive_argument({}, studies)
        assert a["edges"] == [] or self._edge(a, "claim:p", "claim:c") is None


class TestDeriveArms:
    def test_per_arm_claims_and_synthesis_edges(self):
        study = _pass("t1") | {"claim": "operating window exists", "arms": [
            {"id": "a1", "verdict": "supported"},
            {"id": "a2", "gate_status": "passed"},
        ]}
        a = derive_argument({"studies": ["m"]}, {"m": study})
        assert "claim:m:a1" in a["claims"] and "claim:m:a2" in a["claims"]
        assert a["claims"]["claim:m:a1"]["verdict"] == "supported"
        assert a["claims"]["claim:m:a2"]["verdict"] == "supported"  # from gate_status
        # the study-level claim synthesises its arms
        syn = [e for e in a["edges"] if e["type"] == "synthesis" and e["to"] == "claim:m"]
        assert {"claim:m:a1", "claim:m:a2"} == {e["from"] for e in syn}


# ---------------------------------------------------------------------------
# merge_argument — authored wins, derived fills, empty never clobbers
# ---------------------------------------------------------------------------

class TestMergeArgument:
    def test_authored_claim_field_wins_derived_fills(self):
        derived = {"claims": {"claim:a": {"proposition": "P", "scope": None}}, "edges": []}
        authored = {"claims": {"claim:a": {"scope": "within model class"}}, "edges": []}
        m = merge_argument(authored, derived)
        assert m["claims"]["claim:a"]["scope"] == "within model class"
        assert m["claims"]["claim:a"]["proposition"] == "P"

    def test_empty_authored_never_clobbers(self):
        derived = {"claims": {"claim:a": {"scope": "derived scope"}}, "edges": []}
        authored = {"claims": {"claim:a": {"scope": ""}}, "edges": []}
        assert merge_argument(authored, derived)["claims"]["claim:a"]["scope"] == "derived scope"

    def test_authored_edge_added_and_deduped(self):
        derived = {"claims": {}, "edges": [{"from": "claim:a", "to": "claim:b", "type": "support"}]}
        authored = {"claims": {}, "edges": [
            {"from": "claim:a", "to": "claim:b", "type": "support"},   # dup
            {"from": "claim:b", "to": "claim:c", "type": "challenge"},  # new
        ]}
        edges = merge_argument(authored, derived)["edges"]
        assert len(edges) == 2
        assert {"claim:c"} == {e["to"] for e in edges if e["type"] == "challenge"}
