from pathlib import Path
from discovery_engine import study_io, run_registry, study_outcomes as so


def test_cli_syncs_named_study(tmp_path, capsys):
    (tmp_path / "workspace.yaml").write_text("name: ws\n")
    d = tmp_path / "studies" / "s1"; d.mkdir(parents=True)
    study_io.save_yaml_atomic(d / "study.yaml", {"name": "s1", "runs": []})
    run_registry.register_run(d / "runs.db", "r1", spec_id="s1", status="completed",
                              started_at="2026-01-01T00:00:00Z", completed_at="2026-01-01T00:01:00Z")
    rc = so.main(["--workspace", str(tmp_path), "--study", "s1"])
    assert rc == 0
    spec = study_io.load_yaml_mapping(d / "study.yaml")
    assert any(r["name"] == "r1" for r in spec["runs"])
    assert "added=1" in capsys.readouterr().out


def test_cli_all_syncs_every_study(tmp_path, capsys):
    (tmp_path / "workspace.yaml").write_text("name: ws\n")
    for slug, run_id in [("s1", "r1"), ("s2", "r2")]:
        d = tmp_path / "studies" / slug
        d.mkdir(parents=True)
        study_io.save_yaml_atomic(d / "study.yaml", {"name": slug, "runs": []})
        run_registry.register_run(d / "runs.db", run_id, spec_id=slug, status="completed",
                                  started_at="2026-01-01T00:00:00Z",
                                  completed_at="2026-01-01T00:01:00Z")
    rc = so.main(["--workspace", str(tmp_path), "--all"])
    assert rc == 0
    for slug, run_id in [("s1", "r1"), ("s2", "r2")]:
        spec = study_io.load_yaml_mapping(tmp_path / "studies" / slug / "study.yaml")
        assert any(r["name"] == run_id for r in spec["runs"])
    out = capsys.readouterr().out
    assert "s1" in out and "s2" in out
