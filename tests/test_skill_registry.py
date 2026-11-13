import pytest

from discovery_engine.agents.skill_registry import default_registry
from discovery_engine.discover.actions import (
    record_critique_report,
    record_hypothesis_proposal,
    record_model_acquisition_proposal,
)
from discovery_engine.phases.model_acquisition import coerce_model_acquisition_proposal
from discovery_engine.phases.critique_report import coerce_critique_report
from discovery_engine.phases.hypothesis_proposal import coerce_hypothesis_proposal


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
    assert sk.produces == "critique_report/v1"
    assert sk.skill_md.endswith("skills/critique/SKILL.md")
    assert sk.consequence == "authoring"


def test_registry_wires_each_kind_to_its_own_validator_and_recorder_by_identity():
    reg = default_registry()
    hyp, crit = reg.get("hypothesize"), reg.get("critique")
    assert hyp.coerce is coerce_hypothesis_proposal
    assert hyp.record is record_hypothesis_proposal
    assert crit.coerce is coerce_critique_report
    assert crit.record is record_critique_report


def test_unknown_kind_returns_none():
    assert default_registry().get("not_a_kind") is None


def test_registry_wires_acquire_model():
    sk = default_registry().get("acquire_model")
    assert sk is not None
    assert sk.proposal_type == "ModelAcquisitionProposal"
    assert sk.produces == "model_spec/v1"
    assert sk.consequence == "authoring"
    assert sk.skill_md.endswith("skills/acquire_model/SKILL.md")
    assert sk.coerce is coerce_model_acquisition_proposal
    assert sk.record is record_model_acquisition_proposal
