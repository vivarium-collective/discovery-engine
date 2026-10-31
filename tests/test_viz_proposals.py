"""Task 8: propose_visualizations + ledger + accept/reject/close."""
import pytest
import yaml

from discovery_engine import discover as D
from discovery_engine import discovery_engine as de
from discovery_engine.investigation_import import investigations_dir, studies_dir
from discovery_engine.viz_freshness import stamp_meta
from discovery_engine.viz_proposals import CAP, propose_visualizations

CAT = [{"address": "local:BandOverlayViz", "inputs": {"series": "timeseries"}, "form": "band_overlay"}]
RUNS = [{"run_id": "r1", "completed_at": 10.0}]


def _test(name, path):
    return {"name": name, "measure": {"kind": "final", "path": path}, "pass_if": {"low": 1, "high": 2}}


def _spec(tests, vis=()):
    return {"name": "s5", "behavior_tests": tests, "visualizations": list(vis),
            "findings": [{"id": "f-a", "evidence": {"from_test": "t-a"}}]}


INV = {"slug": "inv", "studies": ["s5"]}


def _prop(spec, cat=None, inv=INV):
    return propose_visualizations(inv, {"s5": spec}, {}, {"s5": {}}, cat, now="T")


def test_uncovered_gate_gives_one_proposal_needs_author_without_catalog():
    ps = _prop(_spec([_test("t-a", "pop/survivors")]))
    assert len(ps) == 1
    p = ps[0]
    assert p["id"].startswith("vp:s5:") and p["status"] == "proposed"
    assert p["source"] == {"kind": "gate_uncovered", "behavior_test": "t-a", "observable": "pop/survivors"}
    assert p["suggested"]["needs_author"] is True and p["suggested"]["address"] is None
    assert p["derivable"] is False and p["approval"] == "review"
    assert p["sharpens"]["finding"] == "f-a"
    assert "scientific_judgment" not in p


def test_catalog_makes_it_derivable():
    p = _prop(_spec([_test("t-a", "pop/survivors")]), CAT)[0]
    assert p["derivable"] is True and p["suggested"]["address"] == "local:BandOverlayViz"
    assert p["suggested"]["inputs_map"] == {"series": "pop/survivors"}


def test_covered_by_declared_viz_gives_none():
    for vis in ([{"name": "v", "address": "local:X", "covers": ["pop/survivors"]}],
                [{"name": "v", "address": "local:X", "requires": ["pop/survivors"]}],
                [{"name": "v", "address": "local:X", "config": {"inputs_map": {"series": "pop/survivors"}}}]):
        assert _prop(_spec([_test("t-a", "pop/survivors")], vis)) == []


def test_covered_via_catalog_inputs_of_declared_class_gives_none():
    cat = [{"address": "local:Cls", "inputs": {"pop/survivors": "timeseries"}}]
    spec = _spec([_test("t-a", "pop/survivors")], [{"name": "v", "address": "local:Cls"}])
    assert _prop(spec, cat) == []


def test_cap_two_per_study_ordered_by_id():
    spec = _spec([_test(f"t{i}", f"obs/{i}") for i in range(5)])
    ps = _prop(spec)
    assert len(ps) == CAP == 2
    assert [p["id"] for p in ps] == sorted(p["id"] for p in ps)


def test_discriminating_and_disagreement_triggers():
    inv = {**INV, "hypotheses": [
        {"id": "h1", "study": "s5", "predictions": [{"observable": "o/d", "expected": {"low": 0, "high": 1}}]},
        {"id": "h2", "study": "s5", "predictions": [{"observable": "o/d", "expected": {"low": 5, "high": 6}}]}]}
    spec = {"name": "s5", "visualizations": [],
            "findings": [{"id": "f", "evidence": {"measure": "o/m", "arms": {"a": 1.0, "b": 2.0}}}]}
    ps = propose_visualizations(inv, {"s5": spec}, {}, {"s5": {}}, None, now="T")
    kinds = {p["source"]["kind"] for p in ps}
    assert "discriminating_uncovered" in kinds
    disc = [p for p in ps if p["source"]["kind"] == "discriminating_uncovered"][0]
    assert disc["suggested"]["form"] == "prediction_bands" and disc["source"]["observable"] == "o/d"


def test_content_id_rederive_is_stable():
    spec = _spec([_test("t-a", "pop/survivors")])
    assert [p["id"] for p in _prop(spec)] == [p["id"] for p in _prop(spec)]


# ---- ledger through a workspace ---------------------------------------------------------

def _ws(tmp_path, monkeypatch, vis=()):
    ws = tmp_path
    inv = investigations_dir(ws) / "inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump(INV))
    sd = studies_dir(ws) / "s5"
    (sd / "viz").mkdir(parents=True)
    spec = _spec([_test("t-a", "pop/survivors")], vis)
    spec["runs"] = [{"name": "r1"}]
    (sd / "study.yaml").write_text(yaml.safe_dump(spec))
    monkeypatch.setattr(D, "_load_runs_by_study", lambda w, i, s: {k: list(RUNS) for k in s})
    return ws, sd


def _cycle(ws):
    inv_path, inv, st = D.load_cycle_inputs(ws, "inv")
    res = D._cycle_with_evidence(ws, "inv", inv, st)
    D.persist_cycle(inv_path, inv, res, studies=st, viz_state=D.viz_inventory(ws, "inv", st))
    return yaml.safe_load(inv_path.read_text()), res


