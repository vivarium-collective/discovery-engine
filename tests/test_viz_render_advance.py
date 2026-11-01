"""Task 5: render executor (injected hook), advance eligibility/no-loop, --declare."""
import json
import time

import pytest
import yaml

from discovery_engine import discover as D
from discovery_engine import discovery_engine as de
from discovery_engine.investigation_import import investigations_dir, studies_dir
from discovery_engine.run_command import (render_command, reset_run_command,
                                          set_render_command_template)
from discovery_engine.viz_freshness import read_meta, stamp_meta

RUNS = [{"run_id": "r1", "completed_at": 10.0}]


@pytest.fixture(autouse=True)
def _reset():
    yield
    reset_run_command()


def _ws(tmp_path, monkeypatch, vis, files=()):
    ws = tmp_path
    inv = investigations_dir(ws) / "inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump({"slug": "inv", "studies": ["s5"]}))
    sd = studies_dir(ws) / "s5"
    (sd / "viz").mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({"name": "s5", "visualizations": vis, "runs": [{"name": "r1"}]}))
    for f in files:
        (sd / f).write_text("<html>old</html>")
    monkeypatch.setattr(D, "_load_runs_by_study", lambda w, i, s: {k: list(RUNS) for k in s})
    return ws, sd


def _items(ws):
    _, inv, st = D.load_cycle_inputs(ws, "inv")
    cyc = D._cycle_with_evidence(ws, "inv", inv, st)
    return [i for i in de.frontier(cyc, inv) if i["kind"].startswith("viz_")]


def _hook(mode, calls):
    def hook(sdir, entry, latest):
        calls.append(entry["name"])
        out = f"viz/{entry['name']}.html"
        if mode == "nothing":
            return {"ok": True, "detail": "claimed ok, wrote nothing"}
        (sdir / out).write_text("<html>new</html>")
        if mode == "stamp":
            stamp_meta(sdir / out, source_run_id=latest["run_id"], generation_id=None,
                       rendered_at=time.time(), command="fake")
        return {"ok": True, "chart": out, "source_run_id": latest["run_id"], "detail": "rendered"}
    return hook


VIS = [{"name": "a", "address": "local:x.A"},   # missing
       {"name": "b", "address": "local:x.B"},   # stale (old, no meta)
       {"name": "c", "address": "local:x.C"}]


def test_render_command_template_and_executor_mapping():
    assert render_command("s", "n", "/w") == "vivarium-workbench render-viz s --viz n --workspace /w"
    set_render_command_template("{runner} r {slug}/{name} {ws}")
    assert render_command("s", "n") == "vivarium-workbench r s/n <ws>"
    reset_run_command()
    ex = de._executor_for("build_viz", "fig:s/n")
    assert (ex["executor"], ex["consequence"], ex["auto_safe"]) == ("render-viz", "render", False)
    assert de._executor_for("refine_viz", "fig:s/n", "viz_stale")["consequence"] == "render"
    for ex in (de._executor_for("refine_viz", "fig:s/n", "viz_broken"),
               de._executor_for("build_viz", "vp:s:abc")):
        assert (ex["executor"], ex["consequence"]) == ("discover viz", "review")


@pytest.mark.parametrize("mode", ["stamp", "unstamped"])
def test_advance_renders_each_once_and_next_frontier_clean(tmp_path, monkeypatch, mode):
    ws, sd = _ws(tmp_path, monkeypatch, VIS, files=("viz/b.html",))
    (sd / "viz" / "b.html").touch()
    import os
    os.utime(sd / "viz" / "b.html", (1.0, 1.0))
    (sd / "viz" / "c.html").write_text("<html>fresh</html>")
    stamp_meta(sd / "viz" / "c.html", source_run_id="r1", generation_id=None,
               rendered_at=time.time(), command="x")
    assert {i["target"] for i in _items(ws)} == {"fig:s5/a", "fig:s5/b"}
    calls = []
    res = D.advance_cycle(ws, "inv", autonomous=True, render_hook=_hook(mode, calls))
    assert sorted(calls) == ["a", "b"]                 # exactly once each; fresh c untouched
    assert {r["status"] for r in res["runs"]} == {"executed"}
    for n in ("a", "b"):
        assert read_meta(sd / "viz" / f"{n}.html")["source_run_id"] == "r1"
    assert _items(ws) == []                            # reclassified fresh next cycle
    log = yaml.safe_load((investigations_dir(ws) / "inv" / "investigation.yaml").read_text())
    assert len([e for e in log["executed_actions"] if e["action"] in ("build_viz", "refine_viz")]) == 2


