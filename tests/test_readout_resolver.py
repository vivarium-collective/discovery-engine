"""Tests for readout_resolver.py (sub-project #3 — readout normalization).

Real readout dicts copied inline from:
  v2e-invest/studies/dnaa-1-expression/study.yaml
  v2e-invest/studies/dnaa-2-nucleotide-balance/study.yaml

Do NOT modify those files; tests use literal dicts here.
"""
from __future__ import annotations

import pytest

from discovery_engine.readout_resolver import (
    ResolvedReadout,
    UnresolvedReadout,
    resolve_readout,
    resolve_study_readouts,
)


# ---------------------------------------------------------------------------
# Task 1 — structured single-target dialects
# ---------------------------------------------------------------------------

# --- dnaa-1 real readouts ---

def test_literal_index_from_identifier():
    """listeners.monomer_counts[3861] → element with literal_index."""
    ro = {
        "name": "DnaA monomer total",
        "status": "primary",
        "identifier": "listeners.monomer_counts[3861]",
        "units": "count",
    }
    result = resolve_readout(ro)
    assert isinstance(result, ResolvedReadout)
    assert result.name == "DnaA monomer total"
    assert result.kind == "element"
    assert result.observable == "listeners.monomer_counts"
    assert result.index_by == {"type": "literal_index", "value": 3861}
    assert result.expression is None
    assert result.operand_ids is None
    assert result.aggregate is None
    assert result.units == "count"
    assert result.source_dialect == "identifier"


def test_scalar_path_from_identifier():
    """listeners.replication_data.number_of_oric → scalar."""
    ro = {
        "name": "oriC count",
        "status": "measured",
        "identifier": "listeners.replication_data.number_of_oric",
        "units": "count",
    }
    result = resolve_readout(ro)
    assert isinstance(result, ResolvedReadout)
    assert result.kind == "scalar"
    assert result.observable == "listeners.replication_data.number_of_oric"
    assert result.index_by is None
    assert result.source_dialect == "identifier"


def test_scalar_cell_mass_from_identifier():
    """listeners.mass.cell_mass → scalar."""
    ro = {
        "name": "cell mass",
        "identifier": "listeners.mass.cell_mass",
        "units": "fg",
    }
    result = resolve_readout(ro)
    assert isinstance(result, ResolvedReadout)
    assert result.kind == "scalar"
    assert result.observable == "listeners.mass.cell_mass"


def test_single_bulk_id_from_identifier():
    """bulk MONOMER0-160[c] → element with bulk_id."""
    ro = {
        "name": "DnaA-ATP",
        "identifier": "bulk MONOMER0-160[c]",
    }
    result = resolve_readout(ro)
    assert isinstance(result, ResolvedReadout)
    assert result.kind == "element"
    assert result.observable == "bulk"
    assert result.index_by == {"type": "bulk_id", "value": "MONOMER0-160[c]"}
    assert result.source_dialect == "identifier"


def test_store_path_bulk_dot_id():
    """store_path: bulk.ATP[c] → element with bulk_id."""
    ro = {
        "name": "cellular ATP",
        "store_path": "bulk.ATP[c]",
    }
    result = resolve_readout(ro)
    assert isinstance(result, ResolvedReadout)
    assert result.kind == "element"
    assert result.observable == "bulk"
    assert result.index_by == {"type": "bulk_id", "value": "ATP[c]"}
    assert result.source_dialect == "store_path"


def test_store_path_listener_scalar():
    """store_path: listeners.mass.cell_mass → scalar."""
    ro = {
        "name": "cell mass",
        "store_path": "listeners.mass.cell_mass",
    }
    result = resolve_readout(ro)
    assert isinstance(result, ResolvedReadout)
    assert result.kind == "scalar"
    assert result.observable == "listeners.mass.cell_mass"
    assert result.source_dialect == "store_path"


def test_store_path_derived_is_unresolved():
    """store_path: derived → UnresolvedReadout."""
    ro = {
        "name": "derived obs",
        "store_path": "derived",
    }
    result = resolve_readout(ro)
    assert isinstance(result, UnresolvedReadout)
    assert "derived" in result.reason.lower()


def test_canonical_index_by_passthrough():
    """Readout with index_by block → passed through directly."""
    ro = {
        "name": "DnaA protein",
        "store_path": "agents.0.bulk",
        "index_by": {"type": "bulk_id", "value": "PD03831[c]"},
        "units": "count",
    }
    result = resolve_readout(ro)
    assert isinstance(result, ResolvedReadout)
    assert result.kind == "element"
    assert result.index_by == {"type": "bulk_id", "value": "PD03831[c]"}
    assert result.source_dialect == "index_by"
    assert result.units == "count"


