"""Budget meter + typed stop reasons (bounded autonomous runs)."""
import pytest

from discovery_engine.discover._policy import (
    BUDGET_EXHAUSTED,
    NO_INFORMATIVE_EXPERIMENT,
    STOP_REASONS,
    stop,
)
from discovery_engine.discover.budget import GENERATION_CALLS, Budget


def test_charge_decrements_and_exhausts():
    b = Budget(generation_calls=2)
    assert b.can(GENERATION_CALLS)
    assert b.charge(GENERATION_CALLS) is True
    assert b.remaining(GENERATION_CALLS) == 1
    assert b.charge(GENERATION_CALLS) is True
    assert b.remaining(GENERATION_CALLS) == 0
    assert b.can(GENERATION_CALLS) is False
    assert b.charge(GENERATION_CALLS) is False      # over cap -> spends nothing
    assert b.spent[GENERATION_CALLS] == 2


def test_none_cap_is_unbounded():
    b = Budget()                                    # all caps None
    assert b.remaining(GENERATION_CALLS) is None
    assert all(b.charge(GENERATION_CALLS) for _ in range(100))


def test_charge_rejects_overspend_in_one_call():
    b = Budget(model_builds=1)
    assert b.charge("model_builds", 2) is False     # would exceed -> no partial spend
    assert b.charge("model_builds", 1) is True


def test_from_manifest_and_unknown_kind():
    b = Budget.from_manifest({"simulation_runs": 5, "generation_calls": 3})
    assert b.simulation_runs == 5 and b.generation_calls == 3 and b.model_builds is None
    with pytest.raises(ValueError):
        b.can("teleport")


def test_stop_is_typed_and_rejects_unknown():
    s = stop(NO_INFORMATIVE_EXPERIMENT, detail="no discriminating intervention")
    assert s["outcome"] == "stop" and s["reason"] == NO_INFORMATIVE_EXPERIMENT
    assert BUDGET_EXHAUSTED in STOP_REASONS
    with pytest.raises(ValueError):
        stop("because")