def test_wrote_nothing_is_failed_gated_not_retried(tmp_path, monkeypatch):
    ws, sd = _ws(tmp_path, monkeypatch, VIS[:1])
    calls = []
    res = D.advance_cycle(ws, "inv", autonomous=True, render_hook=_hook("nothing", calls))
    assert calls == ["a"]
    assert [r["status"] for r in res["runs"]] == ["failed"]
    assert not (sd / "viz" / "a.html").exists()        # never fabricated
    assert [i["kind"] for i in _items(ws)] == ["viz_missing"]


def test_runner_fallback_uses_render_command(tmp_path, monkeypatch):
    ws, sd = _ws(tmp_path, monkeypatch, VIS[:1])
    cmds = []

    def runner(cmd, w, **_kw):
        cmds.append(cmd)
        (sd / "viz" / "a.html").write_text("x")
        return {"ok": True, "detail": "ran"}
    D.advance_cycle(ws, "inv", autonomous=True, runner=runner)
    assert cmds == [f"vivarium-workbench render-viz s5 --viz a --workspace {ws}"]
    assert read_meta(sd / "viz" / "a.html")["source_run_id"] == "r1"   # _post_render_stamp


def test_dry_advance_does_not_render(tmp_path, monkeypatch):
    ws, sd = _ws(tmp_path, monkeypatch, VIS[:1])
    calls = []
    res = D.advance_cycle(ws, "inv", render_hook=_hook("stamp", calls))
    assert calls == [] and "would-run" in res["runs"][0]["status"]


def test_broken_stub_never_dispatched(tmp_path, monkeypatch):
    ws, sd = _ws(tmp_path, monkeypatch, [{"name": "e", "rendered": "x"}],
                 files=("viz/e.html",))
    assert [i["kind"] for i in _items(ws)] == ["viz_broken"]
    calls = []
    res = D.advance_cycle(ws, "inv", autonomous=True, render_hook=_hook("stamp", calls))
    assert calls == [] and res["runs"] == []
    # direct execute on the stub is also refused
    rec = D.execute_action(ws, "inv", autonomous=True, render_hook=_hook("stamp", calls),
                           target="fig:s5/e")
    assert rec["status"] in ("gated", "gated-human") and calls == []


def test_declare_materializes_and_discharges(tmp_path, monkeypatch):
    ws, sd = _ws(tmp_path, monkeypatch, [{"name": "e", "rendered": "x"}],
                 files=("viz/e.html",))
    with pytest.raises(ValueError):
        D.declare_viz(ws, "inv", "fig:s5/e", {"address": "html:viz/e.html"}, " ")
    D.declare_viz(ws, "inv", "fig:s5/e", {"address": "html:viz/e.html",
                                           "render": "python -m p.m e {chart}"}, "matching file exists")
    ent = yaml.safe_load((sd / "study.yaml").read_text())["visualizations"][0]
    assert ent["address"] == "html:viz/e.html" and "rendered" not in ent
    assert ent["origin"] == {"kind": "derived_fix"}
    assert "viz_broken" not in {i["kind"] for i in _items(ws)}
    inv = yaml.safe_load((investigations_dir(ws) / "inv" / "investigation.yaml").read_text())
    assert any(r["kind"] == "viz_broken" and r["target"] == "fig:s5/e" for r in inv["resolutions"])


def test_declare_cli(tmp_path, monkeypatch):
    ws, sd = _ws(tmp_path, monkeypatch, [{"name": "e", "rendered": "x"}])
    rc = D.main(["--workspace", str(ws), "viz", "inv", "fig:s5/e", "--declare",
                 "address=local:p.E", "requires=a,b", "--rationale", "because"])
    assert rc == 0
    ent = yaml.safe_load((sd / "study.yaml").read_text())["visualizations"][0]
    assert ent["address"] == "local:p.E" and ent["requires"] == ["a", "b"]