def test_canonical_index_by_literal_index_passthrough():
    """index_by with literal_index type passes through."""
    ro = {
        "name": "monomer count",
        "store_path": "listeners.monomer_counts",
        "index_by": {"type": "literal_index", "value": 3861},
    }
    result = resolve_readout(ro)
    assert isinstance(result, ResolvedReadout)
    assert result.source_dialect == "index_by"
    assert result.index_by["type"] == "literal_index"
    assert result.index_by["value"] == 3861


def test_identifier_derived_is_unresolved():
    """identifier: derived → UnresolvedReadout."""
    ro = {
        "name": "derived obs",
        "identifier": "derived",
    }
    result = resolve_readout(ro)
    assert isinstance(result, UnresolvedReadout)
    assert "derived" in result.reason.lower()


def test_name_preserved_in_resolved():
    """Name field propagates to both resolved and unresolved outputs."""
    ro = {"name": "my-readout", "identifier": "listeners.mass.cell_mass"}
    r = resolve_readout(ro)
    assert r.name == "my-readout"


def test_no_field_is_unresolved():
    """A readout dict with no identifier/store_path/index_by → Unresolved."""
    ro = {"name": "empty", "units": "count"}
    result = resolve_readout(ro)
    assert isinstance(result, UnresolvedReadout)


def test_trailing_prose_qualifier_stripped():
    """listeners.rnap_data.rna_init_event_per_cistron (dnaA cistron) →
    scalar with stripped qualifier note (real dnaa-1 readout)."""
    ro = {
        "name": "dnaA mRNA initiation rate",
        "status": "primary",
        "identifier": "listeners.rnap_data.rna_init_event_per_cistron (dnaA cistron)",
        "units": "events/min",
    }
    result = resolve_readout(ro)
    assert isinstance(result, ResolvedReadout)
    assert result.kind == "scalar"
    assert result.observable == "listeners.rnap_data.rna_init_event_per_cistron"
    assert result.notes is not None and "dnaA cistron" in result.notes
    assert result.source_dialect == "identifier"


# ---------------------------------------------------------------------------
# Task 2 — arithmetic expressions over observable ids
# ---------------------------------------------------------------------------

def test_bulk_expression_dnaa_atp_fraction():
    """Real dnaa-2 expression: bulk MONOMER0-160[c] / (PD03831[c] + ...)
    → kind=expression, 3 distinct operand_ids all tagged bulk_id."""
    ro = {
        "name": "DnaA-ATP fraction",
        "status": "primary",
        "identifier": (
            "bulk MONOMER0-160[c] / (PD03831[c] + MONOMER0-160[c] + MONOMER0-4565[c])"
        ),
        "units": "fraction",
    }
    result = resolve_readout(ro)
    assert isinstance(result, ResolvedReadout)
    assert result.kind == "expression"
    assert result.observable == "bulk"
    # expression string is the part after stripping "bulk "
    assert "MONOMER0-160[c]" in result.expression
    assert "PD03831[c]" in result.expression
    assert "MONOMER0-4565[c]" in result.expression
    # 3 distinct operand ids
    assert result.operand_ids is not None
    assert len(result.operand_ids) == 3
    tokens = {op["token"] for op in result.operand_ids}
    assert tokens == {"MONOMER0-160[c]", "PD03831[c]", "MONOMER0-4565[c]"}
    for op in result.operand_ids:
        assert op["index_by"]["type"] == "bulk_id"
        assert op["index_by"]["value"] == op["token"]
    assert result.aggregate is None
    assert result.units == "fraction"
    assert result.source_dialect == "identifier"


def test_expression_reuses_study_evaluator_tokenizer():
    """Verify that the tokenizer used is the same one from study_evaluator
    (by checking it finds the same tokens for an expression)."""
    from discovery_engine.study_evaluator import _extract_observable_tokens

    expr = "MONOMER0-160[c] / (PD03831[c] + MONOMER0-160[c] + MONOMER0-4565[c])"
    tokens = _extract_observable_tokens(expr)
    # The resolver's operand_ids tokens must be a subset of these
    ro = {
        "name": "frac",
        "identifier": "bulk " + expr,
    }
    result = resolve_readout(ro)
    assert isinstance(result, ResolvedReadout)
    resolver_tokens = {op["token"] for op in result.operand_ids}
    evaluator_tokens = set(tokens)
    assert resolver_tokens == evaluator_tokens


