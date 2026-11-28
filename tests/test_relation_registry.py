"""Relation registry: canonical vocabulary + the drift guard that keeps the
existing scattered vocabularies reconciled under it."""
import itertools

import pytest

from discovery_engine.projection import relation_registry as rr


# --- the canonical core ----------------------------------------------------

def test_canonical_set_is_hyphen_form():
    assert rr.CANONICAL_SCI == ("builds-on", "tests", "refutes", "motivates", "alternative-to")
    for r in rr.CANONICAL_SCI:
        assert rr.is_canonical(r)
        assert rr.normalize(r) == r
        assert rr.family(r) == "E_sci"


def test_underscore_and_hyphen_normalize_together():
    # the live hyphen/underscore split is the whole point
    assert rr.normalize("builds_on") == rr.normalize("builds-on") == "builds-on"
    assert rr.normalize("alternative_to") == rr.normalize("alternative-to") == "alternative-to"
    assert not rr.is_canonical("builds_on")  # it's an alias, not canonical
    assert rr.family("builds_on") == "E_sci"


def test_legacy_aliases_map_to_canonical():
    assert rr.normalize("leads-to") == "builds-on"
    assert rr.normalize("model-input") == "builds-on"
    assert rr.normalize("calibrates-threshold") == "builds-on"
    assert rr.normalize("precedes") == "builds-on"
    assert rr.normalize("evidence") == "tests"
    assert rr.normalize("disconfirms") == "refutes"


def test_case_and_whitespace_insensitive():
    assert rr.normalize("  Builds-On ") == "builds-on"
    assert rr.normalize(None) is None
    assert rr.normalize("") is None


# --- the axes stay apart ---------------------------------------------------

def test_execution_gate_conditions_are_not_sci_relations():
    # condition: tests-passed | ran | complete is the acyclic execution gate — a
    # DIFFERENT axis from the relation type, and must never read as a claim edge.
    for cond in ("tests-passed", "ran", "complete"):
        assert rr.normalize(cond) is None
        assert rr.family(cond) == "E_org"


def test_consolidation_and_provenance_are_not_sci():
    assert rr.normalize("consolidated_from") is None
    assert rr.family("consolidated_from") == "E_prov"
    assert rr.family("generated_by") == "E_prov"
    assert rr.family("member_of") == "E_org"


# --- the lint payload ------------------------------------------------------

def test_unknown_relations_flags_new_dialects_only():
    rels = ["builds-on", "builds_on", "tests-passed", "", None, "frobnicates", "co-occurs"]
    unknown = rr.unknown_relations(rels)
    assert set(unknown) == {"frobnicates", "co-occurs"}


def test_known_covers_every_family():
    assert rr.known("builds_on") and rr.known("ran") and rr.known("consolidated_from")
    assert not rr.known("totally-made-up")


# --- argument edge typing matches the existing projection ------------------

def test_argument_edge_type_centralizes_edge_logic():
    assert rr.argument_edge_type("builds-on", None) == "prerequisite"
    assert rr.argument_edge_type("refutes", "supported") == "challenge"
    assert rr.argument_edge_type("alternative-to", "supported") == "challenge"
    assert rr.argument_edge_type("tests", "supported") == "support"
    assert rr.argument_edge_type("tests", "refuted") == "challenge"
    assert rr.argument_edge_type("motivates", "supported") is None
    # and it normalizes, which the raw _edge_type does not:
    assert rr.argument_edge_type("builds_on", None) == "prerequisite"


def test_argument_edge_type_agrees_with_live_projection():
    """Cross-check against projection/argument.py:_edge_type for every canonical
    relation x verdict — if that function changes, this breaks loudly."""
    from discovery_engine.projection import argument as arg
    verdicts = ("supported", "refuted", "qualified", "inconclusive", "unassessed", None)
    for relation, v in itertools.product(rr.CANONICAL_SCI, verdicts):
        assert rr.argument_edge_type(relation, v) == arg._edge_type(relation, v)


# --- the DRIFT GUARD: the scattered live vocabularies reconcile here --------

def test_relations_epistemic_set_is_the_canonical_set():
    from discovery_engine.projection import relations as rel
    assert set(rel._EPISTEMIC) == set(rr.CANONICAL_SCI)


def test_every_engine_edge_vocab_term_is_recognized():
    """ops.py may use underscore + leads-to + consolidated_from; every one must be
    recognized by the registry (so a NEW term added to _EDGE_VOCAB fails here until
    it's reconciled)."""
    from discovery_engine.investigation import ops
    for term in ops._EDGE_VOCAB:
        assert rr.known(term), f"ops._EDGE_VOCAB term {term!r} unknown to the registry"
    # and the scientific ones among them normalize into the canonical set
    for term in ops._BRANCH_ROLES:
        assert rr.normalize(term) in rr.CANONICAL_SCI, term


def test_schema_parent_relation_enum_is_canonical_or_aliased():
    """The study-schema relation enum values all reconcile to canonical E_sci."""
    for term in ("builds-on", "tests", "refutes", "motivates", "alternative-to"):
        assert rr.normalize(term) in rr.CANONICAL_SCI


# --- richer semantic taxonomy (rev-2 adoption) -----------------------------

def test_semantic_family_splits_the_e_sci_lump():
    assert rr.semantic_family("tests") == "evidential"
    assert rr.semantic_family("supports") == "evidential"
    assert rr.semantic_family("explains") == "explanatory"
    assert rr.semantic_family("competes_with") == "explanatory"    # underscore alias
    assert rr.semantic_family("alternative-to") == "explanatory"
    assert rr.semantic_family("regulates") == "phenomenal"
    assert rr.semantic_family("builds-on") == "inquiry"
    assert rr.semantic_family("builds_on") == "inquiry"            # via the E_sci alias
    assert rr.semantic_family("extends") == "inquiry"
    assert rr.semantic_family("requires_output") == "execution"
    assert rr.semantic_family("derived_from") == "provenance"
    assert rr.semantic_family("totally-made-up") is None


def test_family_classifies_new_relations_into_compat_families():
    assert rr.family("supports") == "E_evid"          # evidential -> E_evid
    assert rr.family("explains") == "E_sci"           # explanatory -> E_sci
    assert rr.family("regulates") == "E_sci"          # phenomenal -> E_sci
    assert rr.family("extends") == "E_org"            # inquiry -> E_org
    assert rr.family("derived-from") == "E_prov"
    # the existing coarse classifications are unchanged
    assert rr.family("builds-on") == "E_sci"
    assert rr.family("tests-passed") == "E_org"
    assert rr.family("consolidated_from") == "E_prov"


def test_known_recognizes_the_new_semantic_relations():
    assert rr.known("supports") and rr.known("explains") and rr.known("regulates")
    assert rr.unknown_relations(["tests", "supports", "explains", "frobnicates"]) == ["frobnicates"]
