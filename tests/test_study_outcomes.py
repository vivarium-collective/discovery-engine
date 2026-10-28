from discovery_engine import study_outcomes as so


def test_canonical_prefers_flag_over_position():
    spec = {"runs": [
        {"name": "a", "status": "completed", "canonical": True},
        {"name": "b", "status": "completed"},
    ]}
    assert so.canonical_run(spec)["name"] == "a"


def test_canonical_falls_back_to_newest_completed():
    spec = {"runs": [
        {"name": "old", "status": "completed", "timestamp": "2026-01-01T00:00:00Z"},
        {"name": "new", "status": "completed", "timestamp": "2026-02-01T00:00:00Z"},
        {"name": "running", "status": "running", "timestamp": "2026-03-01T00:00:00Z"},
    ]}
    assert so.canonical_run(spec)["name"] == "new"


def test_canonical_last_resort_is_last_entry():
    spec = {"runs": [{"name": "a", "status": "running"}, {"name": "b", "status": "running"}]}
    assert so.canonical_run(spec)["name"] == "b"


def test_canonical_none_when_no_runs():
    assert so.canonical_run({"runs": []}) is None
    assert so.canonical_run({}) is None


# ---------------------------------------------------------------------------
# record_runs tests
# ---------------------------------------------------------------------------
from pathlib import Path
from discovery_engine import study_io, run_registry


def _study(tmp_path: Path, spec: dict) -> Path:
    d = tmp_path / "studies" / "s1"; d.mkdir(parents=True)
    study_io.save_yaml_atomic(d / "study.yaml", spec)
    return d


