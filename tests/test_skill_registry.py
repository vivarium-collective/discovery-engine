import pytest

from discovery_engine.agents.skill_registry import default_registry


def test_registry_routes_hypothesize_to_its_validator_and_recorder():
    sk = default_registry().get("hypothesize")
    assert sk is not None
    assert sk.proposal_type == "HypothesisProposal"
    assert sk.skill_md.endswith("skills/hypothesize/SKILL.md")
    assert sk.consequence == "authoring"
    # coerce is the real honesty gate
    with pytest.raises(ValueError):
        sk.coerce({"statement": "x"})  # no mechanism/prediction -> rejected


def test_registry_routes_critique():
    sk = default_registry().get("critique")
    assert sk is not None and sk.proposal_type == "CritiqueReport"


def test_unknown_kind_returns_none():
    assert default_registry().get("not_a_kind") is None
