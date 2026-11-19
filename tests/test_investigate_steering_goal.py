"""`discover investigate` threads a free-text steering goal into every filler request."""
import yaml

from discovery_engine.benchmark.oracle import oracle_filler
from discovery_engine.benchmark.scenario import build_scenario
from discovery_engine.discover.budget import Budget
from discovery_engine.discover.investigate import investigate
from discovery_engine.phases.generation import GateConfig


def _scenario(tmp_path, name):
    (tmp_path / name).mkdir()
    return build_scenario(tmp_path / name, name=name)


def _spy(inner, seen):
    """Wrap a filler, recording each request's context['goal'] as it is filled."""
    def fill(request):
        seen.append((request.context or {}).get("goal"))
        return inner(request)
    return fill


def test_explicit_goal_reaches_every_filler_request(tmp_path):
    sc = _scenario(tmp_path, "goal1")
    seen: list = []
    summary = investigate(
        sc.ws_root, sc.inv_slug, fill=_spy(oracle_filler(sc.ground_truth), seen),
        budget=Budget(generation_calls=4), gates=GateConfig.preset("free_run"),
        max_iterations=5, goal="Make this a visual exposition of the baseline biology.")
    assert seen, "expected at least one generative act"
    assert all(g == "Make this a visual exposition of the baseline biology." for g in seen)
    assert summary["goal"] == "Make this a visual exposition of the baseline biology."


def test_durable_discovery_goal_is_used_when_no_explicit_goal(tmp_path):
    sc = _scenario(tmp_path, "goal2")
    # write a durable goal onto the investigation via the loader the engine uses
    from discovery_engine import discover
    inv_path, inv_spec, _ = discover.load_cycle_inputs(sc.ws_root, sc.inv_slug)
    inv_spec.setdefault("discovery", {})["goal"] = "Durable steering from the investigation."
    with open(inv_path, "w") as f:
        yaml.safe_dump(inv_spec, f, sort_keys=False)

    seen: list = []
    summary = investigate(
        sc.ws_root, sc.inv_slug, fill=_spy(oracle_filler(sc.ground_truth), seen),
        budget=Budget(generation_calls=4), gates=GateConfig.preset("free_run"), max_iterations=5)
    assert seen and all(g == "Durable steering from the investigation." for g in seen)
    assert summary["goal"] == "Durable steering from the investigation."


def test_no_goal_leaves_context_clean(tmp_path):
    sc = _scenario(tmp_path, "goal3")
    seen: list = []
    investigate(sc.ws_root, sc.inv_slug, fill=_spy(oracle_filler(sc.ground_truth), seen),
                budget=Budget(generation_calls=4), gates=GateConfig.preset("free_run"),
                max_iterations=5)
    assert seen and all(g is None for g in seen)


def test_goal_order_floats_relevant_requests_first():
    from types import SimpleNamespace

    from discovery_engine.discover.investigate import _goal_order
    reqs = [
        SimpleNamespace(kind="resolve_decision", target="d1", rationale="", produces="decision/v1"),
        SimpleNamespace(kind="visualize", target="fig:showcase-7/panel", rationale="visual detail",
                        produces="viz_spec/v1"),
        SimpleNamespace(kind="author_tests", target="s3", rationale="", produces="study.yaml/v4"),
    ]
    goal = "Make this a visual exposition showcasing the model in great detail."
    ordered = _goal_order(reqs, goal)
    assert ordered[0].kind == "visualize"              # most goal-relevant floats first
    # no goal -> unchanged order (same objects, same order)
    assert _goal_order(reqs, None) == reqs
