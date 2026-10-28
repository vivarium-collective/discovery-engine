from pathlib import Path
from discovery_engine import run_registry


def test_list_runs_returns_rows_newest_first(tmp_path: Path):
    db = tmp_path / "runs.db"
    run_registry.register_run(db, "r1", spec_id="s", status="completed",
                              started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z")
    run_registry.register_run(db, "r2", spec_id="s", status="completed",
                              started_at="2026-01-02T00:00:00Z", completed_at="2026-01-02T00:01:00Z")
    rows = run_registry.list_runs(db)
    assert [r["run_id"] for r in rows] == ["r2", "r1"]
    assert rows[0]["status"] == "completed"


def test_list_runs_missing_db_is_empty(tmp_path: Path):
    assert run_registry.list_runs(tmp_path / "nope.db") == []