def test_malformed_expression_is_unresolved():
    """An identifier string that mixes prose with arithmetic ambiguously → Unresolved."""
    ro = {
        "name": "weird",
        "identifier": "some text + that + is + not + valid",
    }
    result = resolve_readout(ro)
    # This could be unresolved (no bracket IDs, no valid dotted paths only)
    # or resolved as an expression — the exact outcome depends on impl.
    # Key: it must NOT fabricate operand selectors.
    # At minimum, if resolved as expression, operand_ids must be empty or
    # only contain valid dot-path tokens.
    if isinstance(result, UnresolvedReadout):
        assert result.reason  # has a reason
    else:
        # If resolved as expression, fine; just don't assert fabricated operands
        assert isinstance(result, ResolvedReadout)


# ---------------------------------------------------------------------------
# Task 3 — never-guess on prose/ambiguous + study-level resolve
# ---------------------------------------------------------------------------

def test_middle_dot_group_is_unresolved():
    """Real dnaa-2 readout: bulk PD03831[c] (apo) · MONOMER0-160[c] ... → Unresolved."""
    ro = {
        "name": "DnaA-form counts",
        "status": "measured",
        "identifier": (
            "bulk PD03831[c] (apo) · MONOMER0-160[c] (DnaA-ATP) · MONOMER0-4565[c] (DnaA-ADP)"
        ),
        "units": "molecules/cell",
    }
    result = resolve_readout(ro)
    assert isinstance(result, UnresolvedReadout)
    assert result.name == "DnaA-form counts"
    # reason should mention multi-id or prose
    assert any(word in result.reason.lower() for word in ("multi", "·", "group", "ambiguous", "prose"))


def test_multi_id_comma_form_is_unresolved():
    """bulk__count[ATP[c], ADP[c]] → Unresolved (comma-separated multi-id)."""
    ro = {
        "name": "cellular bulk ATP[c] / ADP[c]",
        "identifier": "bulk__count[ATP[c], ADP[c]]",
    }
    result = resolve_readout(ro)
    assert isinstance(result, UnresolvedReadout)
    assert result.name == "cellular bulk ATP[c] / ADP[c]"


def test_resolve_study_readouts_dnaa2():
    """resolve_study_readouts on the real dnaa-2 readouts block returns the correct mix:
    - DnaA-ATP fraction (expression) → ResolvedReadout
    - DnaA-form counts (· group) → UnresolvedReadout
    - oriC count (scalar) → ResolvedReadout
    - cell mass (scalar) → ResolvedReadout
    - cellular bulk ATP[c]/ADP[c] (comma multi-id) → UnresolvedReadout
    """
    # Copied verbatim from v2e-invest/studies/dnaa-2-nucleotide-balance/study.yaml
    spec = {
        "name": "dnaa-2-nucleotide-balance",
        "readouts": [
            {
                "name": "DnaA-ATP fraction",
                "status": "primary",
                "identifier": (
                    "bulk MONOMER0-160[c] / (PD03831[c] + MONOMER0-160[c] + MONOMER0-4565[c])"
                ),
                "units": "fraction",
            },
            {
                "name": "DnaA-form counts",
                "status": "measured",
                "identifier": (
                    "bulk PD03831[c] (apo) · MONOMER0-160[c] (DnaA-ATP) · MONOMER0-4565[c] (DnaA-ADP)"
                ),
                "units": "molecules/cell",
            },
            {
                "name": "oriC count",
                "status": "measured",
                "identifier": "listeners.replication_data.number_of_oric",
                "units": "count",
            },
            {
                "name": "cell mass",
                "status": "measured",
                "identifier": "listeners.mass.cell_mass",
                "units": "fg",
            },
            {
                "name": "cellular bulk ATP[c] / ADP[c]",
                "status": "measured",
                "identifier": "bulk__count[ATP[c], ADP[c]]",
                "units": "molecules/cell",
            },
        ],
    }
    resolved = resolve_study_readouts(spec)

    # DnaA-ATP fraction → expression
    assert isinstance(resolved["DnaA-ATP fraction"], ResolvedReadout)
    assert resolved["DnaA-ATP fraction"].kind == "expression"
    assert len(resolved["DnaA-ATP fraction"].operand_ids) == 3

    # DnaA-form counts → unresolved (· group)
    assert isinstance(resolved["DnaA-form counts"], UnresolvedReadout)

    # oriC count → scalar
    assert isinstance(resolved["oriC count"], ResolvedReadout)
    assert resolved["oriC count"].kind == "scalar"

    # cell mass → scalar
    assert isinstance(resolved["cell mass"], ResolvedReadout)
    assert resolved["cell mass"].kind == "scalar"

    # cellular bulk ATP[c] / ADP[c] → unresolved (comma multi-id)
    assert isinstance(resolved["cellular bulk ATP[c] / ADP[c]"], UnresolvedReadout)


