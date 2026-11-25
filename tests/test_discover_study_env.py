"""`discover study-env` plans a study's per-study simulator env (RFC-0008)."""
import yaml

from discovery_engine.discover import cli


def _ws(tmp_path, pin=None):
    (tmp_path / "workspace.yaml").write_text("name: t\n")
    d = tmp_path / "studies" / "s"; d.mkdir(parents=True)
    spec = {"name": "s"}
    if pin:
        spec["simulator"] = pin
    (d / "study.yaml").write_text(yaml.safe_dump(spec))
    return tmp_path


def test_study_env_plan_prints_env_key(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("VIVA_STUDY_ENV_CACHE", str(tmp_path / "envs"))
    ws = _ws(tmp_path, {"package": "process-bigraph", "ref": "1.11.2"})
    rc = cli.main(["--workspace", str(ws), "study-env", "s"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "process-bigraph@1.11.2" in out and "env_key" in out
    assert "plan only" in out  # no --build → does not materialize


def test_study_env_no_pin_is_graceful(tmp_path, capsys):
    ws = _ws(tmp_path)  # no simulator pin
    rc = cli.main(["--workspace", str(ws), "study-env", "s"])
    assert rc == 0 and "no `simulator:` pin" in capsys.readouterr().out
