"""PR 4 — the capability-gap build is now dispatched by `advance` under the AutonomyPolicy.

The propose -> accept -> build(via injected hook) -> recatalog -> coverage-verify -> close loop
was already real + tested (test_capability_gap_two_cycle), but only reachable via an explicit
`execute_action(target=cg, build_hook=…)` call. PR 4 wires `build_process` into `advance_cycle`,
so `advance --autonomous` builds an ACCEPTED gap when a build_hook is available — then a study
that needed the just-built mechanism becomes run-ready. The engine still never builds itself: the
composite is authored by the workspace's injected hook; the engine drives it and verifies coverage.
"""
from __future__ import annotations

import yaml

from discovery_engine import discover as D
from discovery_engine.investigation.import_ import investigations_dir, studies_dir
from discovery_engine.phases.model_architect import build_catalog

MECH = "plasmid replication"
BASE = "pkg.mod.GrowthComposite"          # does NOT cover MECH
COVER = "pkg.mod.PlasmidReplication"      # topic tokenizes to MECH -> covers it


def _fixture(tmp_path, monkeypatch):
    ws = tmp_path
    inv = investigations_dir(ws) / "inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump(
        {"name": "inv", "slug": "inv", "studies": ["s1"], "required_mechanisms": [MECH]}))
    sd = studies_dir(ws) / "s1"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump(
        {"name": "s1", "topic": "growth", "baseline": [{"composite": BASE}], "builds": [MECH]}))
    monkeypatch.setattr(D, "_load_runs_by_study", lambda w, i, s: {k: [] for k in s})
    return ws


def _stub_runner(command, ws_root, *, worktree=None, **kw):
    return {"ok": True, "returncode": 0, "run_id": "stub", "tail": ""}


def _accepted_gap(ws) -> str:
    """Identify + persist + accept a capability gap; return its id (status: accepted)."""
    inv_path, inv, st = D.load_cycle_inputs(ws, "inv")
    res = D._cycle_with_evidence(ws, "inv", inv, st)
    D.persist_cycle(inv_path, inv, res, studies=st)
    disk = yaml.safe_load(inv_path.read_text())
    cg = disk["capability_gaps"][0]["id"]
    D.accept_capability_gap(ws, "inv", cg, rationale="needed for the plasmid question")
    return cg


def _covering_hook(calls):
    def hook(gap, ws_root):
        calls.append(gap["id"])
        # the workspace build_hook authors the composite (registers a covering study)
        sd = studies_dir(ws_root) / "s2"
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "study.yaml").write_text(yaml.safe_dump(
            {"name": "s2", "topic": MECH, "baseline": [{"composite": COVER}]}))
        ip = investigations_dir(ws_root) / "inv" / "investigation.yaml"
        d = yaml.safe_load(ip.read_text())
        d["studies"] = sorted(set(d["studies"]) | {"s2"})
        ip.write_text(yaml.safe_dump(d))
        return {"status": "reuse", "process": COVER, "gap_id": gap["id"], "detail": "built by hook"}
    return hook


def test_advance_builds_accepted_gap_with_hook(tmp_path, monkeypatch):
    ws = _fixture(tmp_path, monkeypatch)
    cg = _accepted_gap(ws)
    calls: list[str] = []

    out = D.advance_cycle(ws, "inv", autonomous=True, runner=_stub_runner,
                          build_hook=_covering_hook(calls), max_runs=6)

    assert calls == [cg], f"build_hook should have run once for {cg}; advance runs: {out['runs']}"
    disk = yaml.safe_load((investigations_dir(ws) / "inv" / "investigation.yaml").read_text())
    built = next(g for g in disk["capability_gaps"] if g["id"] == cg)
    assert built["status"] == "built" and built["built"]["process"] == COVER
    # the just-built process is now in the catalog (the mechanism is covered)
    assert COVER in {c["composite"] for c in build_catalog(D.load_cycle_inputs(ws, "inv")[2])}


def test_advance_does_not_build_without_a_hook(tmp_path, monkeypatch):
    ws = _fixture(tmp_path, monkeypatch)
    cg = _accepted_gap(ws)
    # no build_hook injected and no workspace <pkg>.build_hook -> the engine never builds itself
    D.advance_cycle(ws, "inv", autonomous=True, runner=_stub_runner, max_runs=6)
    disk = yaml.safe_load((investigations_dir(ws) / "inv" / "investigation.yaml").read_text())
    assert next(g for g in disk["capability_gaps"] if g["id"] == cg)["status"] == "accepted"


def test_advance_does_not_build_an_unaccepted_gap(tmp_path, monkeypatch):
    ws = _fixture(tmp_path, monkeypatch)
    # identify + persist but DO NOT accept — a proposed gap needs a human accept first
    inv_path, inv, st = D.load_cycle_inputs(ws, "inv")
    D.persist_cycle(inv_path, inv, D._cycle_with_evidence(ws, "inv", inv, st), studies=st)
    calls: list[str] = []
    D.advance_cycle(ws, "inv", autonomous=True, runner=_stub_runner,
                    build_hook=_covering_hook(calls), max_runs=6)
    assert calls == []   # a proposed (un-accepted) gap is not auto-built
    disk = yaml.safe_load((investigations_dir(ws) / "inv" / "investigation.yaml").read_text())
    assert disk["capability_gaps"][0]["status"] == "proposed"