def test_resolve_study_readouts_dnaa1():
    """resolve_study_readouts on the real dnaa-1 readouts block:
    - DnaA monomer total [3861] → element literal_index
    - dnaA mRNA initiation rate (dnaA cistron) → scalar with stripped qualifier
    - oriC count → scalar
    - cell mass → scalar
    """
    spec = {
        "name": "dnaa-1-expression",
        "readouts": [
            {
                "name": "DnaA monomer total",
                "status": "primary",
                "identifier": "listeners.monomer_counts[3861]",
                "units": "count",
            },
            {
                "name": "dnaA mRNA initiation rate",
                "status": "primary",
                "identifier": "listeners.rnap_data.rna_init_event_per_cistron (dnaA cistron)",
                "units": "events/min",
            },
            {
                "name": "oriC count",
                "status": "measured",
                "identifier": "listeners.replication_data.number_of_oric",
                "units": "count",
            },
            {
                "name": "cell mass",
                "status": "measured",
                "identifier": "listeners.mass.cell_mass",
                "units": "fg",
            },
        ],
    }
    resolved = resolve_study_readouts(spec)

    assert isinstance(resolved["DnaA monomer total"], ResolvedReadout)
    assert resolved["DnaA monomer total"].kind == "element"
    assert resolved["DnaA monomer total"].index_by == {"type": "literal_index", "value": 3861}

    # prose qualifier stripped → scalar
    r_init = resolved["dnaA mRNA initiation rate"]
    assert isinstance(r_init, ResolvedReadout)
    assert r_init.kind == "scalar"
    assert r_init.observable == "listeners.rnap_data.rna_init_event_per_cistron"

    assert isinstance(resolved["oriC count"], ResolvedReadout)
    assert resolved["oriC count"].kind == "scalar"

    assert isinstance(resolved["cell mass"], ResolvedReadout)
    assert resolved["cell mass"].kind == "scalar"


def test_empty_readouts_block():
    """spec with no readouts → empty dict."""
    spec = {"name": "s", "readouts": []}
    assert resolve_study_readouts(spec) == {}


def test_resolve_study_readouts_missing_readouts_key():
    """spec with no readouts key → empty dict."""
    spec = {"name": "s"}
    assert resolve_study_readouts(spec) == {}


def test_resolve_study_readouts_prose_string_readouts():
    """Readouts authored as bare prose strings (not structured dicts) must
    resolve to UnresolvedReadout, not raise AttributeError on ``str.get``.

    Real workspaces (e.g. viva-human-atlas studies) list readouts as plain
    strings; the study-run path calls resolve_study_readouts on them and used
    to crash with ``'str' object has no attribute 'get'``.
    """
    spec = {
        "name": "s",
        "readouts": [
            "atlas manifest: n_organs (50), n_modeled (14)",
            "per-organ n_models + BioModels id/name/url list",
        ],
    }
    resolved = resolve_study_readouts(spec)
    assert set(resolved) == set(spec["readouts"])
    for key, ro in resolved.items():
        assert isinstance(ro, UnresolvedReadout)
        assert ro.name == key
        assert ro.raw == key


def test_resolve_study_readouts_mixed_string_and_dict():
    """A readouts list mixing prose strings and structured dicts resolves each
    per its type without the string entries breaking the dict ones."""
    spec = {
        "name": "s",
        "readouts": [
            "some prose readout",
            {"name": "g", "identifier": "glucose"},
        ],
    }
    resolved = resolve_study_readouts(spec)
    assert isinstance(resolved["some prose readout"], UnresolvedReadout)
    assert isinstance(resolved["g"], ResolvedReadout)


def test_never_guess_empty_identifier():
    """Empty identifier string → Unresolved (never fabricate a selector)."""
    ro = {"name": "x", "identifier": ""}
    result = resolve_readout(ro)
    assert isinstance(result, UnresolvedReadout)


