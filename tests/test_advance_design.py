"""DE-10 — `advance` autonomously authors a discriminating study for a competing-hypothesis
fork, gated by the autonomy policy.

Modeled on viva-genesis `origins-of-life-vent` / og-01: the classical alkaline-vent framework
(Lane/Martin) vs the 2026 unfavorable reassessment (Tutolo/Jackson), which fork on the batch
formate yield ``settled_formate_mM`` from abiotic H2->CO2 reduction."""
from __future__ import annotations

import yaml

from discovery_engine.discover.cycle import advance_cycle

PARENT = {"schema_version": 4, "name": "og-carbon", "investigation": "vent",
          "observables": [{"path": "settled_formate_mM"}],
          "behavior_tests": [{"measure": {"path": "settled_formate_mM"}}],
          "baseline": [{"name": "baseline", "composite": "viva_genesis.carbon_fixation",
                        "params": {"H2_flux_mol": 1.0}}]}
# the two frameworks, forking on settled_formate_mM under the same vent condition -> disjoint
# bands -> one run discriminates them.
HYPS = [
    {"id": "h-vent-reductive", "study": "og-carbon", "status": "open", "predictions": [
        {"observable": "settled_formate_mM", "when": {"H2_flux_mol": 2.0},
         "expected": {"op": ">=", "value": 1.0}}]},
    {"id": "h-thermo-unfavorable", "study": "og-carbon", "status": "open", "predictions": [
        {"observable": "settled_formate_mM", "when": {"H2_flux_mol": 2.0},
         "expected": {"op": "<=", "value": 0.05}}]},
]


def _ws(tmp_path, hyps=HYPS):
    ws = tmp_path / "ws"
    (ws / "investigations" / "vent").mkdir(parents=True)
    (ws / "studies" / "og-carbon").mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    (ws / "studies" / "og-carbon" / "study.yaml").write_text(yaml.safe_dump(PARENT), encoding="utf-8")
    (ws / "investigations" / "vent" / "investigation.yaml").write_text(
        yaml.safe_dump({"name": "vent", "studies": ["og-carbon"], "hypotheses": hyps}),
        encoding="utf-8")
    return ws


def test_advance_autonomously_designs_the_discriminating_study(tmp_path):
    ws = _ws(tmp_path)
    res = advance_cycle(ws, "vent", autonomous=True)
    designed = res["designed"]
    assert any(d["status"] == "designed" and d["study"] == "og-carbon"
               and d["child"] == "og-carbon-discriminating" for d in designed), designed
    inv = yaml.safe_load((ws / "investigations" / "vent" / "investigation.yaml").read_text())
    assert "og-carbon-discriminating" in inv["studies"]                    # child registered
    assert any(x["id"] == "design:og-carbon" for x in inv.get("experiment_designs", []))
    child = yaml.safe_load((ws / "studies" / "og-carbon-discriminating" / "study.yaml").read_text())
    assert any(v.get("params", {}).get("H2_flux_mol") == 2.0 for v in child["variants"])


def test_advance_does_not_redesign_once_the_child_exists(tmp_path):
    ws = _ws(tmp_path)
    advance_cycle(ws, "vent", autonomous=True)
    res2 = advance_cycle(ws, "vent", autonomous=True)
    assert all(d["status"] != "designed" for d in res2["designed"])        # idempotent — no loop


def test_dry_advance_reports_would_design_without_writing(tmp_path):
    ws = _ws(tmp_path)
    res = advance_cycle(ws, "vent", autonomous=False)
    assert any("would-design" in d["status"] for d in res["designed"])
    assert not (ws / "studies" / "og-carbon-discriminating").exists()      # dry: nothing written


def test_advance_noop_without_a_fork(tmp_path):
    ws = _ws(tmp_path, hyps=[HYPS[0]])        # a single hypothesis is not discriminable
    res = advance_cycle(ws, "vent", autonomous=True)
    assert res["designed"] == []