def _frontier(ws):
    _, inv, st = D.load_cycle_inputs(ws, "inv")
    return [i for i in de.frontier(D._cycle_with_evidence(ws, "inv", inv, st), inv)
            if i["kind"].startswith("viz_")]


def test_viz_proposed_item_review_tier_and_idempotent_across_cycles(tmp_path, monkeypatch):
    ws, _ = _ws(tmp_path, monkeypatch)
    disk1, _ = _cycle(ws)
    disk2, _ = _cycle(ws)
    assert len(disk1["viz_proposals"]) == 1
    assert disk2["viz_proposals"] == disk1["viz_proposals"]      # no duplicate, no churn
    items = _frontier(ws)
    assert [(i["kind"], i["op"], i["priority"]) for i in items] == [("viz_proposed", "build_viz", 44)]
    ex = de.execute_plan(D._cycle_with_evidence(ws, "inv", *D.load_cycle_inputs(ws, "inv")[1:]),
                         D.load_cycle_inputs(ws, "inv")[1])
    row = [e for e in ex if e["kind"] == "viz_proposed"][0]
    assert row["consequence"] == "review" and row["auto_safe"] is False
    assert "viz_proposed" not in de._CLOSURE_BLOCKING
    # advance never auto-executes it
    out = D.advance_cycle(ws, "inv", autonomous=True)
    assert out["runs"] == []


def test_accept_materializes_entry_and_item_becomes_viz_missing(tmp_path, monkeypatch):
    ws, sd = _ws(tmp_path, monkeypatch)
    disk, _ = _cycle(ws)
    vp = disk["viz_proposals"][0]["id"]
    with pytest.raises(ValueError):
        D.accept_viz_proposal(ws, "inv", vp, address="local:X", rationale="  ")
    with pytest.raises(ValueError):                         # needs_author: no address given
        D.accept_viz_proposal(ws, "inv", vp, rationale="yes")
    D.accept_viz_proposal(ws, "inv", vp, address="local:BandOverlayViz", rationale="reviewer wants it")
    ent = yaml.safe_load((sd / "study.yaml").read_text())["visualizations"][0]
    assert ent["origin"] == {"kind": "viz_proposal", "ref": vp}
    assert ent["address"] == "local:BandOverlayViz" and ent["covers"] == ["pop/survivors"]
    inv = yaml.safe_load((investigations_dir(ws) / "inv" / "investigation.yaml").read_text())
    p = inv["viz_proposals"][0]
    assert p["status"] == "accepted" and p["history"][-1]["event"] == "accepted"
    assert p["history"][-1]["rationale"] == "reviewer wants it"
    kinds = [(i["kind"], i["target"]) for i in _frontier(ws)]
    assert kinds == [("viz_missing", f"fig:s5/{ent['name']}")]
    # the declared entry now covers the observable: re-cycling does not re-propose
    disk2, _ = _cycle(ws)
    assert len(disk2["viz_proposals"]) == 1


def test_accepted_flips_built_when_fresh(tmp_path, monkeypatch):
    ws, sd = _ws(tmp_path, monkeypatch)
    disk, _ = _cycle(ws)
    vp = disk["viz_proposals"][0]["id"]
    D.accept_viz_proposal(ws, "inv", vp, address="local:BandOverlayViz", rationale="ok")
    disk, _ = _cycle(ws)
    assert disk["viz_proposals"][0]["status"] == "accepted"      # declared, not rendered yet
    name = yaml.safe_load((sd / "study.yaml").read_text())["visualizations"][0]["name"]
    chart = sd / f"viz/{name}.html"
    chart.write_text("<html>x</html>")
    stamp_meta(chart, source_run_id="r1", generation_id=None, rendered_at=20.0, command="x")
    disk, _ = _cycle(ws)
    p = disk["viz_proposals"][0]
    assert p["status"] == "built" and p["built"]["source_run_id"] == "r1"
    assert p["history"][-1]["event"] == "built" and p["history"][-1]["by"] == "engine"
    disk, _ = _cycle(ws)                                         # terminal
    assert disk["viz_proposals"][0]["status"] == "built"


def test_reject_is_terminal(tmp_path, monkeypatch):
    ws, _ = _ws(tmp_path, monkeypatch)
    disk, _ = _cycle(ws)
    vp = disk["viz_proposals"][0]["id"]
    with pytest.raises(ValueError):
        D.reject_viz_proposal(ws, "inv", vp, rationale="")
    D.reject_viz_proposal(ws, "inv", vp, rationale="not useful")
    assert _frontier(ws) == []
    disk, res = _cycle(ws)
    assert disk["viz_proposals"][0]["status"] == "rejected"
    assert not [p for p in res["viz_proposals"] if p["id"] == vp]
    assert _frontier(ws) == []
    with pytest.raises(ValueError):
        D.accept_viz_proposal(ws, "inv", vp, address="local:X", rationale="late")


def test_cli_propose_accept_reject(tmp_path, monkeypatch, capsys):
    ws, _ = _ws(tmp_path, monkeypatch)
    assert D.main(["--workspace", str(ws), "viz", "inv", "--propose"]) == 0
    out = capsys.readouterr().out
    assert "vp:s5:" in out and "needs_author" in out
    vp = out.split()[0]
    assert D.main(["--workspace", str(ws), "viz", "inv", "--reject", vp, "--rationale", "no"]) == 0
