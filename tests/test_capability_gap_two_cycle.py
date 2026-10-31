"""Task 4: the two-cycle capability-gap acceptance test (engine wave exit).

identify (cycle 1) -> accept -> build via a fake build_hook -> re-catalog (cycle 2) -> idempotent.
Mirrors tests/test_viz_loop_two_cycle.py.
"""
import pytest
import yaml

from discovery_engine import discover as D
from discovery_engine import discovery_engine as de
from discovery_engine.capability_gaps import propose_capability_gaps
from discovery_engine.investigation_import import investigations_dir, studies_dir
from discovery_engine.model_architect import build_catalog

MECH = "plasmid replication"
BASE = "pkg.mod.GrowthComposite"


def _fixture(tmp_path, monkeypatch, *, builds=None):
    ws = tmp_path
    inv = investigations_dir(ws) / "inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump(
        {"name": "inv", "slug": "inv", "studies": ["s1"], "required_mechanisms": [MECH]}))
    sd = studies_dir(ws) / "s1"
    sd.mkdir(parents=True)
    spec = {"name": "s1", "topic": "growth", "baseline": {"composite": BASE}}
    if builds:
        spec["builds"] = [builds]
    (sd / "study.yaml").write_text(yaml.safe_dump(spec))
    monkeypatch.setattr(D, "_load_runs_by_study", lambda w, i, s: {k: [] for k in s})
    return ws


def _cycle(ws):
    _, inv, st = D.load_cycle_inputs(ws, "inv")
    return D._cycle_with_evidence(ws, "inv", inv, st), inv, st


def _items(ws):
    res, inv, _ = _cycle(ws)
    return [i for i in de.frontier(res, inv) if i["kind"] == "capability_gap"]


def _persist(ws):
    inv_path, inv, st = D.load_cycle_inputs(ws, "inv")
    res = D._cycle_with_evidence(ws, "inv", inv, st)
    D.persist_cycle(inv_path, inv, res, studies=st)
    return yaml.safe_load(inv_path.read_text()), res


def _disk(ws):
    return yaml.safe_load((investigations_dir(ws) / "inv" / "investigation.yaml").read_text())


def _blocking(ws):
    res, inv, _ = _cycle(ws)
    return [i for i in de.frontier(res, inv) if i["kind"] in de._CLOSURE_BLOCKING
            and i["kind"] == "capability_gap"]


def test_two_cycle_capability_gap_loop(tmp_path, monkeypatch):
    ws = _fixture(tmp_path, monkeypatch, builds=MECH)

    # ---- Cycle 1: identify ---------------------------------------------------------------
    res1, inv, _ = _cycle(ws)
    items = _items(ws)
    assert len(items) == 1
    assert _blocking(ws) == []                                   # non-blocking
    closed_before = de.closure(res1, inv)
    disk1, _ = _persist(ws)
    assert len(disk1["capability_gaps"]) == 1
    g = disk1["capability_gaps"][0]
    assert g["status"] == "proposed" and g["mechanism"] == MECH and g["derivable"] is True
    cg = g["id"]

    # ---- Accept (rationale-gated) --------------------------------------------------------
    with pytest.raises(ValueError):
        D.accept_capability_gap(ws, "inv", cg, rationale="   ")
    assert _disk(ws)["capability_gaps"][0]["status"] == "proposed"
    D.accept_capability_gap(ws, "inv", cg, rationale="needed for the plasmid question")
    acc = _disk(ws)["capability_gaps"][0]
    assert acc["status"] == "accepted" and acc["accept"]["rationale"] == "needed for the plasmid question"
    assert len(_items(ws)) == 1                                  # accepted still on the frontier

    # ---- Non-autonomous / no hook: gated, hook NOT called, nothing fabricated ------------
    calls = []

    def hook(gap, ws_root):
        calls.append(gap["id"])
        return {"status": "reuse", "process": BASE, "gap_id": gap["id"], "detail": "fake"}

    rec = D.execute_action(ws, "inv", autonomous=False, target=cg, build_hook=hook)
    assert rec["status"] == "gated-human" and calls == []
    rec = D.execute_action(ws, "inv", autonomous=True, target=cg)       # no hook anywhere
    assert rec["status"] == "gated-human" and calls == []
    assert _disk(ws)["capability_gaps"][0]["status"] == "accepted"

    # ---- Build via the fake hook ---------------------------------------------------------
    rec = D.execute_action(ws, "inv", autonomous=True, target=cg, build_hook=hook)
    assert rec["status"] == "executed" and calls == [cg]
    built = _disk(ws)["capability_gaps"][0]
    assert built["status"] == "built" and built["built"]["process"] == BASE
    assert BASE in {c["composite"] for c in build_catalog(D.load_cycle_inputs(ws, "inv")[2])}

    # ---- Cycle 2 -------------------------------------------------------------------------
    disk2, res2 = _persist(ws)
    assert _items(ws) == []                                      # gone from the frontier
    assert disk2["capability_gaps"][0]["status"] == "built" and len(disk2["capability_gaps"]) == 1
    _, inv2, _ = _cycle(ws)
    assert _blocking(ws) == []
    assert closed_before["closed"] is True and de.closure(res2, inv2)["closed"] is True

    # ---- Idempotence ---------------------------------------------------------------------
    _, _, st = D.load_cycle_inputs(ws, "inv")
    again = propose_capability_gaps(_disk(ws), st, build_catalog(st), now="T3")
    assert again == []
    disk3, _ = _persist(ws)
    assert disk3["capability_gaps"] == disk2["capability_gaps"]


