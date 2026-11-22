"""R3 §3/§10 — explicit arm roles (never name-inferred) + a perturbation-search generator."""
from __future__ import annotations

from discovery_engine.phases.experiment_search import (
    ARM_ROLES,
    ExperimentSearchSpace,
    arm_role,
    enumerate_perturbations,
    is_control,
    validate_arm_roles,
)


# --- explicit arm roles: declared, never inferred from the name ---

def test_arm_role_reads_declaration_not_name():
    assert arm_role({"name": "arm1", "role": "negative_control"}) == "negative_control"
    # a control-sounding NAME with no declared role is NOT a control
    assert arm_role({"name": "no-exchange-control"}) is None
    assert not is_control({"name": "no-exchange-control"})
    assert is_control({"name": "x", "role": "sham"})
    assert arm_role({"name": "y", "role": "bogus"}) is None       # invalid role


def test_validate_arm_roles_requires_explicit_roles_and_a_control():
    # a name-only "control" arm fails: it declares no role
    issues = validate_arm_roles([{"name": "treatment", "role": "treatment"},
                                 {"name": "no-exchange"}])
    assert any("declares no valid role" in i for i in issues)
    assert any("no arm declares a control role" in i for i in issues)

    ok = validate_arm_roles([{"name": "t", "role": "treatment"},
                             {"name": "c", "role": "negative_control"}])
    assert ok == []


def test_validate_arm_roles_checks_required_roles():
    issues = validate_arm_roles(
        [{"name": "t", "role": "treatment"}, {"name": "n", "role": "negative_control"}],
        required_roles=["sham"])
    assert any("missing required arm role: sham" in i for i in issues)


def test_all_roles_are_explicit_vocabulary():
    assert "negative_control" in ARM_ROLES and "matched_baseline" in ARM_ROLES


# --- the perturbation-search generator (invention over the search space) ---

def test_enumerate_grid_over_values_and_range():
    space = ExperimentSearchSpace(
        interventions=[{"parameter": "glucose", "values": [0.1, 1.0]},
                       {"parameter": "oxygen", "range": [0.0, 1.0], "steps": 3}])
    pts = enumerate_perturbations(space)
    assert len(pts) == 2 * 3                                       # 2 glucose x 3 oxygen
    assert {"glucose": 0.1, "oxygen": 0.0} in pts
    assert {"glucose": 1.0, "oxygen": 1.0} in pts


def test_enumerate_respects_constraints():
    space = ExperimentSearchSpace(
        interventions=[{"parameter": "dose", "values": [0, 5, 10, 20]}],
        constraints=[{"parameter": "dose", "max": 10, "forbid": [5]}])
    pts = enumerate_perturbations(space)
    doses = sorted(p["dose"] for p in pts)
    assert doses == [0, 10]                                        # 5 forbidden, 20 over max


def test_enumerate_empty_space_yields_nothing():
    assert enumerate_perturbations(ExperimentSearchSpace(interventions=[])) == []
    assert enumerate_perturbations({"interventions": [{"parameter": "x"}]}) == []   # no values/range


def test_enumerate_accepts_plain_dict_and_limit():
    space = {"interventions": [{"parameter": "k", "range": [0, 10], "steps": 11}]}
    pts = enumerate_perturbations(space, limit=4)
    assert len(pts) == 4
