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


def test_no_critique_drops_only_the_critique_axis(tmp_path_factory):
    # no_critique keeps the acquire_model skill (still proposes the mechanism) but
    # loses the critique skill, so the overreach goes un-critiqued. This is what
    # makes the row meaningful instead of a duplicate of full.
    m = run_matrix(tmp_path_factory=tmp_path_factory)
    full, no_crit = m["full"], m["no_critique"]
    assert full.overreach_critiqued is True
    assert no_crit.overreach_critiqued is False
    # the other axis is unchanged — only critique was dropped
    assert no_crit.mechanism_proposed is True
    assert "overreach_critiqued" in render_matrix(m)


def test_decoy_control_makes_mechanism_match_load_bearing(tmp_path_factory):
    # The negative control: full_decoy records a well-formed proposal naming the
    # WRONG mechanism. It must score mechanism_proposed=False with the SAME
    # proposal_count as full — otherwise the metric would just be counting proposals.
    m = run_matrix(tmp_path_factory=tmp_path_factory)
    full, decoy = m["full"], m["full_decoy"]
    assert full.mechanism_proposed is True
    assert decoy.mechanism_proposed is False
    assert decoy.proposal_count >= 1
    assert decoy.proposal_count == full.proposal_count
    assert "full_decoy" in render_matrix(m)


def test_cli_benchmark_json(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["benchmark", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["full"]["mechanism_proposed"] is True
    assert data["no_acquire_model"]["mechanism_proposed"] is False
    assert os.listdir(tmp_path) == []
