"""Investigation operations — a non-destructive algebra over the study graph.

Slice-2a: ``branch`` and ``link``. These are pure functions over plain study /
investigation dicts (the on-disk ``study.yaml`` / ``investigation.yaml``
shapes) — no process_bigraph import, no engine grounding. Like
``knowledge_store.derive_knowledge`` they never mutate their inputs and always
return a new dict.

The investigation graph has a fixed set of typed operations. Each moves a
study's parts — model, simulation (arms/conditions/behavior_tests),
visualization, results (runs/findings) — along a TYPED edge. The edge role is
what drives inheritance: a ``builds_on`` child carries the parent's results
forward as inputs and starts with a fresh model; a ``tests`` child re-uses the
parent's model verbatim behind a fresh gate; an ``alternative_to`` child keeps
only the question and competes head-to-head.

Public API
----------
branch(parent_spec, edge_role, child_name, child_overrides=None) -> dict
    New child study spec; the edge role drives which parent parts are
    inherited vs reset. Stamps a roled ``parent_studies`` edge; applies
    ``child_overrides`` last (shallow merge, wins). Parent left unchanged.
link(inv_spec, a, b, edge_type) -> dict
    New inv_spec with a scientific edge a->b added to an investigation-level
    ``edges`` list. Idempotent; creates no node; input left unchanged.
"""
from __future__ import annotations

import copy

# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

#: the scientific-edge (E_sci) vocabulary accepted by ``link``
_EDGE_VOCAB = frozenset(
    {
        "builds_on",
        "leads-to",
        "tests",
        "motivates",
        "alternative_to",
        "consolidated_from",
    }
)

#: edge roles ``branch`` knows how to inherit along — the roles in the
#: edge-role -> inheritance table. Roles like ``motivates`` /
#: ``consolidated_from`` are valid scientific edges (``link``) but carry no
#: branch-inheritance recipe, so ``branch`` rejects them.
_BRANCH_ROLES = frozenset({"builds_on", "leads-to", "tests", "alternative_to"})

#: model-bearing fields a ``tests`` re-test copies from the parent verbatim
_MODEL_FIELDS = ("arms", "conditions", "behavior_tests", "visualizations")


# ---------------------------------------------------------------------------
# branch
# ---------------------------------------------------------------------------

def branch(
    parent_spec: dict,
    edge_role: str,
    child_name: str,
    child_overrides: dict | None = None,
) -> dict:
    """Return a NEW child study spec branched from ``parent_spec``.

    The ``edge_role`` drives inheritance (the E_sci families):

    - ``builds_on`` / ``leads-to`` — the child carries a reference to the
      parent's results as inputs (``builds_on: [parent]`` + an ``inputs_from``
      note) and starts with fresh/empty ``arms``, ``runs``, ``findings`` and
      ``visualizations``; the model is left for the author.
    - ``tests`` — the child COPIES the parent's model-bearing fields
      (``arms``, ``conditions``, ``behavior_tests``, ``visualizations``)
      verbatim, but the verdict/status reset and ``runs``/``findings`` clear:
      a re-test behind a fresh gate.
    - ``alternative_to`` — the child carries ONLY the ``question`` (and
      ``hypothesis`` if present); everything else is fresh — a competing model
      for the same question.

    For every role the child is stamped ``name = child_name``,
    ``status = "planned"``, ``verdict = "unassessed"`` and
    ``parent_studies = [{"study": parent, "relationship": edge_role}]``.
    ``child_overrides`` is applied last as a shallow merge (it wins). The
    ``parent_spec`` is never mutated.
    """
    if edge_role not in _BRANCH_ROLES:
        raise ValueError(
            f"unknown branch edge_role {edge_role!r}; "
            f"expected one of {sorted(_BRANCH_ROLES)}"
        )

    parent_name = parent_spec.get("name")

    child: dict = {
        "name": child_name,
        "status": "planned",
        "verdict": "unassessed",
        "parent_studies": [{"study": parent_name, "relationship": edge_role}],
    }

    if edge_role in ("builds_on", "leads-to"):
        # Results carried forward as inputs; model left for the author.
        child["builds_on"] = [parent_name]
        child["inputs_from"] = f"results of {parent_name}"
        child["arms"] = []
        child["runs"] = []
        child["findings"] = []
        child["visualizations"] = []

    elif edge_role == "tests":
        # Re-test: copy the model verbatim, clear results, reset the gate.
        for field in _MODEL_FIELDS:
            if field in parent_spec:
                child[field] = copy.deepcopy(parent_spec[field])
        child["runs"] = []
        child["findings"] = []

    elif edge_role == "alternative_to":
        # Competing model for the same question: carry only the question
        # (and hypothesis if present); everything else fresh.
        if "question" in parent_spec:
            child["question"] = copy.deepcopy(parent_spec["question"])
        if "hypothesis" in parent_spec:
            child["hypothesis"] = copy.deepcopy(parent_spec["hypothesis"])

    if child_overrides:
        child.update(child_overrides)

    return child


# ---------------------------------------------------------------------------
# link
# ---------------------------------------------------------------------------

def link(inv_spec: dict, a: str, b: str, edge_type: str) -> dict:
    """Return a NEW inv_spec with a scientific edge ``a -> b`` added.

    Edges live in an investigation-level ``edges`` list of
    ``{"from": a, "to": b, "type": edge_type}``. The op is idempotent — adding
    the same (from, to, type) triple twice does not duplicate it — creates no
    node, and never mutates ``inv_spec``.
    """
    if edge_type not in _EDGE_VOCAB:
        raise ValueError(
            f"unknown edge_type {edge_type!r}; expected one of {sorted(_EDGE_VOCAB)}"
        )

    out = dict(inv_spec)
    edges = [dict(e) for e in (inv_spec.get("edges") or [])]
    new_edge = {"from": a, "to": b, "type": edge_type}
    if new_edge not in edges:
        edges.append(new_edge)
    out["edges"] = edges
    return out
