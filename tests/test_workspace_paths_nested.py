"""Nested-aware study resolution (investigation-centric structure, Phase 1)."""
from pathlib import Path

from discovery_engine.workspace_paths import WorkspacePaths


def _ws(tmp, nested: bool):
    (tmp / "workspace.yaml").write_text("name: demo\n", encoding="utf-8")
    if nested:
        d = tmp / "investigations" / "inv-a" / "studies" / "s1"
    else:
        d = tmp / "studies" / "s1"
    d.mkdir(parents=True)
    (d / "study.yaml").write_text(
        ("investigation: inv-a\n" if nested else "") + "name: s1\n", encoding="utf-8")
    return WorkspacePaths.load(tmp)


def test_study_dir_nested(tmp_path):
    wp = _ws(tmp_path, nested=True)
    assert wp.study_dir("s1") == tmp_path / "investigations" / "inv-a" / "studies" / "s1"


def test_study_dir_flat_backcompat(tmp_path):
    wp = _ws(tmp_path, nested=False)
    assert wp.study_dir("s1") == tmp_path / "studies" / "s1"


def test_iter_study_dirs_nested(tmp_path):
    wp = _ws(tmp_path, nested=True)
    assert [p.name for p in wp.iter_study_dirs()] == ["s1"]


def test_study_owner_nested(tmp_path):
    wp = _ws(tmp_path, nested=True)
    assert wp.study_owner("s1") == "inv-a"


def test_study_owner_flat_is_none(tmp_path):
    wp = _ws(tmp_path, nested=False)
    assert wp.study_owner("s1") is None


def test_paths_cli_study_dir(tmp_path, capsys):
    _ws(tmp_path, nested=True)
    from discovery_engine.paths import _main
    rc = _main(["--study", "s1", "--workspace", str(tmp_path)])
    out = capsys.readouterr().out.strip()
    assert rc == 0
    assert out.endswith("investigations/inv-a/studies/s1")


def test_inputs_dir_nested(tmp_path):
    wp = _ws(tmp_path, nested=True)
    assert wp.inputs_dir("inv-a") == tmp_path / "investigations" / "inv-a" / "inputs"
