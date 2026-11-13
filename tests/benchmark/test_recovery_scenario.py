from discovery_engine import discover
from discovery_engine.benchmark.recovery_scenario import build_recovery_scenario
from discovery_engine.discover.cycle import run_readiness
from discovery_engine.feedback.capability_gaps import propose_capability_gaps
from discovery_engine.study.io import load_yaml


def _gaps(sc):
    _, inv, st = discover.load_cycle_inputs(sc.ws_root, sc.inv_slug)
    res = discover._cycle_with_evidence(sc.ws_root, sc.inv_slug, inv, st)
    return res, inv, st


def test_scenario_raises_one_capability_gap(tmp_path):
    sc = build_recovery_scenario(tmp_path)
    res, inv, st = _gaps(sc)
    gaps = [g for g in (res.get("capability_gaps") or [])]
    assert len(gaps) == 1, res.keys()
    assert gaps[0]["id"].startswith("cg:") and gaps[0]["mechanism"] == "feedback_inhibition"


def test_gap_trigger_study_not_ready(tmp_path):
    sc = build_recovery_scenario(tmp_path)
    _, inv, st = _gaps(sc)
    assert run_readiness(st[sc.study_slug], sc.ws_root)["ready"] is False
