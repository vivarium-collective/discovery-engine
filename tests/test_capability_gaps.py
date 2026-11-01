"""Tests for the capability_gap bridge prototype (proposed → accepted → built)."""
from __future__ import annotations

import pytest

from discovery_engine.capability_gaps import (
    accept_capability_gap,
    build_capability_gap,
    close_built_capability_gaps,
    propose_capability_gaps,
    CAP,
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


def P(**kw):
    return propose_capability_gaps(INV, {}, TOOL_CATALOG, required_mechanisms=REQ, **kw)


def test_proposes_gap_only_for_missing_mechanism():
    gaps = P()
    assert [g["mechanism"] for g in gaps] == ["non-recoverability-detector"]
    g = gaps[0]
    assert g["id"].startswith("cg:discovery-sbml-recovery:")
    assert g["status"] == "proposed" and g["kind"] == "capability_gap" and g["op"] == "build_process"
    assert g["approval"] == "review"  # rationale-gated, never auto-build


def test_content_addressed_and_idempotent():
    g1 = P()[0]
    # same inputs, DIFFERENT now -> same id (a now()-in-id leak goes red)
    assert P(now="2020-01-01T00:00:00Z")[0]["id"] == g1["id"]
    assert P(now="2031-12-31T23:59:59Z")[0]["id"] == g1["id"]
    # a non-proposed gap already on the spec is not re-emitted
    inv2 = {**INV, "capability_gaps": [{**g1, "status": "accepted"}]}
    assert propose_capability_gaps(inv2, {}, TOOL_CATALOG, required_mechanisms=REQ) == []


def test_accept_requires_rationale():
    g = P()[0]
    with pytest.raises(ValueError):
        accept_capability_gap(g, rationale="")
    accept_capability_gap(g, rationale="needed to recover the oscillator rung")
    assert g["status"] == "accepted" and g["accept"]["rationale"]


def test_build_requires_accept_first():
    g = P()[0]
    with pytest.raises(ValueError):
        build_capability_gap(g, lambda gap, ws=None: {"status": "reuse"})


def test_build_needs_author_when_hook_cannot_build():
    g = P()[0]
    accept_capability_gap(g, rationale="x")

    def hook(gap, ws_root=None):
        return {"status": "needs_implementation", "process": None, "detail": "no process", "gap_id": gap["id"]}

    build_capability_gap(g, hook)
    assert g["status"] == "needs_author"  # non-terminal, awaiting a human/agent


def test_build_marks_built_on_reuse():
    g = P()[0]
    accept_capability_gap(g, rationale="x")

    def hook(gap, ws_root=None):
        return {"status": "reuse", "process": "pkg.NonRecoverabilityDetector", "detail": "found", "gap_id": gap["id"]}

    build_capability_gap(g, hook)
    assert g["status"] == "built"
    assert g["built"]["process"].endswith("NonRecoverabilityDetector")


def test_close_on_fresh_recatalog():
    g = P()[0]
    accept_capability_gap(g, rationale="x")
    fresh = TOOL_CATALOG + [
        {"id": "NRD", "composite": "pkg.NonRecoverabilityDetector",
         "capability": ["non", "recoverability", "detector"], "observables": []}
    ]
    closed = close_built_capability_gaps([g], fresh, {}, now="2030-01-01T00:00:00Z")
    assert g["id"] in closed and g["status"] == "built"
    assert g["built"]["process"] == "pkg.NonRecoverabilityDetector"
    assert g["built"]["at"] == "2030-01-01T00:00:00Z"  # honors now


def test_reject_is_terminal():
    g = P()[0]
    reject_capability_gap(g, rationale="out of scope")
    assert g["status"] == "rejected"
    # a rejected gap is not re-emitted
    inv2 = {**INV, "capability_gaps": [g]}
    assert propose_capability_gaps(inv2, {}, TOOL_CATALOG, required_mechanisms=REQ) == []


def test_required_mechanisms_drawn_from_spec():
    # Domain-general: no explicit mechanisms -> drawn from the investigation contract.
    inv = {"name": "discovery-sbml-recovery",
           "required_mechanisms": ["rate-law-fitter", "non-recoverability-detector"]}
    gaps = propose_capability_gaps(inv, {}, TOOL_CATALOG)
    assert [g["mechanism"] for g in gaps] == ["non-recoverability-detector"]


def test_required_mechanisms_union_of_spec_and_studies():
    inv = {"name": "x", "required_mechanisms": ["a"]}
    studies = {"s1": {"required_mechanisms": ["b", "a"]}, "s2": {"required_mechanisms": ["c"]}}
    assert required_mechanisms_from_spec(inv, studies) == ["a", "b", "c"]


def _accepted():
    g = P()[0]
    accept_capability_gap(g, rationale="x")
    return g


def test_close_ignores_catalog_without_mechanism_negative():
    g = _accepted()
    assert close_built_capability_gaps([g], TOOL_CATALOG, {}) == []
    assert g["status"] == "accepted"
    assert close_built_capability_gaps([g], [], {}) == [] and close_built_capability_gaps([g], None, {}) == []


def test_close_only_accepted_and_no_fuzzy_substring():
    fresh = TOOL_CATALOG + [{"id": "D", "composite": "pkg.Detector", "capability": ["detector"]}]
    has_it = fresh + [{"id": "NRD", "composite": "pkg.NRD", "capability": ["non", "recoverability", "detector"]}]
    proposed = P()[0]  # catalog DOES provide it; only the accepted-gate keeps it open
    assert close_built_capability_gaps([proposed], has_it, {}) == [] and proposed["status"] == "proposed"
    g = _accepted()  # 'detector' alone must not close non-recoverability-detector
    assert close_built_capability_gaps([g], fresh, {}) == [] and g["status"] == "accepted"


def test_non_blocking_kind():
    import discovery_engine.discovery_engine as de
    assert "capability_gap" not in de._CLOSURE_BLOCKING
    assert "needs_build" not in de._CLOSURE_BLOCKING


@pytest.mark.parametrize("bad", ["", "   ", "\n\t", None])
def test_whitespace_rationale_refused(bad):
    with pytest.raises(ValueError):
        accept_capability_gap(P()[0], rationale=bad)
    with pytest.raises(ValueError):
        reject_capability_gap(P()[0], rationale=bad)


def test_accept_reject_validate_target():
    with pytest.raises(ValueError):
        accept_capability_gap({"id": "vp:x:1", "status": "proposed"}, rationale="r")
    with pytest.raises(ValueError):
        accept_capability_gap({}, rationale="r")
    g = _accepted()
    with pytest.raises(ValueError):
        accept_capability_gap(g, rationale="again")  # not proposed
    with pytest.raises(ValueError):
        reject_capability_gap(g, rationale="r")  # accepted, not proposed


def test_reject_terminal_refused():
    g = P()[0]
    reject_capability_gap(g, rationale="no")
    with pytest.raises(ValueError):
        reject_capability_gap(g, rationale="again")
    with pytest.raises(ValueError):
        accept_capability_gap(g, rationale="revive")
    assert g["status"] == "rejected"


def test_hook_exception_becomes_failed():
    g = _accepted()

    def boom(gap, ws_root=None):
        raise RuntimeError("kaboom")

    build_capability_gap(g, boom)
    assert g["status"] == "failed" and "kaboom" in g["detail"]


def test_build_verifies_hook_result():
    cat = TOOL_CATALOG + [{"id": "NRD", "composite": "pkg.NRD", "capability": ["non", "recoverability", "detector"]}]
    g = _accepted()
    build_capability_gap(g, lambda gap, ws=None: {"status": "reuse", "process": None, "gap_id": gap["id"]})
    assert g["status"] == "failed"  # no process named
    g = _accepted()
    build_capability_gap(g, lambda gap, ws=None: {"status": "reuse", "process": "pkg.Ghost", "gap_id": gap["id"]},
                         catalog=cat)
    assert g["status"] == "failed"  # process not in catalog
    g = _accepted()
    build_capability_gap(g, lambda gap, ws=None: {"status": "reuse", "process": "pkg.NRD", "gap_id": "cg:other:1"})
    assert g["status"] == "failed"  # wrong gap id
    g = _accepted()
    build_capability_gap(g, lambda gap, ws=None: {"status": "built", "process": "pkg.NRD", "gap_id": gap["id"]},
                         catalog=cat)
    assert g["status"] == "built" and g["built"]["process"] == "pkg.NRD"


def test_empty_catalog_proposes_nothing():
    for cat in ([], None):
        assert propose_capability_gaps(INV, {}, cat, required_mechanisms=REQ) == []


def test_derivable_vs_needs_author():
    near = [{"id": "X", "composite": "pkg.X", "capability": ["metric"],
             "builds": ["non-recoverability-detector"]}]
    g = propose_capability_gaps(INV, {}, near, required_mechanisms=["non-recoverability-detector"])[0]
    assert g["derivable"] is True and g["suggested"]["needs_author"] is False
    far = [{"id": "Y", "composite": "pkg.Y", "capability": ["unrelated"]}]
    g = propose_capability_gaps(INV, {}, far, required_mechanisms=["non-recoverability-detector"])[0]
    assert g["derivable"] is False and g["suggested"]["needs_author"] is True


def test_provenance_and_cap_per_study():
    studies = {"s1": {"required_mechanisms": ["m-a", "m-b", "m-c"]}, "s2": {"required_mechanisms": ["m-d"]}}
    gaps = propose_capability_gaps(INV, studies, TOOL_CATALOG)
    per = {}
    for g in gaps:
        per[g["study"]] = per.get(g["study"], 0) + 1
        assert g["source"]["study"] == g["study"] and g["source"]["mechanism"] == g["mechanism"]
    assert per == {"s1": CAP, "s2": 1}


def test_no_inv_name_and_empty_override():
    g = propose_capability_gaps({}, {}, TOOL_CATALOG, required_mechanisms=["zzz"])[0]
    assert not g["id"].startswith("cg::")
    inv = {"name": "i", "required_mechanisms": ["zzz"]}
    assert len(propose_capability_gaps(inv, {}, TOOL_CATALOG, required_mechanisms=[])) == 1  # [] == None


def test_public_provides():
    from discovery_engine.model_architect import provides
    assert provides(TOOL_CATALOG[0], "rate-law-fitter") is True
    assert provides(TOOL_CATALOG[0], "zzz") is False


def test_non_dict_gap_refused_cleanly():
    for bad in (None, "cg:x:1"):
        with pytest.raises(ValueError):
            accept_capability_gap(bad, rationale="r")
        with pytest.raises(ValueError):
            reject_capability_gap(bad, rationale="r")


def test_build_with_empty_catalog_fails():
    g = _accepted()
    build_capability_gap(g, lambda gap, ws=None: {"status": "reuse", "process": "pkg.NRD", "gap_id": gap["id"]},
                         catalog=[])
    assert g["status"] == "failed"


# ── G4 (trial finding): prose "mechanisms" must not each become a capability gap ──
def test_is_buildable_mechanism_predicate():
    from discovery_engine.capability_gaps import _is_buildable_mechanism
    assert _is_buildable_mechanism("proto_endosymbiosis_integration")
    assert _is_buildable_mechanism("rate-law-fitter")
    assert _is_buildable_mechanism("viva_genesis.composites.x")
    assert _is_buildable_mechanism("plasmid replication")   # a legit multi-word mechanism NAME
    assert not _is_buildable_mechanism("Register matched runs; distinguish contact from enclosure")
    assert not _is_buildable_mechanism("author bands, then run the discriminating experiment")  # prose
    assert not _is_buildable_mechanism("")


def test_prose_mechanism_is_filtered_not_a_gap():
    # free-text design notes must not each become a buildable capability-gap author-stub (G4)
    req = ["non-recoverability-detector",
           "Register matched runs; distinguish contact from enclosure",  # prose (;) -> filtered
           "author bands, then run the experiment"]                      # prose (,) -> filtered
    gaps = propose_capability_gaps(INV, {}, TOOL_CATALOG, required_mechanisms=req)
    mechs = [g["mechanism"] for g in gaps]
    assert "non-recoverability-detector" in mechs            # the real token still proposes a gap
    assert "Register matched runs; distinguish contact from enclosure" not in mechs
    assert "author bands, then run the experiment" not in mechs
