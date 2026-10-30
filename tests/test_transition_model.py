"""Transition composition + conditional phase diagram (DE 2.0 Milestone 4 / Cycle N+2).

Gate: the decision rule classifies each regime to the right outcome, the sweep covers
the product of axes, the composite is a versioned model built on Cycle N+1 parents, and
the diagram is explicitly conditional on the unresolved enclosure disagreement.
"""
from __future__ import annotations

from discovery_engine import transition_model as T


def test_rule_classifies_each_regime():
    assert T.classify_transition_outcome({"proximity": "distant", "pump": "efficient", "heredity": "high"})["outcome"] == T.NO_TRANSITION
    assert T.classify_transition_outcome({"proximity": "proximate", "pump": "weak", "heredity": "high"})["outcome"] == T.COLLAPSE
    assert T.classify_transition_outcome({"proximity": "proximate", "pump": "efficient", "heredity": "low"})["outcome"] == T.TRANSIENT
    assert T.classify_transition_outcome({"proximity": "proximate", "pump": "efficient", "heredity": "high"})["outcome"] == T.STABLE


def test_surface_covers_the_product_of_axes():
    surface = T.estimate_outcome_surface()
    assert len(surface) == 2 * 2 * 2
    outcomes = {c["outcome"] for c in surface}
    assert T.STABLE in outcomes and T.NO_TRANSITION in outcomes and T.COLLAPSE in outcomes


def test_composed_model_versions_from_cycle_n1_parents():
    studies = {
        "s1": {"topic": "metabolism", "baseline": [{"name": "b", "composite": "viva_genesis.composites.metabolite_exchange_community.metabolite_exchange_community"}]},
        "s3": {"topic": "membrane-mechanics", "baseline": [{"name": "b", "composite": "viva_genesis.composites.protrusion_enclosure_cpm.protrusion_enclosure_cpm"}]}}
    mv = T.compose_transition_model(studies, parent_versions=["mv:A:1234", "mv:B:5678"])
    assert mv["parent_ids"] == ["mv:A:1234", "mv:B:5678"]
    assert mv["id"].startswith("mv:N+2:proto-endosymbiosis:")
    assert "exchange" in mv["components"] and "heredity" in mv["components"]


def test_diagram_is_conditional_on_enclosure_disagreement():
    # A LIVE disagreement: unresolved (no single observed) with arms spanning a range.
    inv = {"studies": ["s3"], "hypotheses": [{"id": "h", "study": "s3", "predictions": []}]}
    studies = {"s3": {"name": "s3", "findings": [
        {"id": "f", "evidence": {"measure": "enclosure",
                                 "by_topology": {"classical": 0.27, "inside_out": 0.83}}}]}}
    caveats = T.conditioning_caveats(inv, studies)
    assert any("s3" in c and "enclosure" in c for c in caveats)
    # once adjudicated + resolved, the caveat lifts
    studies["s3"]["findings"][0]["evidence"]["observed"] = 0.61
    studies["s3"]["findings"][0]["evidence"]["superseded"] = {"source": "CPM", "observed": 1.0,
                                                              "adjudicated_by": "run:arbiter"}
    assert not T.conditioning_caveats(inv, studies)
