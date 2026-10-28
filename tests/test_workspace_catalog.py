"""Tests for discovery_engine.workspace_catalog."""
from __future__ import annotations
import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest


@pytest.fixture
def pbg_home(tmp_path, monkeypatch):
    home = tmp_path / "pbg-home"
    monkeypatch.setenv("PBG_HOME", str(home))
    return home


@pytest.fixture
def workspace_dir(tmp_path):
    """Create a minimal workspace directory with workspace.yaml."""
    ws = tmp_path / "my-workspace"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(
        "name: my-workspace\npackage: pbg_myworkspace\n"
    )
    return ws


def test_list_returns_empty_when_no_catalog(pbg_home):
    from discovery_engine.workspace_catalog import list_workspaces
    assert list_workspaces() == []


def test_add_inserts_entry(pbg_home, workspace_dir):
    from discovery_engine.workspace_catalog import add, list_workspaces
    entry = add(workspace_dir)
    assert entry["name"] == "my-workspace"
    assert entry["package"] == "pbg_myworkspace"
    assert entry["path"] == str(workspace_dir.resolve())
    assert "added_at" in entry
    assert list_workspaces() == [entry]


def test_add_is_idempotent_by_path(pbg_home, workspace_dir):
    from discovery_engine.workspace_catalog import add, list_workspaces
    e1 = add(workspace_dir)
    e2 = add(workspace_dir)
    assert e1 == e2
    assert len(list_workspaces()) == 1


def test_add_rejects_non_workspace(pbg_home, tmp_path):
    from discovery_engine.workspace_catalog import add
    bogus = tmp_path / "not-a-workspace"
    bogus.mkdir()
    with pytest.raises(ValueError, match="no workspace.yaml"):
        add(bogus)


def test_add_explicit_name_overrides_yaml(pbg_home, workspace_dir):
    from discovery_engine.workspace_catalog import add
    entry = add(workspace_dir, name="explicit", package="pbg_explicit")
    assert entry["name"] == "explicit"
    assert entry["package"] == "pbg_explicit"


def test_add_reads_package_path_key(pbg_home, tmp_path):
    """A workspace.yaml using the canonical top-level `package_path` key
    (what the schema defines and the scaffolder writes) must populate the
    catalog entry's package — not store null. Regression for #356."""
    from discovery_engine.workspace_catalog import add
    ws = tmp_path / "pp-workspace"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(
        "name: pp-workspace\npackage_path: viva_ppworkspace\n"
    )
    entry = add(ws)
    assert entry["package"] == "viva_ppworkspace"


def test_add_prefers_package_path_over_legacy_package(pbg_home, tmp_path):
    """When both keys are present, the canonical `package_path` wins."""
    from discovery_engine.workspace_catalog import add
    ws = tmp_path / "both-workspace"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(
        "name: both-workspace\npackage_path: viva_new\npackage: pbg_old\n"
    )
    entry = add(ws)
    assert entry["package"] == "viva_new"


def test_forget_removes_entry(pbg_home, workspace_dir):
    from discovery_engine.workspace_catalog import add, forget, list_workspaces
    add(workspace_dir)
    assert forget(workspace_dir) is True
    assert list_workspaces() == []


def test_forget_missing_returns_false(pbg_home, tmp_path):
    from discovery_engine.workspace_catalog import forget
    # path that was never added (and need not exist on disk)
    assert forget(tmp_path / "never-added") is False


def test_register_server_writes_file(pbg_home, workspace_dir):
    from discovery_engine.workspace_catalog import register_server
    fpath = register_server(
        name="my-workspace", path=workspace_dir,
        pid=os.getpid(), port=8731,
        url="http://127.0.0.1:8731",
    )
    assert fpath.exists()
    data = json.loads(fpath.read_text())
    assert data["name"] == "my-workspace"
    assert data["path"] == str(workspace_dir.resolve())
    assert data["pid"] == os.getpid()
    assert data["port"] == 8731
    assert data["url"] == "http://127.0.0.1:8731"
    assert "started_at" in data


