"""DE 2.0 gap-resolution taxonomy (discovery_engine.resolution).

The engine owns the canonical {acquire_data, develop_method, request_expert, spawn_research}
enum; workspace cards (e.g. sbml-cycle's recovery studies, PR #13) are conforming fixtures.
"""
from __future__ import annotations

import pytest

from discovery_engine import resolution as R
from discovery_engine.resolution import (
    ResolutionRouteError,
    normalize_resolution_route as norm,
    resolution_route_of,
    resolution_satisfied as satisfied,
)


def test_enum_is_the_four_atomic_routes():
    assert R.RESOLUTION_ROUTES == ("acquire_data", "develop_method", "request_expert", "spawn_research")
    assert R.RESOLUTION_MODES == ("all", "any")


# ── normalization (sugar → canonical) ────────────────────────────────────────
def test_bare_string_becomes_one_element_list_mode_all():
    assert norm("develop_method") == {"routes": ["develop_method"], "mode": "all"}


def test_plain_list_defaults_mode_all():
    assert norm(["acquire_data", "develop_method"]) == {"routes": ["acquire_data", "develop_method"], "mode": "all"}


def test_full_block_is_preserved_and_mode_always_present():
    assert norm({"routes": ["develop_method", "request_expert"], "mode": "any"}) == \
        {"routes": ["develop_method", "request_expert"], "mode": "any"}
    # mode omitted in input → defaults to all (canonical form always carries mode)
    assert norm({"routes": ["acquire_data"]}) == {"routes": ["acquire_data"], "mode": "all"}


def test_routes_are_deduplicated_in_first_seen_order():
    assert norm(["develop_method", "acquire_data", "develop_method"]) == \
        {"routes": ["develop_method", "acquire_data"], "mode": "all"}


@pytest.mark.parametrize("card,expected", [
    # sbml-cycle PR #13 fixtures — verbatim
    ("recover-enzyme-moiety-aware", {"routes": ["develop_method"], "mode": "all"}),
    ("recover-oscillator-data-richness", {"routes": ["acquire_data", "develop_method"], "mode": "all"}),
    ("recover-gene-switch-rational-laws", {"routes": ["develop_method", "request_expert"], "mode": "any"}),
])
def test_sbml_fixtures_are_canonical(card, expected):
    assert norm(expected) == expected          # already canonical, round-trips unchanged


# ── validation (never silently drop a mis-declared route) ─────────────────────
def test_unknown_route_is_rejected():
    with pytest.raises(ResolutionRouteError, match="unknown resolution route"):
        norm(["develop_method", "buy_a_cluster"])


def test_bad_mode_is_rejected():
    with pytest.raises(ResolutionRouteError, match="mode"):
        norm({"routes": ["acquire_data"], "mode": "most"})


def test_empty_routes_is_rejected():
    with pytest.raises(ResolutionRouteError, match="at least one route"):
        norm({"routes": [], "mode": "all"})
    with pytest.raises(ResolutionRouteError):
        norm([])


def test_extra_field_is_rejected():
    with pytest.raises(ResolutionRouteError, match="unknown resolution_route field"):
        norm({"routes": ["develop_method"], "mode": "all", "rationale": "nope"})


def test_non_mapping_scalar_is_rejected():
    with pytest.raises(ResolutionRouteError):
        norm(42)


def test_is_valid_never_raises():
    assert R.is_valid_resolution_route(["acquire_data"]) is True
    assert R.is_valid_resolution_route({"routes": ["x"]}) is False


# ── satisfaction semantics ───────────────────────────────────────────────────
def test_mode_all_needs_every_route():
    rr = {"routes": ["acquire_data", "develop_method"], "mode": "all"}
    assert satisfied(rr, ["acquire_data"]) is False
    assert satisfied(rr, ["acquire_data", "develop_method"]) is True


def test_mode_any_needs_one_route():
    rr = {"routes": ["develop_method", "request_expert"], "mode": "any"}
    assert satisfied(rr, []) is False
    assert satisfied(rr, ["request_expert"]) is True


def test_empty_resolution_route_is_unsatisfiable():
    assert satisfied(None, ["acquire_data"]) is False
    assert satisfied({}, ["acquire_data"]) is False


# ── reading off a record ─────────────────────────────────────────────────────
def test_resolution_route_of_reads_and_normalizes():
    assert resolution_route_of({"resolution_route": "develop_method"}) == \
        {"routes": ["develop_method"], "mode": "all"}
    assert resolution_route_of({"id": "cg:x"}) is None          # none declared
    assert resolution_route_of("not a dict") is None
    with pytest.raises(ResolutionRouteError):
        resolution_route_of({"resolution_route": {"routes": ["bogus"]}})


# ── capability gaps carry a default resolution_route (develop_method) ─────────
def test_capability_gap_carries_a_valid_default_resolution_route():
    from discovery_engine.capability_gaps import propose_capability_gaps
    catalog = [{"id": "Tool", "composite": "pkg.Tool", "capability": ["rate", "law"], "observables": []}]
    gaps = propose_capability_gaps({"name": "inv"}, {}, catalog,
                                   required_mechanisms=["totally-novel-mechanism"])
    assert gaps, "a novel mechanism with no catalog coverage should propose a gap"
    rr = gaps[0]["resolution_route"]
    assert rr == {"routes": ["develop_method"], "mode": "all"}
    assert norm(rr) == rr                                        # already canonical


def test_capability_gap_with_resolution_route_passes_the_extensions_schema():
    from discovery_engine.discover import _validate_extensions
    gap = {"id": "cg:inv:deadbeef", "status": "proposed", "mechanism": "m",
           "resolution_route": {"routes": ["acquire_data", "develop_method"], "mode": "all"}}
    _validate_extensions({"capability_gaps": [gap]})            # valid → no raise
    bad = {**gap, "resolution_route": {"routes": ["nope"], "mode": "all"}}
    import jsonschema
    with pytest.raises(jsonschema.ValidationError):
        _validate_extensions({"capability_gaps": [bad]})
