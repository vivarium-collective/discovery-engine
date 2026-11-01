"""G5: the default runner gains worktree-awareness — when a worktree is given it's prepended to
PYTHONPATH so a freshly-built composite there resolves OVER the canonical editable install (so the
cycle can run a just-built composite without a workspace-wide reinstall). Command-based + worktree
(preserves compound branch commands + render); worktree=None is today's behavior exactly."""
from __future__ import annotations

import types

from discovery_engine import discover as d


class _Proc:
    def __init__(self, rc=0):
        self.returncode = rc
        self.stdout = "ok"
        self.stderr = ""


def _capture(monkeypatch):
    seen = {}
    def _fake_run(command, **kw):
        seen["command"] = command
        seen["env"] = kw.get("env") or {}
        seen["cwd"] = kw.get("cwd")
        seen["timeout"] = kw.get("timeout")
        return _Proc(0)
    import subprocess
    monkeypatch.setattr(subprocess, "run", _fake_run)
    return seen


def test_worktree_is_prepended_to_pythonpath(monkeypatch):
    seen = _capture(monkeypatch)
    out = d._default_runner("vivarium-workbench run-study s1 --workspace /ws", "/ws", worktree="/wt")
    pp = seen["env"].get("PYTHONPATH", "")
    assert pp.split(":")[0] == "/wt"                       # worktree wins over the canonical editable
    assert set(out) >= {"ok", "returncode", "run_id", "tail"}
    assert out["ok"] is True and out["returncode"] == 0


def test_no_worktree_leaves_pythonpath_unchanged(monkeypatch):
    monkeypatch.setenv("PYTHONPATH", "/existing")
    seen = _capture(monkeypatch)
    d._default_runner("echo hi", "/ws")                    # worktree=None
    assert seen["env"].get("PYTHONPATH") == "/existing"    # untouched
    # PATH still gets the venv-bin prepend (today's behavior)
    assert seen["env"]["PATH"].split(":", 1)[0]


def test_timeout_is_threaded(monkeypatch):
    seen = _capture(monkeypatch)
    d._default_runner("echo hi", "/ws", timeout=42)
    assert seen["timeout"] == 42
