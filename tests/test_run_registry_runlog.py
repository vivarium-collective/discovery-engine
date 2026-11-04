"""The engine resolves a study's registered runs from its per-study ``runs.db``.
The vivarium-workbench also keeps a workspace-wide append-only run log at
``.pbg/runs.jsonl`` (dashboard #612); a workspace whose runs are logged only
there used to be invisible to evidence reconciliation (``0 registered runs``
even though the run happened). These cover the run-log reader + its merge into
``_load_runs_by_study`` so run-log-registered runs become resolvable.
"""
import json
from pathlib import Path

from discovery_engine import run_registry
from discovery_engine.discover import _load_runs_by_study


def _write_runlog(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def test_read_runlog_folds_events_to_latest_per_run(tmp_path: Path):
    rl = tmp_path / ".pbg" / "runs.jsonl"
    _write_runlog(rl, [
        {"run_id": "r1", "study_slug": "s", "status": "running", "event": "start"},
        {"run_id": "r1", "study_slug": "s", "status": "completed", "event": "complete"},
        {"run_id": "r2", "study_slug": "s", "status": "running", "event": "start"},
    ])
    by_id = {r["run_id"]: r for r in run_registry.read_runlog(rl)}
    assert set(by_id) == {"r1", "r2"}
    assert by_id["r1"]["status"] == "completed"   # last event for a run_id wins
    assert by_id["r2"]["status"] == "running"


def test_read_runlog_missing_file_is_empty(tmp_path: Path):
    assert run_registry.read_runlog(tmp_path / ".pbg" / "runs.jsonl") == []


def test_read_runlog_tolerates_bad_and_blank_lines(tmp_path: Path):
    rl = tmp_path / "runs.jsonl"
    rl.write_text('not json\n\n{"run_id": "r1", "status": "completed", "study_slug": "s"}\n'
                  '{"no_run_id": true}\n')
    assert [r["run_id"] for r in run_registry.read_runlog(rl)] == ["r1"]


def test_runs_by_study_from_runlog_groups_and_filters(tmp_path: Path):
    rl = tmp_path / "runs.jsonl"
    _write_runlog(rl, [
        {"run_id": "a", "study_slug": "s1", "status": "completed"},
        {"run_id": "b", "study_slug": "s2", "status": "completed"},
        {"run_id": "c", "study_slug": "s1", "status": "running"},
    ])
    out = run_registry.runs_by_study_from_runlog(rl, {"s1"})
    assert set(out) == {"s1"}                       # s2 filtered out
    assert {r["run_id"] for r in out["s1"]} == {"a", "c"}


def test_load_runs_by_study_merges_runlog_only_runs(tmp_path: Path):
    """A study with a runs.db keeps it (authoritative); a study with runs only
    in .pbg/runs.jsonl is still resolvable; db wins on run_id conflict."""
    ws = tmp_path
    (ws / "studies" / "has-db").mkdir(parents=True)
    db = ws / "studies" / "has-db" / "runs.db"
    run_registry.register_run(db, "db1", spec_id="s", status="completed",
                              started_at="2026-01-01T00:00:00Z")
    _write_runlog(ws / ".pbg" / "runs.jsonl", [
        {"run_id": "db1", "study_slug": "has-db", "status": "running"},   # conflict: db wins
        {"run_id": "log1", "study_slug": "has-db", "status": "completed"},
        {"run_id": "log2", "study_slug": "log-only", "status": "completed"},
    ])
    out = _load_runs_by_study(ws, "inv", {"has-db": {}, "log-only": {}})
    has_db = {r["run_id"]: r for r in out["has-db"]}
    assert set(has_db) == {"db1", "log1"}
    assert has_db["db1"]["status"] == "completed"   # runs.db value, not the runlog "running"
    assert [r["run_id"] for r in out["log-only"]] == ["log2"]
