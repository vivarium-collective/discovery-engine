"""Tests for discovery_engine.investigation_ops — Slice-2a: branch + link.

The investigation-operations algebra is a set of NON-DESTRUCTIVE, pure
functions over plain study / investigation dicts (the on-disk study.yaml and
investigation.yaml shapes). No process_bigraph import; inputs are never
mutated; every op returns a new dict.

Covers:
- branch: the edge role drives inheritance (E_sci families) —
    * builds_on / leads-to: carry the parent's results forward as inputs, with
      fresh/empty model-bearing + results fields (the model is left for the
      author);
    * tests: copy the parent's model-bearing fields verbatim but reset the
      verdict/status and clear runs/findings (a re-test with a new gate);
    * alternative_to: carry ONLY the question (+ hypothesis if present) — a
      competing model for the same question.
  For every role the child is stamped planned / unassessed with a roled
  parent_studies edge, child_overrides win, and the parent is left unchanged.
- link: add a scientific edge a->b to an investigation-level edges[] list,
  idempotently, without creating nodes or mutating the input.
"""
from __future__ import annotations

import copy

import pytest

from discovery_engine.investigation_ops import branch, link


# ---------------------------------------------------------------------------
# Fixtures — a realistic (genesis-shaped) parent study spec
# ---------------------------------------------------------------------------

def _parent():
    return {
        "name": "og-02",
        "status": "ran",
        "verdict": "supported",
        "execution_status": "completed",
        "question": "Does X capture free energy?",
        "hypothesis": "A carrier captures and couples free energy.",
        "claim": "X captures free energy with a closing ledger.",
        "arms": [{"id": "arm1", "verdict": "supported"}],
        "conditions": [{"name": "nominal"}],
        "behavior_tests": [{"name": "currency-maintained", "pass_if": {"op": ">=", "value": 0.25}}],
        "visualizations": [{"name": "energy-panel"}],
        "runs": [{"name": "run-1", "status": "completed"}],
        "findings": [{"id": "f1", "statement": "captured"}],
        "parent_studies": [{"study": "og-00", "relationship": "builds_on"}],
    }


# ---------------------------------------------------------------------------
# branch — common stamping (every role)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["builds_on", "leads-to", "tests", "alternative_to"])
def test_branch_stamps_name_status_verdict_and_parent_edge(role):
    child = branch(_parent(), role, "og-child")
    assert child["name"] == "og-child"
    assert child["status"] == "planned"
    assert child["verdict"] == "unassessed"
    assert child["parent_studies"] == [{"study": "og-02", "relationship": role}]


# ---------------------------------------------------------------------------
# branch — builds_on / leads-to: results carried forward, model fresh
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["builds_on", "leads-to"])
def test_branch_builds_on_carries_results_as_inputs_and_resets_model(role):
    child = branch(_parent(), role, "og-child")
    # a reference to the parent's results as inputs
    assert child["builds_on"] == ["og-02"]
    assert "inputs_from" in child and child["inputs_from"]
    # fresh / empty model-bearing + results fields; model left for the author
    assert child["arms"] == []
    assert child["runs"] == []
    assert child["findings"] == []
    assert child["visualizations"] == []
    # the parent's model is NOT copied verbatim for this role
    assert "behavior_tests" not in child or child["behavior_tests"] == []


# ---------------------------------------------------------------------------
# branch — tests: copy the model verbatim, reset verdict/status, clear results
# ---------------------------------------------------------------------------

def test_branch_tests_copies_model_verbatim_and_resets_gate():
    parent = _parent()
    child = branch(parent, "tests", "og-adv")
    # model-bearing fields copied verbatim
    assert child["arms"] == parent["arms"]
    assert child["conditions"] == parent["conditions"]
    assert child["behavior_tests"] == parent["behavior_tests"]
    assert child["visualizations"] == parent["visualizations"]
    # but reset to an unassessed, planned re-test
    assert child["verdict"] == "unassessed"
    assert child["status"] == "planned"
    # results cleared (a re-test with a new gate)
    assert child["runs"] == []
    assert child["findings"] == []