def test_name_collision_uses_hash_suffix(pbg_home, tmp_path):
    from discovery_engine.workspace_catalog import register_server
    # Two workspaces with the same name but different paths.
    w1 = tmp_path / "w1" / "shared-name"; w1.mkdir(parents=True)
    w2 = tmp_path / "w2" / "shared-name"; w2.mkdir(parents=True)
    f1 = register_server("shared-name", w1, 100, 8001, "http://127.0.0.1:8001")
    f2 = register_server("shared-name", w2, 200, 8002, "http://127.0.0.1:8002")
    assert f1.name == "shared-name.json"
    assert f2.name.startswith("shared-name.") and f2.name.endswith(".json")
    assert f2.name != f1.name


def test_find_running_returns_entry_if_pid_alive(pbg_home, workspace_dir):
    from discovery_engine.workspace_catalog import register_server, find_running
    register_server(
        name="my-workspace", path=workspace_dir,
        pid=os.getpid(),  # this process is alive
        port=8731, url="http://127.0.0.1:8731",
    )
    entry = find_running(workspace_dir)
    assert entry is not None
    assert entry["pid"] == os.getpid()


def test_find_running_returns_none_if_pid_dead(pbg_home, workspace_dir):
    import subprocess as _sp
    from discovery_engine.workspace_catalog import register_server, find_running
    # Spawn a real subprocess, wait for it to exit, then use its confirmed-dead PID.
    proc = _sp.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    dead_pid = proc.pid
    register_server(
        name="my-workspace", path=workspace_dir,
        pid=dead_pid, port=8731, url="http://127.0.0.1:8731",
    )
    assert find_running(workspace_dir) is None


def test_unregister_server_removes_file(pbg_home, workspace_dir):
    from discovery_engine.workspace_catalog import register_server, unregister_server, find_entry
    register_server("my-workspace", workspace_dir, os.getpid(), 8731, "http://127.0.0.1:8731")
    assert find_entry(workspace_dir) is not None
    assert unregister_server(workspace_dir) is True
    assert find_entry(workspace_dir) is None


def test_concurrent_add_same_path_dedups(pbg_home, tmp_path):
    """Many threads adding the SAME path → exactly one catalog entry."""
    from discovery_engine.workspace_catalog import add, list_workspaces

    ws = tmp_path / "concurrent"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: concurrent\npackage: pbg_concurrent\n")

    errors = []
    def worker():
        try:
            add(ws)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for t in threads: t.start()
    for t in threads: t.join()

    assert errors == []
    assert len(list_workspaces()) == 1


def test_concurrent_add_different_paths_all_survive(pbg_home, tmp_path):
    """N threads each adding a distinct workspace path → all N entries land.

    Without flock, the read-modify-write race causes lost updates: a thread
    reads the catalog, another thread reads the same state, both append their
    entry to their local copy, both write back. The second write erases the
    first thread's entry. With flock, all N entries survive.
    """
    from discovery_engine.workspace_catalog import add, list_workspaces

    N = 12
    workspaces = []
    for i in range(N):
        ws = tmp_path / f"ws-{i}"
        ws.mkdir()
        (ws / "workspace.yaml").write_text(f"name: ws-{i}\npackage: pbg_ws_{i}\n")
        workspaces.append(ws)

    errors = []
    def worker(ws):
        try:
            add(ws)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(w,)) for w in workspaces]
    for t in threads: t.start()
    for t in threads: t.join()

    assert errors == []
    listed = list_workspaces()
    assert len(listed) == N
    paths = sorted(w["path"] for w in listed)
    expected = sorted(str(ws.resolve()) for ws in workspaces)
    assert paths == expected


