"""Task 10: the two-cycle viz-loop acceptance test (decision §4) -- Wave 2 exit gate."""
import os
import time

import yaml

from discovery_engine import discover as D
from discovery_engine import discovery_engine as de
from discovery_engine.investigation_import import investigations_dir, studies_dir
from discovery_engine.viz_freshness import read_meta, stamp_meta
from discovery_engine.viz_proposals import propose_visualizations

RUNS = [{"run_id": "r2", "completed_at": 100.0}]
OBS = "pop/survivors"
INV = {"slug": "inv", "studies": ["s5"]}


def _fixture(tmp_path, monkeypatch):
    ws = tmp_path
    inv = investigations_dir(ws) / "inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump(INV))
    sd = studies_dir(ws) / "s5"
    (sd / "viz").mkdir(parents=True)
    spec = {"name": "s5", "runs": [{"name": "r2"}],
            "behavior_tests": [{"name": "t-a", "measure": {"kind": "final", "path": OBS},
                                "pass_if": {"low": 1, "high": 2}}],
            "findings": [{"id": "f-a", "evidence": {"from_test": "t-a"}}],
            "visualizations": [
                {"name": "eigen", "rendered": "x"},                      # broken stub
                {"name": "old", "address": "local:p.Old"}]}              # stale
    (sd / "study.yaml").write_text(yaml.safe_dump(spec))
    old = sd / "viz" / "old.html"
    old.write_text("<html>old</html>")
    stamp_meta(old, source_run_id="r1", generation_id=None, rendered_at=5.0, command="x")
    monkeypatch.setattr(D, "_load_runs_by_study", lambda w, i, s: {k: list(RUNS) for k in s})
    return ws, sd


def _cycle_res(ws):
    _, inv, st = D.load_cycle_inputs(ws, "inv")
    return D._cycle_with_evidence(ws, "inv", inv, st), inv, st


def _viz_items(ws):
    res, inv, _ = _cycle_res(ws)
    return [i for i in de.frontier(res, inv) if i["kind"].startswith("viz_")]


def _persist(ws):
    inv_path, inv, st = D.load_cycle_inputs(ws, "inv")
    res = D._cycle_with_evidence(ws, "inv", inv, st)
    D.persist_cycle(inv_path, inv, res, studies=st, viz_state=D.viz_inventory(ws, "inv", st))
    return yaml.safe_load(inv_path.read_text()), res


def _disk(ws):
    return yaml.safe_load((investigations_dir(ws) / "inv" / "investigation.yaml").read_text())


def test_two_cycle_viz_loop(tmp_path, monkeypatch):
    ws, sd = _fixture(tmp_path, monkeypatch)

    # ---- Cycle 1: catch ------------------------------------------------------------------
    res1, inv, _ = _cycle_res(ws)
    kinds = sorted((i["kind"], i["target"]) for i in _viz_items(ws))
    assert kinds[0] == ("viz_broken", "fig:s5/eigen")
    assert ("viz_stale", "fig:s5/old") in kinds
    assert [k for k, _ in kinds].count("viz_proposed") == 1
    assert len(kinds) == 3
    blocking = [i for i in de.frontier(res1, inv) if i["kind"] in de._CLOSURE_BLOCKING
                and i["kind"].startswith("viz_")]
    assert blocking == []                                    # non-blocking
    closed_before = de.closure(res1, inv)
    disk1, _ = _persist(ws)
    assert len(disk1["viz_proposals"]) == 1
    vp = disk1["viz_proposals"][0]
    assert vp["status"] == "proposed" and vp["source"]["kind"] == "gate_uncovered"

    # ---- Author actions ------------------------------------------------------------------
    D.declare_viz(ws, "inv", "fig:s5/eigen", {"address": "local:p.Eigen"}, "restore the stub")
    D.accept_viz_proposal(ws, "inv", vp["id"], address="local:BandOverlayViz", rationale="covers the gate")
    after = {(i["kind"], i["target"]) for i in _viz_items(ws)}
    assert ("viz_broken", "fig:s5/eigen") not in after
    assert ("viz_missing", "fig:s5/eigen") in after
    assert ("viz_stale", "fig:s5/old") in after
    accepted = yaml.safe_load((sd / "study.yaml").read_text())["visualizations"][-1]
    assert accepted["origin"] == {"kind": "viz_proposal", "ref": vp["id"]}
    assert ("viz_missing", f"fig:s5/{accepted['name']}") in after

    # ---- Advance with a fake render hook -------------------------------------------------
    calls = []

    def hook(sdir, entry, latest):
        calls.append(entry["name"])
        out = f"viz/{entry['name']}.html"
        (sdir / out).write_text(f"<html>{entry['name']}</html>")
        stamp_meta(sdir / out, source_run_id=latest["run_id"], generation_id=None,
                   rendered_at=time.time(), command="fake")
        return {"ok": True, "chart": out, "source_run_id": latest["run_id"], "detail": "rendered"}

    out = D.advance_cycle(ws, "inv", autonomous=True, render_hook=hook)
    assert sorted(calls) == sorted(["eigen", "old", accepted["name"]])   # once each
    assert {r["status"] for r in out["runs"]} == {"executed"}
    for n in calls:
        assert read_meta(sd / "viz" / f"{n}.html")["source_run_id"] == "r2"

    # ---- Cycle 2 -------------------------------------------------------------------------
    disk2, res2 = _persist(ws)
    assert _viz_items(ws) == []                              # gone: reclassified fresh
    p = disk2["viz_proposals"][0]
    assert p["status"] == "built" and p["built"]["source_run_id"] == "r2"
    assert len(disk2["viz_proposals"]) == 1
    _, inv2, _ = _cycle_res(ws)
    assert [i for i in de.frontier(res2, inv2) if i["kind"] in de._CLOSURE_BLOCKING
            and i["kind"].startswith("viz_")] == []
    assert closed_before["closed"] is True and de.closure(res2, inv2)["closed"] is True   # closure holds both cycles

    # ---- Re-run idempotence --------------------------------------------------------------
    _, _, st = D.load_cycle_inputs(ws, "inv")
    again = propose_visualizations(_disk(ws), st, res2, D.viz_inventory(ws, "inv", st), None, now="T3")
    assert [q for q in again if q["id"] != vp["id"]] == []
    disk3, _ = _persist(ws)
    assert disk3["viz_proposals"] == disk2["viz_proposals"]
