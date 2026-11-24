import json
import sqlite3

from discovery_engine.study.run_registry import (
    RUNS_META_DDL,
    build_run_manifest,
    get_run_params,
    latest_run,
    register_run,
)


def _db(tmp):
    p = tmp / "runs.db"; conn = sqlite3.connect(p); conn.executescript(RUNS_META_DDL)
    conn.execute("INSERT INTO runs_meta(run_id,spec_id,started_at,completed_at,status,emitter_path,generation_id)"
                 " VALUES(?,?,?,?,?,?,?)", ("old","s",1.0,10.0,"complete","out/old","g0"))
    conn.execute("INSERT INTO runs_meta(run_id,spec_id,started_at,completed_at,status,emitter_path,generation_id)"
                 " VALUES(?,?,?,?,?,?,?)", ("new","s",2.0,20.0,"complete","out/new","g1"))
    conn.commit(); conn.close(); return p

def test_latest_run_picks_newest_completed(tmp_path):
    db = _db(tmp_path)
    lr = latest_run(db)
    assert lr["run_id"] == "new"
    assert lr["emitter_path"] == "out/new"
    assert lr["generation_id"] == "g1"
    assert lr["completed_at"] == 20.0

def test_latest_run_none_when_empty(tmp_path):
    p = tmp_path / "empty.db"; sqlite3.connect(p).executescript(RUNS_META_DDL)
    assert latest_run(p) is None

def test_latest_run_missing_db(tmp_path):
    assert latest_run(tmp_path / "nope.db") is None

def test_latest_run_tolerates_db_without_emitter_path(tmp_path):
    # An older runs.db missing emitter_path/generation_id must still work.
    p = tmp_path / "legacy.db"; conn = sqlite3.connect(p)
    conn.executescript("CREATE TABLE runs_meta(run_id TEXT PRIMARY KEY, spec_id TEXT NOT NULL,"
                       " started_at REAL NOT NULL, completed_at REAL, status TEXT NOT NULL);")
    conn.execute("INSERT INTO runs_meta VALUES('r','s',1.0,5.0,'complete')")
    conn.commit(); conn.close()
    lr = latest_run(p)
    assert lr["run_id"] == "r"
    assert "emitter_path" not in lr  # column absent → key omitted


# --- register_run / get_run_params (run-config provenance slot) ------------

def test_register_run_creates_db_and_records_params(tmp_path):
    db = tmp_path / "sub" / "runs.db"  # parent dir does not exist yet
    cfg = {"perturbations": {"TU00259[c]": 1.7e-3}, "seed": 1, "generations": 8}
    register_run(db, "exp_a", spec_id="dnaa-1", status="complete", params=cfg)
    assert db.is_file()
    got = get_run_params(db, "exp_a")
    assert got == cfg
    lr = latest_run(db)
    assert lr["run_id"] == "exp_a"
    assert lr["status"] == "complete"


def test_register_run_spec_defaults_to_run_id(tmp_path):
    db = tmp_path / "runs.db"
    register_run(db, "exp_b", params={"seed": 0})
    conn = sqlite3.connect(db)
    row = conn.execute(
        "SELECT spec_id, status FROM runs_meta WHERE run_id=?", ("exp_b",)
    ).fetchone()
    conn.close()
    assert row[0] == "exp_b"      # spec_id defaulted to run_id
    assert row[1] == "recorded"   # status defaulted


def test_register_run_upsert_preserves_params_when_omitted(tmp_path):
    db = tmp_path / "runs.db"
    cfg = {"perturbations": {"TU00259[c]": 1.7e-3}, "seed": 2}
    # up-front: running + full config
    register_run(db, "exp_c", status="running", params=cfg)
    # completion: status only, no params — must NOT wipe the config
    register_run(db, "exp_c", status="complete")
    assert get_run_params(db, "exp_c") == cfg
    assert latest_run(db)["status"] == "complete"


def test_register_run_params_can_be_replaced(tmp_path):
    db = tmp_path / "runs.db"
    register_run(db, "exp_d", params={"seed": 1})
    register_run(db, "exp_d", params={"seed": 9, "extra": True})
    assert get_run_params(db, "exp_d") == {"seed": 9, "extra": True}


def test_get_run_params_missing_run_or_db(tmp_path):
    assert get_run_params(tmp_path / "nope.db", "x") is None
    db = tmp_path / "runs.db"
    sqlite3.connect(db).executescript(RUNS_META_DDL)
    assert get_run_params(db, "absent") is None


# --- manifest_json (reproducible-rerun-spine Task 1: unify writers) --------

def _git(cwd, *args):
    import subprocess
    subprocess.run(["git", "-C", str(cwd), *args], check=True,
                   capture_output=True, text=True)


def test_capture_environment_records_commit_and_env_id(tmp_path):
    from discovery_engine.study.run_registry import capture_environment
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "f.txt").write_text("x")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    env = capture_environment(tmp_path)
    assert env["commit"] and len(env["commit"]) == 40
    assert env["dirty"] is False
    assert env["env_id"] and isinstance(env["env_id"], str)
    # a new uncommitted change flips dirty and so changes env_id
    (tmp_path / "f.txt").write_text("y")
    env2 = capture_environment(tmp_path)
    assert env2["dirty"] is True
    assert env2["env_id"] != env["env_id"]


