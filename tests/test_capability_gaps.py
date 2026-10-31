"""Tests for the capability_gap bridge prototype (proposed → accepted → built)."""
from __future__ import annotations

import pytest

from discovery_engine.capability_gaps import (
    accept_capability_gap,
    build_capability_gap,
    close_built_capability_gaps,
    propose_capability_gaps,
    reject_capability_gap,
    required_mechanisms_from_spec,
)

# A tool-catalog of built capabilities (capability = tokenized slug). The recovery
# loop's capabilities are tools, so gap detection runs over this rather than the
# biological composite catalog.
TOOL_CATALOG = [
    {"id": "RateLawFitter", "composite": "pkg.RateLawFitter", "capability": ["rate", "law", "fitter"], "observables": []},
    {"id": "TrajectoryComparator", "composite": "pkg.TrajectoryComparator", "capability": ["trajectory", "comparator"], "observables": []},
]
REQ = ["rate-law-fitter", "trajectory-comparator", "non-recoverability-detector"]
INV = {"name": "discovery-sbml-recovery"}


def test_proposes_gap_only_for_missing_mechanism():
    gaps = propose_capability_gaps(INV, TOOL_CATALOG, REQ)
    assert [g["mechanism"] for g in gaps] == ["non-recoverability-detector"]
    g = gaps[0]
    assert g["id"].startswith("cg:discovery-sbml-recovery:")
    assert g["status"] == "proposed" and g["kind"] == "needs_build"
    assert g["approval"] == "review"  # rationale-gated, never auto-build


def test_content_addressed_and_idempotent():
    g1 = propose_capability_gaps(INV, TOOL_CATALOG, REQ)[0]
    # same inputs -> same id
    assert propose_capability_gaps(INV, TOOL_CATALOG, REQ)[0]["id"] == g1["id"]
    # a non-proposed gap already on the spec is not re-emitted
    inv2 = {**INV, "capability_gaps": [{**g1, "status": "accepted"}]}
    assert propose_capability_gaps(inv2, TOOL_CATALOG, REQ) == []


def test_accept_requires_rationale():
    g = propose_capability_gaps(INV, TOOL_CATALOG, REQ)[0]
    with pytest.raises(ValueError):
        accept_capability_gap(g, rationale="")
    accept_capability_gap(g, rationale="needed to recover the oscillator rung")
    assert g["status"] == "accepted" and g["accept"]["rationale"]


def test_build_requires_accept_first():
    g = propose_capability_gaps(INV, TOOL_CATALOG, REQ)[0]
    with pytest.raises(ValueError):
        build_capability_gap(g, lambda gap, ws=None: {"status": "reuse"})


def test_build_needs_author_when_hook_cannot_build():
    g = propose_capability_gaps(INV, TOOL_CATALOG, REQ)[0]
    accept_capability_gap(g, rationale="x")

    def hook(gap, ws_root=None):
        return {"status": "needs_implementation", "process": None, "detail": "no process", "gap_id": gap["id"]}

    build_capability_gap(g, hook)
    assert g["status"] == "needs_author"  # non-terminal, awaiting a human/agent


def test_build_marks_built_on_reuse():
    g = propose_capability_gaps(INV, TOOL_CATALOG, REQ)[0]
    accept_capability_gap(g, rationale="x")

    def hook(gap, ws_root=None):
        return {"status": "reuse", "process": "pkg.NonRecoverabilityDetector", "detail": "found", "gap_id": gap["id"]}

    build_capability_gap(g, hook)
    assert g["status"] == "built"
    assert g["built"]["process"].endswith("NonRecoverabilityDetector")


def test_close_on_fresh_recatalog():
    g = propose_capability_gaps(INV, TOOL_CATALOG, REQ)[0]
    accept_capability_gap(g, rationale="x")
    fresh = TOOL_CATALOG + [
        {"id": "NRD", "composite": "pkg.NonRecoverabilityDetector",
         "capability": ["non", "recoverability", "detector"], "observables": []}
    ]
    closed = close_built_capability_gaps([g], fresh)
    assert g["id"] in closed and g["status"] == "built"


def test_reject_is_terminal():
    g = propose_capability_gaps(INV, TOOL_CATALOG, REQ)[0]
    reject_capability_gap(g, rationale="out of scope")
    assert g["status"] == "rejected"
    # a rejected gap is not re-emitted
    inv2 = {**INV, "capability_gaps": [g]}
    assert propose_capability_gaps(inv2, TOOL_CATALOG, REQ) == []


def test_required_mechanisms_drawn_from_spec():
    # Domain-general: no explicit mechanisms -> drawn from the investigation contract.
    inv = {"name": "discovery-sbml-recovery",
           "required_mechanisms": ["rate-law-fitter", "non-recoverability-detector"]}
    gaps = propose_capability_gaps(inv, TOOL_CATALOG)
    assert [g["mechanism"] for g in gaps] == ["non-recoverability-detector"]


def test_required_mechanisms_union_of_spec_and_studies():
    inv = {"name": "x", "required_mechanisms": ["a"]}
    studies = {"s1": {"required_mechanisms": ["b", "a"]}, "s2": {"required_mechanisms": ["c"]}}
    assert required_mechanisms_from_spec(inv, studies) == ["a", "b", "c"]