def test_concurrent_register_server_same_name_different_paths(pbg_home, tmp_path):
    """Two workspaces with same name registering concurrently must end up
    with distinct files (one base, one hash-suffixed) — neither lost."""
    from discovery_engine.workspace_catalog import register_server

    w1 = tmp_path / "a" / "shared-name"; w1.mkdir(parents=True)
    w2 = tmp_path / "b" / "shared-name"; w2.mkdir(parents=True)

    results = {}
    errors = []
    def worker(label, ws, pid, port):
        try:
            fpath = register_server("shared-name", ws, pid, port, f"http://127.0.0.1:{port}")
            results[label] = fpath
        except Exception as e:
            errors.append(e)

    t1 = threading.Thread(target=worker, args=("w1", w1, 100, 8001))
    t2 = threading.Thread(target=worker, args=("w2", w2, 200, 8002))
    t1.start(); t2.start(); t1.join(); t2.join()

    assert errors == []
    assert len(results) == 2
    assert results["w1"] != results["w2"]
    # Both files should still exist (neither overwritten).
    assert results["w1"].exists()
    assert results["w2"].exists()
    # And their contents should match what each thread wrote.
    d1 = json.loads(results["w1"].read_text())
    d2 = json.loads(results["w2"].read_text())
    assert d1["path"] != d2["path"]
    assert {d1["pid"], d2["pid"]} == {100, 200}


def test_cli_add_and_list(pbg_home, workspace_dir):
    env = {**os.environ, "PBG_HOME": str(pbg_home)}
    out = subprocess.check_output(
        [sys.executable, "-m", "discovery_engine.workspace_catalog",
         "add", "--path", str(workspace_dir)],
        env=env,
    )
    entry = json.loads(out)
    assert entry["name"] == "my-workspace"

    listed = subprocess.check_output(
        [sys.executable, "-m", "discovery_engine.workspace_catalog", "list"],
        env=env,
    )
    parsed = json.loads(listed)
    assert len(parsed) == 1
    assert parsed[0]["path"] == str(workspace_dir.resolve())


# -------------------------------------------------------------------- Pass C:
# list_servers / find_duplicates_for_path / cleanup_orphans helpers used by
# the cross-worktree dashboard switcher and workbench-server dedup.


def test_list_servers_returns_empty_when_no_dir(pbg_home):
    from discovery_engine.workspace_catalog import list_servers
    assert list_servers() == []


def test_list_servers_augments_with_file_and_alive(pbg_home, workspace_dir):
    from discovery_engine.workspace_catalog import register_server, list_servers
    register_server("my-workspace", workspace_dir, os.getpid(), 8731,
                    "http://127.0.0.1:8731")
    entries = list_servers()
    assert len(entries) == 1
    assert entries[0]["_alive"] is True
    assert entries[0]["_file"].endswith(".json")
    assert entries[0]["path"] == str(workspace_dir.resolve())


def test_list_servers_marks_dead_pid(pbg_home, workspace_dir):
    import subprocess as _sp
    from discovery_engine.workspace_catalog import register_server, list_servers
    proc = _sp.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    register_server("my-workspace", workspace_dir, proc.pid, 8731,
                    "http://127.0.0.1:8731")
    entries = list_servers()
    assert len(entries) == 1
    assert entries[0]["_alive"] is False


def test_find_duplicates_for_path_returns_only_same_path(pbg_home, tmp_path):
    from discovery_engine.workspace_catalog import (
        register_server, find_duplicates_for_path,
    )
    w1 = tmp_path / "w1"; w1.mkdir()
    w2 = tmp_path / "w2"; w2.mkdir()
    (w1 / "workspace.yaml").write_text("name: w1\n")
    (w2 / "workspace.yaml").write_text("name: w2\n")

    register_server("w1", w1, os.getpid(), 8001, "http://127.0.0.1:8001")
    register_server("w2", w2, os.getpid(), 8002, "http://127.0.0.1:8002")

    dups_w1 = find_duplicates_for_path(w1)
    dups_w2 = find_duplicates_for_path(w2)
    assert len(dups_w1) == 1 and dups_w1[0]["path"] == str(w1.resolve())
    assert len(dups_w2) == 1 and dups_w2[0]["path"] == str(w2.resolve())


