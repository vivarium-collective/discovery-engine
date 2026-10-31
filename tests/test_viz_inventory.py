import json
from pathlib import Path

from discovery_engine import discover as D
from discovery_engine.investigation_import import studies_dir
from discovery_engine.validation import ValidationContext


def _mk(tmp_path):
    ws = tmp_path
    sd = studies_dir(ws) / "s1"
    (sd / "viz").mkdir(parents=True)
    (sd / "viz" / "a.html").write_text("<html>a</html>")
    (sd / "viz" / "a.html.meta.json").write_text(json.dumps({"source_run_id": "r1"}))
    (sd / "viz" / "orphan.html").write_text("<html>o</html>")
    studies = {"s1": {"visualizations": [{"name": "a", "address": "local:x.A"},
                                         {"name": "b", "address": "local:x.B"}]}}
    return ws, studies


def test_viz_inventory_rows_latest_orphans(tmp_path, monkeypatch):
    ws, studies = _mk(tmp_path)
    monkeypatch.setattr(D, "_load_runs_by_study",
                        lambda w, i, s: {"s1": [{"run_id": "r1", "completed_at": 5.0}]})
    inv = D.viz_inventory(ws, "inv", studies)["s1"]
    a, b = inv["a"], inv["b"]
    assert a["exists"] and a["meta"] == {"source_run_id": "r1"}
    assert a["mtime"] > 0 and a["content_hash"].startswith("sha256:")
    assert a["path"].endswith("viz/a.html")
    assert b["exists"] is False and b["meta"] is None and b["content_hash"] is None
    assert inv["_latest"] == {"run_id": "r1", "completed_at": 5.0}
    assert inv["_orphans"] == ["viz/orphan.html"]


def test_viz_inventory_no_runs(tmp_path):
    ws, studies = _mk(tmp_path)
    assert D.viz_inventory(ws, "inv", studies)["s1"]["_latest"] is None


def test_context_default_and_run_cycle_hermetic():
    assert ValidationContext().viz_state == {}
    r = D.run_cycle({"slug": "i", "studies": []}, {})
    assert "validations" in r


def test_cycle_with_evidence_threads_viz_state(tmp_path, monkeypatch):
    ws, studies = _mk(tmp_path)
    seen = {}
    monkeypatch.setattr(D, "run_cycle", lambda *a, **k: seen.update(k) or {})
    D._cycle_with_evidence(ws, "inv", {"slug": "inv", "studies": ["s1"]}, studies)
    assert seen["viz_state"]["s1"]["a"]["exists"] is True
    assert "viz_catalog" in seen
