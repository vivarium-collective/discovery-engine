"""Task 3: viz-appropriateness engine acceptance (engine wave exit).

End-to-end on a disk fixture: load -> run_cycle -> frontier -> closure -> persist -> advance.
"""
import yaml

from discovery_engine import discover as D
from discovery_engine import discovery_engine as de
from discovery_engine.investigation_import import investigations_dir, studies_dir

CAT = [{"address": "local:Cross", "inputs": {"arms": "list[string]", "labels": "list[string]"}},
       {"address": "local:Per", "inputs": {"time": "list[float]"}}]
KINDS = ("viz_misplaced", "viz_duplicated", "viz_untied")
SYN_PG = {"prerequisites": [{"study": "a", "relation": "builds-on"},
                            {"study": "b", "relation": "builds-on"}]}


def _viz(addr, name, covers=True, **kw):
    e = {"name": name, "address": addr, **kw}
    if covers:
        e["covers"] = ["obs"]
    return e


def _ws(tmp_path, monkeypatch, studies, inv_extra=None):
    inv = investigations_dir(tmp_path) / "inv"
    inv.mkdir(parents=True)
    spec = {"slug": "inv", "studies": list(studies), **(inv_extra or {})}
    (inv / "investigation.yaml").write_text(yaml.safe_dump(spec))
    for slug, sp in studies.items():
        d = studies_dir(tmp_path) / slug
        d.mkdir(parents=True)
        (d / "study.yaml").write_text(yaml.safe_dump({"name": slug, **sp}))
    monkeypatch.setattr(D, "_load_runs_by_study", lambda w, i, s: {k: [] for k in s})
    return tmp_path


def _res(ws, catalog=CAT):
    """run_cycle on the disk fixture; catalog=None -> the headless production path."""
    _, inv, st = D.load_cycle_inputs(ws, "inv")
    if catalog is None:
        return D._cycle_with_evidence(ws, "inv", inv, st), inv, st
    return D.run_cycle(inv, st, viz_catalog=catalog), inv, st


def _items(ws, catalog=CAT):
    res, inv, st = _res(ws, catalog)
    return res, inv, st, [i for i in de.frontier(res, inv) if i["kind"] in KINDS]


def _nonblocking(res, inv):
    assert not set(KINDS) & set(de._CLOSURE_BLOCKING)
    assert [i for i in de.frontier(res, inv) if i["kind"] in KINDS and i["kind"] in de._CLOSURE_BLOCKING] == []
    return de.closure(res, inv)


def test_1_misplaced_under_plain_study_nonblocking(tmp_path, monkeypatch):
    ws = _ws(tmp_path, monkeypatch, {"a": {"visualizations": [_viz("local:Cross", "x")]}})
    res, inv, _, items = _items(ws)
    assert [(i["kind"], i["target"], i["op"]) for i in items] == [("viz_misplaced", "fig:a/x", "relocate_viz")]
    base_ws = _ws(tmp_path / "ctl", monkeypatch, {"a": {"visualizations": [_viz("local:Per", "x")]}})
    ctl, cinv, _, citems = _items(base_ws)
    assert citems == []
    _nonblocking(res, inv)
    assert de.closure(res, inv)["closed"] == de.closure(ctl, cinv)["closed"]   # closure unaffected


def test_2_synthesis_study_exempt(tmp_path, monkeypatch):
    ws = _ws(tmp_path, monkeypatch, {
        "a": {}, "b": {},
        "syn": {"visualizations": [_viz("local:Cross", "x")], "pipeline_gate": SYN_PG}})
    _, _, _, items = _items(ws)
    assert items == []


def test_3_duplicated_surfaces_once(tmp_path, monkeypatch):
    ws = _ws(tmp_path, monkeypatch, {"a": {"visualizations": [_viz("local:Cross", "x")]},
                                     "b": {"visualizations": [_viz("local:Cross", "x")]}})
    res, inv, _, items = _items(ws)
    kinds = [i["kind"] for i in items]
    assert kinds.count("viz_duplicated") == 1 and kinds.count("viz_misplaced") == 2
    dup = next(i for i in items if i["kind"] == "viz_duplicated")
    assert dup["target"] == "xviz:local:Cross"
    _nonblocking(res, inv)


