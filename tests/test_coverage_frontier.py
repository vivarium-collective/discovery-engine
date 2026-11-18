"""Proactive coverage-gap frontier (#2 Phase 1): a configured scientific dimension
no study probes yet becomes a frontier item -> a propose_study generation request.
The engine detects the gap deterministically; it never authors the question itself.
"""
from discovery_engine.projection.coverage import coverage_gap_items
from discovery_engine.projection.engine import frontier
from discovery_engine.phases.generation_emit import generation_requests


def _inv(dims, **extra):
    d = {"question": "", "studies": [], "discovery": {"coverage_dimensions": dims}}
    d.update(extra)
    return d


def test_no_dimensions_configured_emits_nothing():
    assert coverage_gap_items({}, {"discovery": {}}) == []
    assert coverage_gap_items({}, {}) == []
    assert coverage_gap_items({}, None) == []


def test_uncovered_is_raised_covered_is_not():
    dims = [{"name": "single-cell variability", "keywords": ["variability", "noise"]},
            {"name": "growth-law scaling", "keywords": ["growth law", "scaling"]}]
    inv = _inv(dims, question="How does growth-law scaling vary with nutrients?")
    targets = [t for t, _ in coverage_gap_items({}, inv)]
    assert "coverage:single-cell-variability" in targets    # uncovered -> raised
    assert "coverage:growth-law-scaling" not in targets      # keywords in the question -> covered


def test_dimension_question_is_the_rationale():
    dims = [{"name": "robustness", "keywords": ["robustness"],
             "question": "Is the phenotype robust to perturbation?"}]
    assert coverage_gap_items({}, _inv(dims)) == [
        ("coverage:robustness", "Is the phenotype robust to perturbation?")]


def test_covered_by_a_derived_claim_statement():
    dims = [{"name": "overflow metabolism", "keywords": ["overflow", "acetate"]}]
    cr = {"argument": {"claims": {"c1": {"statement": "acetate overflow rises above the critical rate"}}}}
    assert coverage_gap_items(cr, _inv(dims)) == []           # covered by the claim text


def test_keywordless_dimension_matches_on_its_name():
    dims = [{"name": "chemotaxis"}]
    assert coverage_gap_items({}, _inv(dims, question="a study of chemotaxis gradients")) == []
    assert [t for t, _ in coverage_gap_items({}, _inv(dims, question="unrelated"))] == ["coverage:chemotaxis"]


def test_coverage_gap_reaches_frontier_and_becomes_propose_study():
    dims = [{"name": "proteome allocation", "keywords": ["proteome", "allocation"]}]
    inv = _inv(dims, question="How does colony cost scale with N?")
    f = frontier({}, inv)
    cov = [i for i in f if i["kind"] == "coverage_gap"]
    assert cov and cov[0]["target"] == "coverage:proteome-allocation"
    assert cov[0]["op"] == "branch"
    # end-to-end: a coverage_gap opportunity routes to propose_study -> viva-investigation
    opp = {"id": "coverage_gap:x", "source": "coverage:proteome-allocation",
           "action_type": "branch", "kind": "coverage_gap", "value": {"score": 0.2},
           "question": "probe proteome allocation"}
    r = generation_requests([opp])[0]
    assert r.kind == "propose_study"
    assert r.skill == "viva-investigation"
