from discovery_engine.viz_freshness import (
    normalize_entry, classify_viz, chart_freshness, stamp_meta, read_meta)
import os

LATEST = {"run_id": "r2", "completed_at": 1000.0}


def row(exists=True, meta=None, mtime=None):
    return {"path": "viz/x.html", "exists": exists, "meta": meta,
            "mtime": mtime, "content_hash": None}


def test_s5_stub_is_broken():
    s = normalize_entry({"name": "eigen-catastrophe", "title": "t",
                         "rendered": "workspace/studies/s5/viz/eigen-catastrophe.html"})
    assert s["kind"] == "broken_stub"
    assert s["name"] == "eigen-catastrophe"
    assert s["requires"] == [] and s["covers"] == [] and s["source_run"] is None


def test_local_is_class():
    s = normalize_entry({"name": "her", "address": "local:S5HeritabilityViz"})
    assert s["kind"] == "class" and s["output"] == "viz/her.html"
    assert normalize_entry({"name": "d", "address": "dashboard:X"})["kind"] == "class"


def test_figure_kinds():
    s = normalize_entry({"name": "a", "address": "html:viz/a.html"})
    assert (s["kind"], s["output"]) == ("figure", "viz/a.html")
    s = normalize_entry({"name": "b", "address": "svg:charts/b.svg"})
    assert (s["kind"], s["output"]) == ("figure", "charts/b.svg")
    s = normalize_entry({"name": "c", "chart": "charts/c.png"})
    assert (s["kind"], s["output"]) == ("figure", "charts/c.png")


def test_command():
    s = normalize_entry({"name": "c", "chart": "charts/c.svg", "render": "python r.py {chart}",
                         "requires": ["x"], "source_run": "r1", "covers": ["y"]})
    assert s["kind"] == "command" and s["output"] == "charts/c.svg"
    assert s["render"] and s["requires"] == ["x"] and s["source_run"] == "r1"
    assert s["covers"] == ["y"]
    s = normalize_entry({"name": "d", "address": "html:viz/d.html", "render": "go"})
    assert s["kind"] == "command" and s["output"] == "viz/d.html"


def _spec(**kw):
    return normalize_entry({"name": "x", "address": "html:viz/x.html", **kw})


def test_broken_always():
    s = normalize_entry({"name": "x"})
    for r in (row(False), row(True), row(True, meta={"source_run_id": "r2"})):
        assert classify_viz(s, r, LATEST) == "broken_stub"


def test_unrendered():
    assert classify_viz(_spec(), row(False), LATEST) == "unrendered"


def test_no_meta_mtime():
    assert classify_viz(_spec(), row(mtime=1500.0), LATEST) == "fresh"
    assert classify_viz(_spec(), row(mtime=500.0), LATEST) == "stale"
    assert classify_viz(_spec(), row(mtime=1.0), None) == "fresh"


def test_meta_run_match():
    ok = {"source_run_id": "r2", "rendered_at": 1100.0}
    assert classify_viz(_spec(), row(meta=ok), LATEST) == "fresh"
    assert classify_viz(_spec(), row(meta={**ok, "source_run_id": "r1"}), LATEST) == "stale"
    assert classify_viz(_spec(), row(meta={**ok, "rendered_at": 900.0}), LATEST) == "stale"


def test_pin():
    m = {"source_run_id": "r1", "rendered_at": 1.0}
    assert classify_viz(_spec(source_run="r1"), row(meta=m), LATEST) == "fresh"
    assert classify_viz(_spec(source_run="r9"), row(meta=m), LATEST) == "stale"


def test_mv_anchor():
    m = {"source_run_id": "old", "rendered_at": 1.0, "model_version": "mv:a"}
    s = _spec(requires=[])
    assert classify_viz(s, row(meta=m), LATEST, mv="mv:a") == "fresh"
    assert classify_viz(s, row(meta=m), LATEST, mv="mv:b") == "stale"
    # requires non-empty -> run rule applies
    assert classify_viz(_spec(requires=["o"]), row(meta=m), LATEST, mv="mv:a") == "stale"


def test_chart_freshness_spec_and_mtime(tmp_path):
    (tmp_path / "viz").mkdir()
    f = tmp_path / "viz" / "x.html"
    assert chart_freshness(tmp_path, {"address": "html:viz/x.html"}, LATEST) == "unrendered"
    f.write_text("hi")
    os.utime(f, (1500, 1500))
    assert chart_freshness(tmp_path, {"address": "html:viz/x.html"}, LATEST) == "fresh"
    os.utime(f, (500, 500))
    assert chart_freshness(tmp_path, _spec(), LATEST) == "stale"


def test_stamp_model_version(tmp_path):
    c = tmp_path / "c.svg"; c.write_text("x")
    stamp_meta(c, source_run_id=None, generation_id=None, rendered_at=1.0,
               command="c", model_version="mv:a")
    assert read_meta(c)["model_version"] == "mv:a"
    stamp_meta(c, source_run_id=None, generation_id=None, rendered_at=1.0, command="c")
    assert "model_version" not in read_meta(c)
