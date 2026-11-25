"""RFC-0008 Phase 2: per-study simulator env planner + opt-in builder."""
from pathlib import Path

import pytest

from discovery_engine.study import sim_env


@pytest.fixture(autouse=True)
def _cache(tmp_path, monkeypatch):
    monkeypatch.setenv("VIVA_STUDY_ENV_CACHE", str(tmp_path / "envs"))


def test_env_key_stable_and_ref_sensitive():
    a = sim_env.env_key({"package": "viva-smoldyn", "ref": "1.4.2"}, python="3.12.1")
    b = sim_env.env_key({"package": "viva-smoldyn", "ref": "1.4.2"}, python="3.12.1")
    c = sim_env.env_key({"package": "viva-smoldyn", "ref": "1.5.0"}, python="3.12.1")
    assert a == b and a != c and len(a) == 12


def test_plan_version_ref():
    p = sim_env.plan_study_env({"package": "viva-smoldyn", "ref": "1.4.2"})
    assert p["requirement"] == "viva-smoldyn==1.4.2"
    assert p["git_ref"] is False and p["cached"] is False
    assert p["uv_commands"][0][:2] == ["uv", "venv"]
    assert p["uv_commands"][1][:3] == ["uv", "pip", "install"]


def test_plan_git_ref_is_flagged_not_pinned():
    p = sim_env.plan_study_env({"package": "viva-smoldyn", "ref": "a1b2c3d4e5f6"})
    assert p["git_ref"] is True
    assert p["requirement"] == "viva-smoldyn"  # bare: can't == a sha


def test_materialize_execute_false_never_builds():
    out = sim_env.materialize_study_env({"package": "viva-smoldyn", "ref": "1.4.2"})
    assert out["built"] is False and out["python_path"] is None


def test_materialize_git_ref_refused():
    out = sim_env.materialize_study_env(
        {"package": "viva-smoldyn", "ref": "a1b2c3d4e5f6"}, execute=True)
    assert out["built"] is False and "git-sha" in out["error"]


def test_materialize_uses_cache_without_building(tmp_path):
    pin = {"package": "viva-smoldyn", "ref": "1.4.2"}
    env_dir = Path(sim_env.plan_study_env(pin)["env_dir"])
    (env_dir / "bin").mkdir(parents=True)
    (env_dir / "bin" / "python").write_text("#!/bin/sh\n")  # pretend a built env
    out = sim_env.materialize_study_env(pin, execute=True)
    assert out["cached"] is True and out["built"] is False
    assert out["python_path"].endswith("/bin/python")
