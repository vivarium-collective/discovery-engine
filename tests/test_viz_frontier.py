from discovery_engine import discover as D
from discovery_engine import discovery_engine as de

INV = {"slug": "inv", "studies": ["s5"]}


def _vs(**extra):
    return {"s5": {"_latest": {"run_id": "r1", "completed_at": 10.0}, "_orphans": [], **extra}}


def _row(exists=True, meta=None, mtime=20.0):
    return {"path": "viz/x.html", "exists": exists, "meta": meta, "mtime": mtime,
            "content_hash": "sha256:ab" if exists else None}


def _run(vis, viz_state):
    studies = {"s5": {"visualizations": vis}}
    return D.run_cycle(INV, studies, viz_state=viz_state), studies


def _viz_items(res):
    return [i for i in de.frontier(res, INV) if i["kind"].startswith("viz_")]


def test_broken_stub_with_orphan_gives_viz_broken_with_fix():
    vs = _vs(**{"_orphans": ["viz/eigen-catastrophe.html"]})
    res, _ = _run([{"name": "eigen-catastrophe", "rendered": "x"}], vs)
    items = _viz_items(res)
    assert [(i["kind"], i["op"], i["priority"]) for i in items] == [("viz_broken", "refine_viz", 47)]
    assert items[0]["target"] == "fig:s5/eigen-catastrophe"
    assert "html:viz/eigen-catastrophe.html" in items[0]["rationale"]
    rec = [r for r in res["validations"] if r["target"] == "fig:s5/eigen-catastrophe"][0]
    assert rec["evidence"][0]["derivable_fix"] == {"address": "html:viz/eigen-catastrophe.html"}
    assert rec["result"]["scientific_judgment"] is None


def test_local_entry_without_meta_stale_or_missing():
    vis = [{"name": "a", "address": "local:x.A"}, {"name": "b", "address": "local:x.B"}]
    vs = _vs(a=_row(mtime=1.0), b=_row(exists=False, mtime=None))
    res, _ = _run(vis, vs)
    kinds = {i["target"]: i["kind"] for i in _viz_items(res)}
    assert kinds == {"fig:s5/a": "viz_stale", "fig:s5/b": "viz_missing"}
    ops = {i["kind"]: (i["op"], i["priority"]) for i in _viz_items(res)}
    assert ops["viz_stale"] == ("refine_viz", 38) and ops["viz_missing"] == ("build_viz", 46)


def test_fresh_emits_nothing():
    res, _ = _run([{"name": "a", "address": "local:x.A"}], _vs(a=_row(mtime=99.0)))
    assert _viz_items(res) == []
    assert not any("[visual/" in i["rationale"] for i in de.frontier(res, INV))


def test_closure_unaffected():
    vis = [{"name": "b", "address": "local:x.B"}, {"name": "s", "rendered": "x"}]
    with_viz, _ = _run(vis, _vs(b=_row(exists=False, mtime=None)))
    without, _ = _run(vis, {})
    assert _viz_items(with_viz)
    assert de.closure(with_viz, INV)["closed"] == de.closure(without, INV)["closed"]
    assert "viz_broken" not in de._CLOSURE_BLOCKING


def test_visual_never_generic_validation_finding():
    res, _ = _run([{"name": "s", "rendered": "x"}], _vs())
    assert not any("[visual/" in i["rationale"]
                   for i in de.frontier(res, INV))


def test_orphan_is_standing_inconclusive_not_frontier():
    res, _ = _run([], _vs(**{"_orphans": ["viz/z.html"]}))
    recs = [r for r in res["validations"] if r["type"] == "visual"]
    assert len(recs) == 1 and recs[0]["criteria"] == ["untracked"]
    assert recs[0]["result"]["execution_status"] == "inconclusive"
    assert _viz_items(res) == []


def test_census_counts():
    vis = [{"name": "a", "address": "local:x.A"}, {"name": "b", "address": "local:x.B"},
           {"name": "c", "address": "local:x.C"}, {"name": "s", "rendered": "x"}]
    vs = _vs(a=_row(mtime=99.0), b=_row(mtime=1.0), c=_row(exists=False, mtime=None),
             **{"_orphans": ["viz/o.html"]})
    res, studies = _run(vis, vs)
    c = D.build_cycle_state(res, INV, studies=None)["viz_census"]
    assert (c["fresh"], c["stale"], c["unrendered"], c["broken"], c["untracked_files"]) == (1, 1, 1, 1, 1)


def test_hermetic_no_viz_state():
    res, _ = _run([{"name": "s", "rendered": "x"}], {})
    assert not [r for r in res["validations"] if r["type"] == "visual"]


def test_resolution_kinds():
    for k in ("viz_broken", "viz_missing", "viz_proposed", "viz_stale"):
        assert k in D.RESOLUTION_KINDS


def test_crashed_visual_validator_surfaces(monkeypatch):
    from discovery_engine import validation as V
    reg = [v for v in V._REGISTRY if v["id"] == "visual.figure.freshness"][0]
    monkeypatch.setitem(reg, "fn", lambda ctx: (_ for _ in ()).throw(RuntimeError("boom")))
    res, _ = _run([{"name": "a", "address": "local:x.A"}], _vs(a=_row()))
    f = de.frontier(res, INV)
    hit = [i for i in f if i["kind"] == "validation_finding" and "boom" in i["rationale"]]
    assert hit and hit[0]["op"] == "revise"


def test_charts_dir_fix_and_no_double_orphan():
    vs = _vs(**{"_orphans": ["charts/eigen.svg", "viz/other.html"]})
    res, _ = _run([{"name": "eigen", "rendered": "x"}], vs)
    rec = [r for r in res["validations"] if r["target"] == "fig:s5/eigen"][0]
    assert rec["evidence"][0]["derivable_fix"] == {"address": "svg:charts/eigen.svg"}
    unt = [r["target"] for r in res["validations"] if r["criteria"] == ["untracked"]]
    assert unt == ["fig:s5/viz/other.html"]
    assert D.build_cycle_state(res, INV)["viz_census"]["untracked_files"] == 1