def test_branch_tests_deep_copies_model_so_child_edits_dont_touch_parent():
    parent = _parent()
    child = branch(parent, "tests", "og-adv")
    child["behavior_tests"][0]["pass_if"]["value"] = 0.99
    child["arms"][0]["verdict"] = "refuted"
    assert parent["behavior_tests"][0]["pass_if"]["value"] == 0.25
    assert parent["arms"][0]["verdict"] == "supported"


# ---------------------------------------------------------------------------
# branch — alternative_to: carry only the question (+ hypothesis)
# ---------------------------------------------------------------------------

def test_branch_alternative_to_carries_only_question_and_hypothesis():
    parent = _parent()
    child = branch(parent, "alternative_to", "og-alt")
    assert child["question"] == parent["question"]
    assert child["hypothesis"] == parent["hypothesis"]
    # everything else fresh — the model, arms, results are NOT inherited
    assert "arms" not in child
    assert "behavior_tests" not in child
    assert "runs" not in child
    assert "findings" not in child
    assert "claim" not in child


def test_branch_alternative_to_without_hypothesis_omits_it():
    parent = _parent()
    del parent["hypothesis"]
    child = branch(parent, "alternative_to", "og-alt")
    assert child["question"] == "Does X capture free energy?"
    assert "hypothesis" not in child


# ---------------------------------------------------------------------------
# branch — non-destructive + overrides + validation
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("role", ["builds_on", "leads-to", "tests", "alternative_to"])
def test_branch_does_not_mutate_parent(role):
    parent = _parent()
    before = copy.deepcopy(parent)
    branch(parent, role, "og-child", {"status": "running"})
    assert parent == before


def test_branch_child_overrides_win_shallow():
    child = branch(
        _parent(),
        "tests",
        "og-adv",
        {"status": "running", "verdict": "qualified", "note": "hand-set"},
    )
    assert child["status"] == "running"
    assert child["verdict"] == "qualified"
    assert child["note"] == "hand-set"
    # un-overridden fields keep their branch value
    assert child["name"] == "og-adv"


def test_branch_rejects_unknown_edge_role():
    with pytest.raises(ValueError):
        branch(_parent(), "frobnicate", "og-child")


# ---------------------------------------------------------------------------
# link — add a scientific edge, idempotent, non-destructive, validated
# ---------------------------------------------------------------------------

def test_link_adds_edge():
    inv = {"name": "inv", "studies": ["a", "b"]}
    out = link(inv, "a", "b", "builds_on")
    assert out["edges"] == [{"from": "a", "to": "b", "type": "builds_on"}]
    # does not create nodes
    assert out["studies"] == ["a", "b"]


def test_link_appends_to_existing_edges():
    inv = {"name": "inv", "edges": [{"from": "a", "to": "b", "type": "builds_on"}]}
    out = link(inv, "b", "c", "leads-to")
    assert out["edges"] == [
        {"from": "a", "to": "b", "type": "builds_on"},
        {"from": "b", "to": "c", "type": "leads-to"},
    ]


def test_link_is_idempotent():
    inv = {"name": "inv"}
    once = link(inv, "a", "b", "tests")
    twice = link(once, "a", "b", "tests")
    assert twice["edges"] == [{"from": "a", "to": "b", "type": "tests"}]


def test_link_does_not_mutate_input():
    inv = {"name": "inv", "edges": [{"from": "a", "to": "b", "type": "builds_on"}]}
    before = copy.deepcopy(inv)
    link(inv, "b", "c", "motivates")
    assert inv == before


def test_link_rejects_unknown_edge_type():
    with pytest.raises(ValueError):
        link({"name": "inv"}, "a", "b", "frobnicate")


@pytest.mark.parametrize(
    "edge_type",
    ["builds_on", "leads-to", "tests", "motivates", "alternative_to", "consolidated_from"],
)
def test_link_accepts_every_vocab_edge(edge_type):
    out = link({"name": "inv"}, "a", "b", edge_type)
    assert out["edges"][0]["type"] == edge_type
