"""study_edge_proposed: the cycle PROPOSES a study-graph edge latent in the prose — a study whose
claim/findings explicitly name another member study without a declared relation. Surfaced for
accept via `discover relate`, never auto-wired, respecting edge_integrity (no builds-on onto a
refuted parent)."""
from __future__ import annotations

from discovery_engine import discover
from discovery_engine.projection import engine as de
from discovery_engine.projection.study_edges import propose_study_edges

# a hand-authored chain whose claims literally cross-reference each other (Maya's grc case)
_CHAIN = {
    "grc-05": {"name": "grc-05", "claim": "the runaway is a synthesis latch; missing GTP brake"},
    "grc-06": {"name": "grc-06", "claim": "grc-05's GTP-brake is a phenomenological proxy"},
    "grc-07": {"name": "grc-07", "claim": "steps back from the RelA side grc-06 characterized"},
}


# --- the inference (pure) --------------------------------------------------------------------

def test_edges_recovered_from_explicit_slug_references():
    props = {(p["study"], p["parent"]) for p in propose_study_edges(_CHAIN)}
    assert ("grc-06", "grc-05") in props
    assert ("grc-07", "grc-06") in props
    for p in propose_study_edges(_CHAIN):
        assert p["relation"] == "builds-on"
        assert p["command"] == f"discover relate {p['study']} {p['parent']} --relation builds-on"


def test_already_declared_edge_is_not_reproposed():
    s = {**_CHAIN, "grc-06": {**_CHAIN["grc-06"], "parent_studies": [{"study": "grc-05"}]}}
    assert ("grc-06", "grc-05") not in {(p["study"], p["parent"]) for p in propose_study_edges(s)}


def test_refuted_parent_is_not_proposed_edge_integrity():
    assert not [p for p in propose_study_edges(_CHAIN, verdicts={"grc-05": "refuted"})
                if p["parent"] == "grc-05"]


def test_no_cross_reference_no_proposal():
    s = {"a": {"name": "a", "claim": "x"}, "b": {"name": "b", "claim": "y"}}
    assert propose_study_edges(s) == []


def test_substring_slug_is_not_a_false_match():
    # 'grc-0' must not match inside 'grc-05'
    s = {"grc-0": {"name": "grc-0", "claim": "foo"},
         "grc-05": {"name": "grc-05", "claim": "about grc-0 wait no"}}
    # grc-05 mentions grc-0 explicitly -> that IS a valid edge; but grc-05 appearing elsewhere must
    # not spuriously match grc-0. Assert grc-0 is only proposed when grc-0 is actually referenced.
    props = {(p["study"], p["parent"]) for p in propose_study_edges(s)}
    assert ("grc-05", "grc-0") in props          # grc-05's text names grc-0
    assert ("grc-0", "grc-05") not in props      # grc-0's text does not name grc-05


def test_findings_text_counts_not_just_claim():
    s = {"up": {"name": "up", "claim": "k"},
         "down": {"name": "down", "findings": [{"statement": "up established the keystone"}]}}
    assert ("down", "up") in {(p["study"], p["parent"]) for p in propose_study_edges(s)}


# --- surfacing on the frontier (advisory, non-blocking, resolvable) --------------------------

def _cyc():
    return {"study_edge_proposals": [{"study": "grc-06", "parent": "grc-05", "relation": "builds-on"}]}


def test_proposal_surfaces_on_the_frontier():
    (it,) = [i for i in de.frontier(_cyc(), {}) if i["kind"] == "study_edge_proposed"]
    assert it["target"] == "grc-06"
    assert it["op"] == "relate"
    assert it["priority"] == de._PRIORITY["study_edge_proposed"] == 39
    assert "grc-05" in it["rationale"] and "discover relate" in it["rationale"]


def test_proposal_is_advisory_non_blocking_resolvable():
    from discovery_engine.discover.cycle import _ADVANCE_OPS
    assert "relate" not in _ADVANCE_OPS                       # never auto-wired by advance
    assert "study_edge_proposed" not in de._CLOSURE_BLOCKING  # does not keep the investigation open
    assert de.closure(_cyc(), {})["closed"] is True
    assert "study_edge_proposed" in discover.RESOLUTION_KINDS
    inv = {"resolutions": [{"kind": "study_edge_proposed", "target": "grc-06", "rationale": "dismissed"}]}
    assert not [i for i in de.frontier(_cyc(), inv) if i["kind"] == "study_edge_proposed"]


def test_relate_executor_is_review_not_auto():
    ex = de._executor_for("relate", "grc-06")
    assert ex["auto_safe"] is False and ex["consequence"] == "review"
    assert "discover relate grc-06" in ex["command"]