def test_capture_environment_never_raises_without_git():
    from discovery_engine.study.run_registry import capture_environment
    env = capture_environment(None)          # no ws_root at all
    assert isinstance(env, dict) and env["commit"] is None and env["env_id"]
    assert isinstance(env["packages"], dict)  # framework pkgs still recorded


def test_register_run_derives_ws_root_and_records_commit(tmp_path):
    # The earn-path gap: a caller that passes only the runs.db path (no ws_root)
    # must still capture the commit — register_run walks up to the workspace root.
    from discovery_engine.study.run_registry import latest_run, register_run
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "workspace.yaml").write_text("name: ws\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "init")
    db = tmp_path / "studies" / "s" / "runs.db"
    register_run(db, "baseline", spec_id="x", status="completed", n_steps=14)  # no ws_root
    m = json.loads(latest_run(db)["manifest_json"])
    assert m["env"]["commit"] and len(m["env"]["commit"]) == 40


def test_register_run_writes_env_id_column_when_present(tmp_path):
    from discovery_engine.study.run_registry import register_run
    db = tmp_path / "runs.db"
    # a workbench-shape runs.db that HAS an env_id column
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE runs_meta (run_id TEXT PRIMARY KEY, spec_id TEXT NOT NULL,"
        " started_at REAL NOT NULL, completed_at REAL, status TEXT NOT NULL,"
        " n_steps INTEGER, manifest_json TEXT, env_id TEXT);")
    conn.commit(); conn.close()
    register_run(db, "r1", spec_id="x", params={"seed": 1}, n_steps=3)
    conn = sqlite3.connect(db)
    val = conn.execute("SELECT env_id FROM runs_meta WHERE run_id='r1'").fetchone()[0]
    conn.close()
    assert val and isinstance(val, str)


def test_register_run_stamps_manifest_v2(tmp_path):
    # Bespoke `run-script` runs go through register_run, not the dashboard's
    # composite_runs.save_metadata — this is the "second writer" the
    # reproducible-rerun-spine plan unifies onto the same manifest schema so
    # those runs are replayable too.
    db = tmp_path / "runs.db"
    register_run(db, "run_a", spec_id="ecoli", params={"seed": 0}, n_steps=10)
    row = latest_run(db)
    m = json.loads(row["manifest_json"])
    assert m["version"] == 2
    assert m["params"]["seed"] == 0
    assert m["n_steps"] == 10
    assert m["spec_id"] == "ecoli"
    for k in ("seed", "fingerprint_fields", "result_fingerprint"):
        assert k in m and m[k] is None
    # env is now captured (not a null placeholder): a dict with a stable env_id
    # and the installed framework package versions.
    assert isinstance(m["env"], dict)
    assert m["env"]["env_id"] and isinstance(m["env"]["env_id"], str)
    assert isinstance(m["env"]["packages"], dict)


def test_build_run_manifest_schema_matches_documented_v2_key_set():
    # Drift guard (code review round 1): the two writers' manifests are
    # documented as "one shared manifest schema" — pin the exact top-level
    # key set here so any future divergence (a key added to one writer but
    # not the other) fails this test, not just a comment.
    m = build_run_manifest(spec_id="s", params={"seed": 0}, n_steps=1)
    assert set(m) == {
        "version", "spec_id", "params", "n_steps", "emitter", "emit_paths",
        "runtime", "origin", "study", "pkg", "generation_id", "code_version",
        "env", "seed", "fingerprint_fields", "result_fingerprint",
    }


def test_build_run_manifest_code_version_best_effort_null_without_ws_root():
    # Parity with composite_runs.build_run_manifest: code_version degrades
    # to {git_sha: None, package: None} rather than being omitted, and never
    # raises when ws_root/pkg aren't resolvable.
    m = build_run_manifest(spec_id="s", params={}, n_steps=1)
    assert m["code_version"] == {"git_sha": None, "package": None}


def test_register_run_manifest_null_on_legacy_db_without_column(tmp_path):
    # A pre-existing runs.db created before manifest_json existed must not
    # break — register_run degrades gracefully (no manifest_json written),
    # rather than raising "no such column".
    db = tmp_path / "legacy.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        "CREATE TABLE runs_meta (run_id TEXT PRIMARY KEY, spec_id TEXT NOT NULL,"
        " started_at REAL NOT NULL, completed_at REAL, status TEXT NOT NULL);"
    )
    conn.commit(); conn.close()
    register_run(db, "run_b", spec_id="ecoli", params={"seed": 1}, n_steps=5)
    lr = latest_run(db)
    assert lr["run_id"] == "run_b"
    assert "manifest_json" not in lr  # column absent → key omitted, no crash


def test_get_run_params_db_without_params_column(tmp_path):
    p = tmp_path / "legacy.db"
    conn = sqlite3.connect(p)
    conn.executescript("CREATE TABLE runs_meta(run_id TEXT PRIMARY KEY,"
                       " spec_id TEXT NOT NULL, started_at REAL NOT NULL,"
                       " completed_at REAL, status TEXT NOT NULL);")
    conn.execute("INSERT INTO runs_meta VALUES('r','s',1.0,5.0,'complete')")
    conn.commit(); conn.close()
    assert get_run_params(p, "r") is None  # column absent → None, no crash