def test_4_untied_only_with_rollout_flag(tmp_path, monkeypatch):
    studies = {"a": {"visualizations": [_viz("local:Per", "p", covers=False)]}}
    ws = _ws(tmp_path / "off", monkeypatch, studies)
    _, _, _, items = _items(ws)
    assert items == []                                        # off by default
    ws2 = _ws(tmp_path / "on", monkeypatch, studies, {"cycle_flags": {"viz_untied": True}})
    res, inv, _, items = _items(ws2)
    assert [(i["kind"], i["target"]) for i in items] == [("viz_untied", "fig:a/p")]
    _nonblocking(res, inv)


def test_5_headless_authored_scope(tmp_path, monkeypatch):
    ws = _ws(tmp_path, monkeypatch, {"a": {"visualizations": [
        _viz("local:Table", "authored", scope="investigation"),
        _viz("local:Cross", "unscoped")]}})
    res, inv, _, items = _items(ws, catalog=None)             # NO catalog
    assert [(i["kind"], i["target"]) for i in items] == [("viz_misplaced", "fig:a/authored")]
    rows = {r["name"]: r for r in res["viz_appropriateness"]["per_study"]["a"]}
    assert rows["authored"]["scope"] == "investigation"
    assert rows["unscoped"]["state"] == "not_assessed"
    assert not any("unscoped" in i["target"] for i in de.frontier(res, inv) if i["kind"] in KINDS)


def test_6_never_auto_executed(tmp_path, monkeypatch):
    ws = _ws(tmp_path, monkeypatch, {
        "a": {"visualizations": [_viz("local:Cross", "x", scope="investigation"),
                                 _viz("local:Per", "u", covers=False)]},
        "b": {"visualizations": [_viz("local:Cross", "x", scope="investigation")]}},
        {"cycle_flags": {"viz_untied": True}})
    res, inv, _ = _res(ws, None)
    fr = [i for i in de.frontier(res, inv) if i["kind"] in KINDS]
    assert {i["kind"] for i in fr} == set(KINDS)
    plan = [p for p in de.execute_plan(res, inv) if p["action_type"] == "relocate_viz"]
    assert plan and all(p["consequence"] not in ("simulation", "simulation+study", "render") for p in plan)
    assert "relocate_viz" not in D._ADVANCE_OPS
    out = D.advance_cycle(ws, "inv", autonomous=True, render_hook=lambda *a: (_ for _ in ()).throw(AssertionError))
    assert not any(r.get("target", "").startswith(("fig:", "xviz:")) for r in out["runs"])
    _, inv2, st2 = D.load_cycle_inputs(ws, "inv")
    still = [i for i in de.frontier(D._cycle_with_evidence(ws, "inv", inv2, st2), inv2) if i["kind"] in KINDS]
    assert {i["kind"] for i in still} == set(KINDS)           # nothing resolved them


def test_7_census_and_persist(tmp_path, monkeypatch):
    ws = _ws(tmp_path, monkeypatch, {
        "a": {"visualizations": [_viz("local:Cross", "x"), _viz("local:Per", "u", covers=False),
                                 _viz("local:Mystery", "m")]},
        "b": {"visualizations": [_viz("local:Cross", "x")]}})
    res, inv, st = _res(ws)
    cs = D.build_cycle_state(res, inv, studies=st)
    assert cs["viz_appropriateness"] == {
        "misplaced": 2, "untied": 1, "duplicated": 1, "not_assessed": 1,
        "any_issue": True, "untied_surfaced": False}
    inv_path = investigations_dir(ws) / "inv" / "investigation.yaml"
    D.persist_cycle(inv_path, inv, res, studies=st)
    disk = yaml.safe_load(inv_path.read_text())
    assert disk["cycle_state"]["viz_appropriateness"]["misplaced"] == 2
    assert disk["cycle_state"]["viz_appropriateness"]["duplicated"] == 1
