import json
import os

from discovery_engine.benchmark.report import render_recovery_matrix, run_recovery_matrix
from discovery_engine.discover import cli


def test_recovery_matrix_discriminates(tmp_path_factory):
    m = run_recovery_matrix(tmp_path_factory=tmp_path_factory)
    assert m["full"].mechanism_recovered is True
    assert m["full_decoy"].mechanism_recovered is False
    assert m["no_acquire"].mechanism_recovered is False
    assert m["full_decoy"].held_out_error > m["full"].held_out_error
    assert "full_decoy" in render_recovery_matrix(m)


def test_cli_recovery_json(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["benchmark", "--recovery", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["full"]["mechanism_recovered"] is True
    assert data["full_decoy"]["mechanism_recovered"] is False
    assert os.listdir(tmp_path) == []
