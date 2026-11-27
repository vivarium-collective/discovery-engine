"""Canonical relation registry for the investigation graph.

One source of truth for the graph's edge vocabulary. Today the same scientific
relation is spelled several ways across the codebase, and the spellings are
*disjoint string sets*:

- ``investigation/ops.py`` ``_EDGE_VOCAB`` uses **underscore** forms
  (``builds_on``, ``alternative_to``) plus ``leads-to`` / ``consolidated_from``.
- ``projection/relations.py`` ``_EPISTEMIC`` uses **hyphen** forms
  (``builds-on``, ``alternative-to``) plus ``refutes``.
- ``projection/argument.py`` ``_edge_type`` reads the hyphen forms and maps them
  to Claim->Claim edge types (``support/challenge/prerequisite/synthesis``).
- the study schema ``parent_studies`` / ``parent_relations`` / ``pipeline_gate``
  enums use the hyphen forms, with legacy aliases (``leads-to``, ``model-input``,
  ``precedes`` ...) "alias-mapped by the dashboard but [to] be migrated".

That fragmentation gets worse with every new study. This module is **additive and
read-only**: it changes no existing behavior. It gives every writer (and a CI
lint) a single vocabulary to normalize against, so a ``builds_on`` edge and a
``builds-on`` edge are recognized as the same scientific relation and no new
dialect creeps in unnoticed.

It also keeps the three edge *axes* explicitly apart — only the execution gate is
required to be acyclic for scheduling; the scientific and provenance edges are not:

- ``E_sci``  — scientific Study<->Study relations (the set below).
- ``E_org``  — organizational membership + the execution-prerequisite gate
  (``condition: tests-passed | ran | complete``), a different axis from the
  relation *type*.
- ``E_prov`` — provenance / consolidation (``consolidated_from``, ``generated_by`` ...).
"""
from __future__ import annotations

from typing import Iterable

#: The canonical scientific (E_sci) Study<->Study relations. Hyphen-canonical, to
#: match the serialized schema form and the argument/relations projections. This is
#: the union of ``relations._EPISTEMIC`` with the schema's ``parent_studies`` enum.
CANONICAL_SCI: tuple[str, ...] = (
    "builds-on",
    "tests",
    "refutes",
    "motivates",
    "alternative-to",
)

#: Every non-canonical surface form seen in the code or schema -> its canonical
#: E_sci relation. Covers the underscore engine forms and the legacy study-relation
#: values the schema says should be migrated.
_SCI_ALIASES: dict[str, str] = {
    # engine underscore forms (ops.py)
    "builds_on": "builds-on",
    "alternative_to": "alternative-to",
    # "child carries the parent's results as inputs" — same role as builds-on
    "leads-to": "builds-on",
    "leads_to": "builds-on",
    # legacy study-relation values (schema: alias-mapped by the dashboard)
    "model-input": "builds-on",
    "model_input": "builds-on",
    "calibrates-threshold": "builds-on",
    "precedes": "builds-on",
    "evidence": "tests",
    # near-synonyms of refutes
    "refute": "refutes",
    "refuted": "refutes",
    "challenges": "refutes",
    "challenge": "refutes",
    "disconfirms": "refutes",
}

#: Recognized relations that are deliberately NOT scientific Study<->Study edges,
#: mapped to the family they belong to. Kept out of ``CANONICAL_SCI`` so they are
#: never silently treated as a claim-bearing scientific edge.
_NON_SCI_FAMILY: dict[str, str] = {
    # consolidation / provenance
    "consolidated_from": "E_prov",
    "derived_from": "E_prov",
    "generated_by": "E_prov",
    "used": "E_prov",
    "supersedes": "E_prov",
    "attributed_to": "E_prov",
    # organizational membership
    "member_of": "E_org",
    "included_in": "E_org",
    "part_of": "E_org",
    # execution-prerequisite GATE conditions (a different axis from relation TYPE;
    # only this graph must stay acyclic for scheduling)
    "tests-passed": "E_org",
    "ran": "E_org",
    "complete": "E_org",
}

#: Claim->Claim argument edge types (projection/argument.py).
ARGUMENT_EDGE_TYPES: tuple[str, ...] = ("support", "challenge", "prerequisite", "synthesis")

_CANONICAL_SET = frozenset(CANONICAL_SCI)


def _key(relation: str | None) -> str:
    return (relation or "").strip().lower()


def normalize(relation: str | None) -> str | None:
    """Return the canonical E_sci relation for ``relation``, or ``None`` if it is
    not a scientific Study<->Study relation (unknown, or a non-E_sci family)."""
    k = _key(relation)
    if k in _CANONICAL_SET:
        return k
    return _SCI_ALIASES.get(k)


def is_canonical(relation: str | None) -> bool:
    """True iff ``relation`` is already a canonical E_sci relation (no aliasing)."""
    return _key(relation) in _CANONICAL_SET


def family(relation: str | None) -> str | None:
    """The edge family of ``relation`` — ``"E_sci"``, ``"E_org"``, ``"E_prov"`` —
    or ``None`` if the relation is not recognized at all."""
    k = _key(relation)
    if k in _CANONICAL_SET or k in _SCI_ALIASES:
        return "E_sci"
    return _NON_SCI_FAMILY.get(k)


def known(relation: str | None) -> bool:
    """True iff ``relation`` is recognized in any family (so a CI lint can flag a
    genuinely new/unknown dialect)."""
    return family(relation) is not None


def unknown_relations(relations: Iterable[str | None]) -> list[str]:
    """The distinct, recognized-as-unknown surface forms in ``relations`` — the
    lint payload. Empty strings / ``None`` are ignored (an absent relation defaults
    to ``builds-on`` elsewhere and is not a dialect)."""
    seen: dict[str, None] = {}
    for r in relations:
        k = _key(r)
        if k and not known(k):
            seen.setdefault(r if isinstance(r, str) else k, None)
    return list(seen)


def argument_edge_type(relation: str | None, child_verdict: str | None) -> str | None:
    """The Claim->Claim argument edge type for a (relation, child verdict) pair —
    the centralized form of ``projection/argument.py:_edge_type``, but normalizing
    the relation first so underscore and legacy forms map the same as the canonical
    ones. Returns ``None`` for relations that carry no argument edge (``motivates``)."""
    canon = normalize(relation)
    if canon == "builds-on":
        return "prerequisite"
    if canon in ("refutes", "alternative-to"):
        return "challenge"
    if canon == "tests":
        return "support" if child_verdict == "supported" else "challenge"
    return None