def test_built_new_process_becomes_covered(tmp_path, monkeypatch):
    """The hook BUILDS a new process (a new study arm); it enters the re-catalog and the
    mechanism is genuinely covered (nothing to re-propose even without the built record)."""
    ws = _fixture(tmp_path, monkeypatch, builds=MECH)
    cg = _persist(ws)[0]["capability_gaps"][0]["id"]
    D.accept_capability_gap(ws, "inv", cg, rationale="build it")
    new = "pkg.mod.PlasmidReplication"

    def hook(gap, ws_root):
        sd = studies_dir(ws_root) / "s2"
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "study.yaml").write_text(yaml.safe_dump(
            {"name": "s2", "topic": "plasmid replication", "baseline": {"composite": new}}))
        ip = investigations_dir(ws_root) / "inv" / "investigation.yaml"
        d = yaml.safe_load(ip.read_text())
        d["studies"] = ["s1", "s2"]
        ip.write_text(yaml.safe_dump(d))
        return {"status": "built", "process": new, "gap_id": gap["id"], "detail": "fake build"}

    rec = D.execute_action(ws, "inv", autonomous=True, target=cg, build_hook=hook)
    assert rec["status"] == "executed", rec
    assert _disk(ws)["capability_gaps"][0]["status"] == "built"
    spec = _disk(ws)
    _, _, st = D.load_cycle_inputs(ws, "inv")
    assert new in {c["composite"] for c in build_catalog(st)}
    assert propose_capability_gaps({**spec, "capability_gaps": []}, st, build_catalog(st), now="T") == []


def test_needs_author_never_auto_built(tmp_path, monkeypatch):
    ws = _fixture(tmp_path, monkeypatch)                         # no `builds` -> needs_author
    g = _persist(ws)[0]["capability_gaps"][0]
    assert g["derivable"] is False and g["suggested"]["needs_author"] is True
    cg = g["id"]
    calls = []
    hook = lambda gap, w: calls.append(gap["id"]) or {"status": "reuse", "process": BASE}  # noqa: E731
    decider = lambda *a: {"choice": "accept", "rationale": "ok"}  # noqa: E731
    rec = D.execute_action(ws, "inv", autonomous=True, target=cg, build_hook=hook, decider=decider)
    assert rec["status"] == "gated-human" and calls == []
    assert _disk(ws)["capability_gaps"][0]["status"] == "proposed"
    assert _blocking(ws) == []
    # a human can still accept it, but accepted-without-a-rationale record cannot be built
    D.accept_capability_gap(ws, "inv", cg, rationale="author supplies it")
    assert _disk(ws)["capability_gaps"][0]["status"] == "accepted"


def test_rejected_gap_never_reemits(tmp_path, monkeypatch):
    ws = _fixture(tmp_path, monkeypatch, builds=MECH)
    cg = _persist(ws)[0]["capability_gaps"][0]["id"]
    D.reject_capability_gap(ws, "inv", cg, rationale="out of scope")
    disk, _ = _persist(ws)
    assert [g["status"] for g in disk["capability_gaps"]] == ["rejected"]
    assert _items(ws) == []
    _, _, st = D.load_cycle_inputs(ws, "inv")
    assert propose_capability_gaps(_disk(ws), st, build_catalog(st)) == []
    assert _persist(ws)[0]["capability_gaps"] == disk["capability_gaps"]
