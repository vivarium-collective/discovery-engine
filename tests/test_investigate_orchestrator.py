"""`discover investigate`: a bounded loop over the existing verbs, budgeted + logged."""
import yaml

from discovery_engine.benchmark.oracle import oracle_filler
from discovery_engine.benchmark.scenario import build_scenario
from discovery_engine.discover.budget import Budget
from discovery_engine.discover.investigate import investigate
from discovery_engine.phases.generation import GateConfig


def _scenario(tmp_path, name):
    (tmp_path / name).mkdir()
    return build_scenario(tmp_path / name, name=name)


def test_investigate_is_bounded_and_logs_every_decision(tmp_path):
    sc = _scenario(tmp_path, "inv1")
    budget = Budget(generation_calls=4)
    summary = investigate(sc.ws_root, sc.inv_slug, fill=oracle_filler(sc.ground_truth),
                          budget=budget, gates=GateConfig.preset("free_run"), max_iterations=5)

    assert summary["stop_reason"] in {
        "closed", "budget_exhausted", "no_informative_experiment",
        "insufficient_evidence", "max_iterations"}
    assert summary["decisions"] >= 1
    assert summary["iterations"] <= 5
    assert budget.spent.get("generation_calls", 0) <= 4

    # the decision log was persisted, one entry per decision
    inv = yaml.safe_load(open(summary["inv_path"]).read())
    assert len(inv.get("decision_log") or []) == summary["decisions"]
    assert all(e.get("actor") == "orchestrator" and e.get("policy_version")
               for e in inv["decision_log"])


def test_investigate_halts_on_generation_budget(tmp_path):
    sc = _scenario(tmp_path, "inv2")
    budget = Budget(generation_calls=1)          # the scenario emits >1 auto request
    summary = investigate(sc.ws_root, sc.inv_slug, fill=oracle_filler(sc.ground_truth),
                          budget=budget, gates=GateConfig.preset("free_run"), max_iterations=10)
    assert summary["stop_reason"] == "budget_exhausted"
    assert budget.spent["generation_calls"] == 1
    assert summary["applied"] <= 1               # at most one act ran before the budget halt