def test_record_adds_missing_run_and_preserves_authored(tmp_path: Path):
    d = _study(tmp_path, {"name": "s1", "runs": [
        {"name": "r1", "status": "completed",
         "outcomes": {"t1": {"result": "PASS", "detail": "authored"}},
         "description": "hand-written"},
    ]})
    db = d / "runs.db"
    # r1 already authored; r2 only in the DB
    run_registry.register_run(db, "r1", spec_id="s1", status="completed",
                              started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z")
    run_registry.register_run(db, "r2", spec_id="s1", status="completed",
                              started_at="2026-01-02T00:00:00Z", completed_at="2026-01-02T00:01:00Z",
                              emitter_path="out/r2")
    summary = so.record_runs(d)
    spec = study_io.load_yaml_mapping(d / "study.yaml")
    by = {r["name"]: r for r in spec["runs"]}
    assert summary == {"added": 1, "updated": 1}
    # authored fields preserved on r1
    assert by["r1"]["outcomes"]["t1"]["result"] == "PASS"
    assert by["r1"]["description"] == "hand-written"
    # r2 recorded with mechanical fields
    assert by["r2"]["status"] == "completed"
    assert by["r2"]["emitter"]["kind"] in {"sqlite", "parquet", "xarray", "unknown"}


def test_canonical_missing_timestamp_not_preferred(tmp_path: Path):
    """Regression: a completed run with no/null timestamp must not beat a real ISO date."""
    spec = {"runs": [
        {"name": "dated", "status": "completed", "timestamp": "2026-02-01T00:00:00Z"},
        {"name": "no_key", "status": "completed"},            # no timestamp key
        {"name": "null_ts", "status": "completed", "timestamp": None},  # explicit null
    ]}
    assert so.canonical_run(spec)["name"] == "dated"


def test_record_is_idempotent(tmp_path: Path):
    d = _study(tmp_path, {"name": "s1", "runs": []})
    db = d / "runs.db"
    run_registry.register_run(db, "r1", spec_id="s1", status="completed",
                              started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z")
    so.record_runs(d)
    first = (d / "study.yaml").read_text()
    so.record_runs(d)
    assert (d / "study.yaml").read_text() == first


# ---------------------------------------------------------------------------
# reproducible-rerun-spine Task 5 fix — provenance_status/env_id must sync
# from the DB row into the study.yaml run entry. The dashboard's runs.db
# (vivarium_workbench.lib.composite_runs) and pbg's own writer share the
# SAME sqlite file per study; `run_registry.list_runs` already does
# `SELECT *`, so a vwb-migrated DB's provenance_status/env_id columns come
# through in `db_row` for free — the gap was `_mechanical_record`/
# `_MECHANICAL` silently dropping them before they reach study.yaml. Without
# this, needs_attention's env_stale/nondeterministic signals can never fire
# on a real workspace (they read study.yaml `runs:`, never the DB).
# ---------------------------------------------------------------------------

def _add_provenance_columns(db: Path, run_id: str, *, provenance_status=None, env_id=None):
    """Simulate a runs.db already migrated by vwb's composite_runs.py (which
    ALTERs in these nullable columns) — pbg's own DDL doesn't create them."""
    import sqlite3
    conn = sqlite3.connect(db)
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(runs_meta)")}
        if "provenance_status" not in have:
            conn.execute("ALTER TABLE runs_meta ADD COLUMN provenance_status TEXT")
        if "env_id" not in have:
            conn.execute("ALTER TABLE runs_meta ADD COLUMN env_id TEXT")
        conn.execute("UPDATE runs_meta SET provenance_status=?, env_id=? WHERE run_id=?",
                     (provenance_status, env_id, run_id))
        conn.commit()
    finally:
        conn.close()


def test_record_syncs_provenance_status_and_env_id(tmp_path: Path):
    d = _study(tmp_path, {"name": "s1", "runs": []})
    db = d / "runs.db"
    run_registry.register_run(db, "r1", spec_id="s1", status="completed",
                              started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z")
    _add_provenance_columns(db, "r1", provenance_status="env_stale", env_id="env-b-hash")

    summary = so.record_runs(d)
    assert summary == {"added": 1, "updated": 0}
    spec = study_io.load_yaml_mapping(d / "study.yaml")
    by = {r["name"]: r for r in spec["runs"]}
    assert by["r1"]["provenance_status"] == "env_stale"
    assert by["r1"]["env_id"] == "env-b-hash"


def test_record_updates_provenance_status_on_existing_run(tmp_path: Path):
    """An already-synced run whose provenance_status changes (e.g. a later
    verify_reproduction/env-drift check stamps it after the first sync) must
    be picked up on the next record_runs call."""
    d = _study(tmp_path, {"name": "s1", "runs": [
        {"name": "r1", "status": "completed"},
    ]})
    db = d / "runs.db"
    run_registry.register_run(db, "r1", spec_id="s1", status="completed",
                              started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z")
    _add_provenance_columns(db, "r1", provenance_status="nondeterministic", env_id="env-a-hash")

    summary = so.record_runs(d)
    assert summary == {"added": 0, "updated": 1}
    spec = study_io.load_yaml_mapping(d / "study.yaml")
    by = {r["name"]: r for r in spec["runs"]}
    assert by["r1"]["provenance_status"] == "nondeterministic"
    assert by["r1"]["env_id"] == "env-a-hash"


def test_record_omits_provenance_status_when_db_lacks_it(tmp_path: Path):
    """Old workspaces / DBs that predate the provenance_status/env_id
    columns must not break — best-effort, nullable: the fields are simply
    absent from the synced run entry rather than written as null/None."""
    d = _study(tmp_path, {"name": "s1", "runs": []})
    db = d / "runs.db"
    run_registry.register_run(db, "r1", spec_id="s1", status="completed",
                              started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z")
    so.record_runs(d)
    spec = study_io.load_yaml_mapping(d / "study.yaml")
    by = {r["name"]: r for r in spec["runs"]}
    assert "provenance_status" not in by["r1"]
    assert "env_id" not in by["r1"]


# NOTE: test_env_stale_synced_run_surfaces_needs_attention moved to the workbench
# in Phase 2.1k batch 2 (needs_attention → vivarium_workbench/lib) — see
# vivarium_workbench/tests/test_needs_attention_env_stale_sync.py. It exercised
# needs_attention.scan_investigation (now a workbench module); study_outcomes
# stays here.


def test_record_preserves_comments(tmp_path: Path):
    """record_runs must not strip YAML comments or reflow hand-authored content."""
    d = tmp_path / "studies" / "s_comments"
    d.mkdir(parents=True)

    # Write study.yaml as RAW TEXT so comments survive (save_yaml_atomic would strip them)
    raw_yaml = """\
# top-of-file comment: research context for this study
name: s_comments  # inline comment on the name line
description: a study with authored comments

# section header for runs
runs:
  - name: r_existing  # inline comment on run entry
    status: running
    # a comment inside the run entry
    outcomes:
      test_a:
        result: PASS  # authored PASS verdict
        detail: hand-authored detail
  # placeholder: future_run goes here

# end-of-file notes section
"""
    (d / "study.yaml").write_text(raw_yaml)

    db = d / "runs.db"
    run_registry.register_run(
        db, "r_new", spec_id="s_comments", status="completed",
        started_at="2026-01-03T00:00:00Z", completed_at="2026-01-03T00:01:00Z",
    )

    result = so.record_runs(d)
    assert result["added"] == 1

    after_text = (d / "study.yaml").read_text()

    # (a) Every specific comment substring must still be present
    assert "# top-of-file comment: research context for this study" in after_text
    assert "# inline comment on the name line" in after_text
    assert "# a comment inside the run entry" in after_text
    assert "# authored PASS verdict" in after_text
    assert "# end-of-file notes section" in after_text

    # (b) New run name appears in the file
    assert "r_new" in after_text

    # (c) Authored outcomes value is unchanged
    spec = study_io.load_yaml_mapping(d / "study.yaml")
    by = {r["name"]: r for r in spec["runs"]}
    assert by["r_existing"]["outcomes"]["test_a"]["result"] == "PASS"
    assert by["r_existing"]["outcomes"]["test_a"]["detail"] == "hand-authored detail"

    # (d) Second call is a byte-identical no-op
    result2 = so.record_runs(d)
    assert result2 == {"added": 0, "updated": 0}
    assert (d / "study.yaml").read_text() == after_text