def _stale_b(tmp_path, monkeypatch):
    ws, sd = _ws(tmp_path, monkeypatch, VIS[1:2], files=("viz/b.html",))
    import os
    os.utime(sd / "viz" / "b.html", (1.0, 1.0))
    return ws, sd


def test_ok_but_unchanged_stale_figure_is_not_laundered(tmp_path, monkeypatch):
    ws, sd = _stale_b(tmp_path, monkeypatch)
    calls = []
    res = D.advance_cycle(ws, "inv", autonomous=True, render_hook=_hook("nothing", calls))
    assert calls == ["b"] and [r["status"] for r in res["runs"]] == ["failed"]
    assert (sd / "viz" / "b.html").read_text() == "<html>old</html>"
    assert read_meta(sd / "viz" / "b.html") is None
    assert [i["kind"] for i in _items(ws)] == ["viz_stale"]


def test_prestamped_item_not_redispatched_by_advance(tmp_path, monkeypatch):
    ws, sd = _ws(tmp_path, monkeypatch, VIS[1:2], files=("viz/b.html",))
    import os
    os.utime(sd / "viz" / "b.html", (1.0, 1.0))
    # stamped against latest run but rendered_at predates completion -> still stale
    stamp_meta(sd / "viz" / "b.html", source_run_id="r1", generation_id=None,
               rendered_at=1.0, command="x")
    assert [i["kind"] for i in _items(ws)] == ["viz_stale"]
    calls = []
    res = D.advance_cycle(ws, "inv", autonomous=True, render_hook=_hook("stamp", calls))
    assert calls == [] and res["runs"] == []


def test_hook_exception_is_failed_gated_no_figure(tmp_path, monkeypatch):
    ws, sd = _ws(tmp_path, monkeypatch, VIS[:1])
    calls = []

    def boom(sdir, entry, latest):
        calls.append(entry["name"])
        raise RuntimeError("renderer crashed")
    res = D.advance_cycle(ws, "inv", autonomous=True, render_hook=boom)
    assert calls == ["a"]                       # gated, not retried
    assert [r["status"] for r in res["runs"]] == ["failed"]
    assert "RuntimeError" in res["runs"][0]["outcome"]
    assert not (sd / "viz" / "a.html").exists()


def test_declare_refuses_healthy_entry(tmp_path, monkeypatch):
    ws, sd = _ws(tmp_path, monkeypatch, [{"name": "h", "address": "local:p.H"}])
    with pytest.raises(ValueError, match="not a broken stub"):
        D.declare_viz(ws, "inv", "fig:s5/h", {"address": "local:p.Z"}, "why")
    assert yaml.safe_load((sd / "study.yaml").read_text())["visualizations"][0]["address"] == "local:p.H"


def _pin_hook(pin, calls):
    def hook(sdir, entry, latest):
        calls.append(entry["name"])
        out = f"viz/{entry['name']}.html"
        (sdir / out).write_text("<html>new</html>")
        stamp_meta(sdir / out, source_run_id=pin, generation_id=None,
                   rendered_at=time.time(), command="fake")
        return {"ok": True, "chart": out, "source_run_id": pin, "detail": "rendered"}
    return hook


def test_source_run_pin_is_kept_and_discharged_by_advance(tmp_path, monkeypatch):
    vis = [{"name": "b", "address": "local:x.B", "source_run": "rX"}]
    ws, sd = _ws(tmp_path, monkeypatch, vis, files=("viz/b.html",))   # latest = r1 != rX
    import os
    os.utime(sd / "viz" / "b.html", (1.0, 1.0))
    assert [i["kind"] for i in _items(ws)] == ["viz_stale"]
    calls = []
    D.advance_cycle(ws, "inv", autonomous=True, render_hook=_pin_hook("rX", calls))
    assert calls == ["b"]
    assert read_meta(sd / "viz" / "b.html")["source_run_id"] == "rX"   # pin not overwritten
    assert _items(ws) == []                                            # discharged: fresh
    calls2 = []
    D.advance_cycle(ws, "inv", autonomous=True, render_hook=_pin_hook("rX", calls2))
    assert calls2 == []                                                # not re-dispatched
