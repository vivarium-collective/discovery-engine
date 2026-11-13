import json
import os

from discovery_engine.benchmark.ablation import CONFIGS
from discovery_engine.benchmark.report import render_matrix, run_matrix
from discovery_engine.discover import cli


def test_matrix_discriminates(tmp_path_factory):
    m = run_matrix(tmp_path_factory=tmp_path_factory)
    print(render_matrix(m))
    assert m["full"].mechanism_proposed is True
    assert m["no_acquire_model"].mechanism_proposed is False
    out = render_matrix(m)
    for name in CONFIGS:
        assert name in out
    assert "mechanism_proposed" in out


def test_cli_benchmark_json(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["benchmark", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["full"]["mechanism_proposed"] is True
    assert data["no_acquire_model"]["mechanism_proposed"] is False
    assert os.listdir(tmp_path) == []