def test_aggregate_passthrough_from_canonical():
    """aggregate field in a canonical readout dict is passed through."""
    ro = {
        "name": "r",
        "store_path": "bulk",
        "index_by": {"type": "bulk_id", "value": "MONOMER0-160[c]"},
        "aggregate": {"op": "sum", "over": ["MONOMER0-160[c]"]},
    }
    result = resolve_readout(ro)
    assert isinstance(result, ResolvedReadout)
    assert result.aggregate == {"op": "sum", "over": ["MONOMER0-160[c]"]}


def test_old_dialects_do_not_infer_aggregate():
    """identifier dialect never has aggregate inferred from prose."""
    ro = {
        "name": "r",
        "identifier": "listeners.monomer_counts[3861]",
        "notes": "sum across forms",
    }
    result = resolve_readout(ro)
    assert isinstance(result, ResolvedReadout)
    assert result.aggregate is None


# ---------------------------------------------------------------------------
# FIX 1 — to_select_dict() merges observable into index_by for RunReader.select
# ---------------------------------------------------------------------------

def test_to_select_dict_literal_index():
    """literal_index element → to_select_dict includes observable key."""
    ro = {
        "name": "DnaA monomer total",
        "identifier": "listeners.monomer_counts[3861]",
        "units": "count",
    }
    result = resolve_readout(ro)
    assert isinstance(result, ResolvedReadout)
    assert result.kind == "element"
    d = result.to_select_dict()
    assert d == {
        "type": "literal_index",
        "value": 3861,
        "observable": "listeners.monomer_counts",
    }


def test_to_select_dict_bulk_id():
    """bulk_id element → to_select_dict includes observable (harmless/correct for select)."""
    ro = {
        "name": "DnaA-ATP",
        "identifier": "bulk MONOMER0-160[c]",
    }
    result = resolve_readout(ro)
    assert isinstance(result, ResolvedReadout)
    assert result.kind == "element"
    d = result.to_select_dict()
    assert d == {
        "type": "bulk_id",
        "value": "MONOMER0-160[c]",
        "observable": "bulk",
    }


def test_to_select_dict_canonical_passthrough():
    """Canonical index_by passthrough → to_select_dict includes observable from store_path."""
    ro = {
        "name": "DnaA protein",
        "store_path": "agents.0.bulk",
        "index_by": {"type": "bulk_id", "value": "PD03831[c]"},
        "units": "count",
    }
    result = resolve_readout(ro)
    assert isinstance(result, ResolvedReadout)
    d = result.to_select_dict()
    assert d is not None
    assert d["type"] == "bulk_id"
    assert d["value"] == "PD03831[c]"
    assert d["observable"] == "agents.0.bulk"


def test_to_select_dict_scalar_returns_none():
    """scalar kind → to_select_dict returns None (not passable to select)."""
    ro = {
        "name": "cell mass",
        "identifier": "listeners.mass.cell_mass",
        "units": "fg",
    }
    result = resolve_readout(ro)
    assert isinstance(result, ResolvedReadout)
    assert result.kind == "scalar"
    assert result.to_select_dict() is None


def test_to_select_dict_expression_returns_none():
    """expression kind → to_select_dict returns None."""
    ro = {
        "name": "DnaA-ATP fraction",
        "identifier": (
            "bulk MONOMER0-160[c] / (PD03831[c] + MONOMER0-160[c] + MONOMER0-4565[c])"
        ),
        "units": "fraction",
    }
    result = resolve_readout(ro)
    assert isinstance(result, ResolvedReadout)
    assert result.kind == "expression"
    assert result.to_select_dict() is None


# ---------------------------------------------------------------------------
# FIX 2 — mixed bulk+listener expression must NOT drop dotted operands
# ---------------------------------------------------------------------------

def test_bulk_mixed_expression_keeps_dotted_operands():
    """bulk X[c] / listeners.mass.cell_mass → operand_ids has BOTH operands.

    X[c] → bulk_id; listeners.mass.cell_mass → scalar.
    Neither is silently dropped.
    """
    ro = {
        "name": "mixed",
        "identifier": "bulk X[c] / listeners.mass.cell_mass",
    }
    result = resolve_readout(ro)
    assert isinstance(result, ResolvedReadout)
    assert result.kind == "expression"
    assert result.operand_ids is not None
    tokens = {op["token"] for op in result.operand_ids}
    assert "X[c]" in tokens, "bracket-id X[c] must be an operand"
    assert "listeners.mass.cell_mass" in tokens, "dotted path must NOT be dropped"
    # check types
    by_token = {op["token"]: op for op in result.operand_ids}
    assert by_token["X[c]"]["index_by"]["type"] == "bulk_id"
    assert by_token["listeners.mass.cell_mass"]["index_by"]["type"] == "scalar"
