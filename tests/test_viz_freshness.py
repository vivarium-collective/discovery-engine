import json
from pathlib import Path
from discovery_engine.viz_freshness import (
    stamp_meta, read_meta, chart_freshness, manifest_diff,
)

def _study(tmp):
    d = tmp / "studies" / "s1"; (d / "charts").mkdir(parents=True)
    return d

def test_stamp_and_read_roundtrip(tmp_path):
    d = _study(tmp_path); chart = d / "charts" / "c.svg"; chart.write_text("<svg/>")
    stamp_meta(chart, source_run_id="r1", generation_id="g1",
               rendered_at=100.0, command="cmd")
    m = read_meta(chart)
    assert m["source_run_id"] == "r1"
    assert m["generation_id"] == "g1"
    assert m["rendered_at"] == 100.0
    assert m["content_hash"].startswith("sha256:")

def test_freshness_fresh(tmp_path):
    d = _study(tmp_path); chart = d / "charts" / "c.svg"; chart.write_text("x")
    stamp_meta(chart, source_run_id="r2", generation_id=None,
               rendered_at=200.0, command="cmd")
    entry = {"name": "v", "chart": "charts/c.svg", "render": "cmd"}
    latest = {"run_id": "r2", "completed_at": 150.0}
    assert chart_freshness(d, entry, latest) == "fresh"

def test_freshness_stale_wrong_run(tmp_path):
    d = _study(tmp_path); chart = d / "charts" / "c.svg"; chart.write_text("x")
    stamp_meta(chart, source_run_id="OLD", generation_id=None,
               rendered_at=200.0, command="cmd")
    entry = {"name": "v", "chart": "charts/c.svg", "render": "cmd"}
    assert chart_freshness(d, entry, {"run_id": "r2", "completed_at": 150.0}) == "stale"

def test_freshness_stale_rendered_before_run(tmp_path):
    d = _study(tmp_path); chart = d / "charts" / "c.svg"; chart.write_text("x")
    stamp_meta(chart, source_run_id="r2", generation_id=None,
               rendered_at=100.0, command="cmd")
    entry = {"name": "v", "chart": "charts/c.svg", "render": "cmd"}
    assert chart_freshness(d, entry, {"run_id": "r2", "completed_at": 150.0}) == "stale"

def test_freshness_unrendered(tmp_path):
    d = _study(tmp_path)
    entry = {"name": "v", "chart": "charts/missing.svg", "render": "cmd"}
    assert chart_freshness(d, entry, {"run_id": "r2", "completed_at": 1.0}) == "unrendered"

def test_manifest_diff_flags_orphans(tmp_path):
    d = _study(tmp_path)
    (d / "charts" / "tracked.svg").write_text("x")
    (d / "charts" / "orphan.svg").write_text("x")
    entries = [{"name": "v", "chart": "charts/tracked.svg", "render": "cmd"}]
    diff = manifest_diff(d, entries)
    assert "charts/orphan.svg" in diff["untracked"]
    assert "charts/tracked.svg" not in diff["untracked"]


def test_freshness_pinned_source_run_fresh(tmp_path):
    d = _study(tmp_path); chart = d / "charts" / "c.svg"; chart.write_text("x")
    stamp_meta(chart, source_run_id="runA", generation_id=None,
               rendered_at=10.0, command="cmd")
    entry = {"name": "v", "chart": "charts/c.svg", "render": "cmd", "source_run": "runA"}
    # latest is a DIFFERENT, newer run — the pin wins.
    assert chart_freshness(d, entry, {"run_id": "runB", "completed_at": 999.0}) == "fresh"


def test_freshness_pinned_source_run_stale(tmp_path):
    d = _study(tmp_path); chart = d / "charts" / "c.svg"; chart.write_text("x")
    stamp_meta(chart, source_run_id="OTHER", generation_id=None,
               rendered_at=10.0, command="cmd")
    entry = {"name": "v", "chart": "charts/c.svg", "render": "cmd", "source_run": "runA"}
    assert chart_freshness(d, entry, {"run_id": "runA", "completed_at": 1.0}) == "stale"