def test_cleanup_orphans_removes_dead_pid_records(pbg_home, workspace_dir):
    import subprocess as _sp
    from discovery_engine.workspace_catalog import (
        register_server, cleanup_orphans, list_servers,
    )
    proc = _sp.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    register_server("dead", workspace_dir, proc.pid, 9001,
                    "http://127.0.0.1:9001")
    register_server("alive", workspace_dir, os.getpid(), 9002,
                    "http://127.0.0.1:9002")
    # Two records share workspace_dir.path; both are eligible orphan checks
    # via PID alive/dead test only.
    result = cleanup_orphans()
    assert len(result["removed"]) == 1
    assert result["removed"][0]["pid"] == proc.pid
    assert result["removed"][0]["reason"] == "pid-dead"
    remaining = list_servers()
    assert len(remaining) == 1
    assert remaining[0]["pid"] == os.getpid()


def test_cleanup_orphans_removes_missing_path_records(pbg_home, tmp_path):
    from discovery_engine.workspace_catalog import (
        register_server, cleanup_orphans, list_servers,
    )
    gone = tmp_path / "will-be-gone"
    gone.mkdir()
    (gone / "workspace.yaml").write_text("name: gone\n")
    register_server("gone", gone, os.getpid(), 9003,
                    "http://127.0.0.1:9003")
    # Now remove the directory but keep the registry record.
    import shutil
    shutil.rmtree(gone)
    result = cleanup_orphans()
    assert len(result["removed"]) == 1
    assert "path-missing" in result["removed"][0]["reason"]
    assert list_servers() == []


def test_cleanup_orphans_is_noop_when_clean(pbg_home, workspace_dir):
    from discovery_engine.workspace_catalog import (
        register_server, cleanup_orphans,
    )
    register_server("alive", workspace_dir, os.getpid(), 9004,
                    "http://127.0.0.1:9004")
    result = cleanup_orphans()
    assert result["removed"] == []
    assert result["kept"] == 1


def test_remove_server_file_rejects_path_outside_servers_dir(pbg_home, tmp_path):
    from discovery_engine.workspace_catalog import remove_server_file
    bogus = tmp_path / "elsewhere.json"
    bogus.write_text("{}")
    # Path is outside ~/.pbg/servers/ → must refuse and return False.
    assert remove_server_file(bogus) is False
    assert bogus.exists()


def test_remove_server_file_removes_valid_record(pbg_home, workspace_dir):
    from discovery_engine.workspace_catalog import (
        register_server, remove_server_file, list_servers,
    )
    fpath = register_server("rm-me", workspace_dir, os.getpid(), 9005,
                            "http://127.0.0.1:9005")
    assert fpath.exists()
    assert remove_server_file(fpath) is True
    assert not fpath.exists()
    assert list_servers() == []


def test_cli_list_servers_and_cleanup(pbg_home, workspace_dir):
    import subprocess as _sp
    from discovery_engine.workspace_catalog import register_server

    proc = _sp.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    register_server("dead", workspace_dir, proc.pid, 9006,
                    "http://127.0.0.1:9006")

    env = {**os.environ, "PBG_HOME": str(pbg_home)}
    listed = subprocess.check_output(
        [sys.executable, "-m", "discovery_engine.workspace_catalog", "list-servers"],
        env=env,
    )
    parsed = json.loads(listed)
    assert len(parsed) == 1
    assert parsed[0]["_alive"] is False

    cleaned = subprocess.check_output(
        [sys.executable, "-m", "discovery_engine.workspace_catalog",
         "cleanup-servers"],
        env=env,
    )
    cresult = json.loads(cleaned)
    assert len(cresult["removed"]) == 1
    assert cresult["kept"] == 0
