"""Tests for discovery_engine.relations — the unified typed relations accessor.

RFC-0006 Tier 2: four keys name the same parent studies with different facets
(parent_studies = organizational gate, parent_relations = epistemic, builds_on =
lineage, upstream_state_consumed = computational). `study_relations` returns one
list of `{study, relation?, condition?, rationale?, provides?}` entries, merged
by study. Derive-first: an enriched `parent_studies` entry wins; the legacy keys
are merged in for back-compat so nothing breaks before migration.
"""
from __future__ import annotations

from discovery_engine.relations import study_relations, epistemic_relations


def _by_study(rels):
    return {r["study"]: r for r in rels}


def test_unifies_all_four_keys_by_study():
    spec = {
        "parent_studies": [{"study": "a", "condition": "tests-passed"}],
        "parent_relations": [{"study": "a", "relation": "builds-on", "rationale": "r"}],
        "builds_on": ["b"],
        "upstream_state_consumed": [{"from": "a", "provides": "flux"}],
    }
    rels = _by_study(study_relations(spec))
    assert set(rels) == {"a", "b"}
    assert rels["a"] == {"study": "a", "condition": "tests-passed",
                         "relation": "builds-on", "rationale": "r", "provides": "flux"}
    assert rels["b"]["relation"] == "builds-on"      # from builds_on lineage


def test_enriched_parent_studies_entry_wins():
    # once migrated, the relation lives on the parent_studies entry; it wins over
    # a legacy parent_relations entry.
    spec = {
        "parent_studies": [{"study": "a", "condition": "tests-passed", "relation": "tests"}],
        "parent_relations": [{"study": "a", "relation": "builds-on"}],
    }
    assert _by_study(study_relations(spec))["a"]["relation"] == "tests"


def test_epistemic_relations_filters_to_the_scientific_edge_vocab():
    spec = {"parent_studies": [
        {"study": "a", "relation": "tests"},
        {"study": "b", "condition": "tests-passed"},          # gate only, no relation
        {"study": "c", "relation": "builds-on"},
    ]}
    eps = {r["study"] for r in epistemic_relations(spec)}
    assert eps == {"a", "c"}                                   # b has no epistemic relation


def test_pipeline_gate_prerequisites_is_the_canonical_source():
    # the canonical Section-2 field wins over legacy parent_studies (Tier 2b)
    spec = {
        "pipeline_gate": {"prerequisites": [
            {"study": "a", "condition": "tests-passed", "relation": "builds-on",
             "outputs_used": "flux"}]},
        "parent_studies": [{"study": "a", "relation": "tests"}],   # legacy, loses
    }
    r = _by_study(study_relations(spec))["a"]
    assert r["relation"] == "builds-on"          # pipeline_gate wins
    assert r["provides"] == "flux"               # outputs_used -> provides
    assert r["condition"] == "tests-passed"


def test_empty_spec_is_empty():
    assert study_relations({}) == [] and epistemic_relations({}) == []
