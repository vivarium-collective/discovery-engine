"""`discover stage <name>` — the six-stage public cycle read-only umbrella.

Each stage view projects the cycle and prints the read-only slice for that
public stage (docs/glossary.md), without running any mutating verb.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from discovery_engine.discover import main
from discovery_engine.discover.cli import _STAGES

_STAGE_NAMES = list(_STAGES)


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "investigations").mkdir(parents=True)
    (ws / "studies").mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: stage-ws\n", encoding="utf-8")
    d = ws / "investigations" / "inv1"
    d.mkdir(parents=True, exist_ok=True)
    (d / "investigation.yaml").write_text(
        yaml.safe_dump({"name": "inv1", "members": ["s1"]}, sort_keys=False), encoding="utf-8")
    sd = ws / "studies" / "s1"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "study.yaml").write_text(
        yaml.safe_dump({"schema_version": 4, "name": "s1", "investigation": "inv1",
                        "claim": "s1 claim"}, sort_keys=False), encoding="utf-8")
    return ws


def test_six_stages_registered():
    assert _STAGE_NAMES == ["question", "hypothesize", "model", "experiment", "evaluate", "synthesize"]


def test_each_stage_view_runs_read_only(tmp_path, capsys):
    ws = _ws(tmp_path)
    inv_yaml = ws / "investigations" / "inv1" / "investigation.yaml"
    before = inv_yaml.read_text(encoding="utf-8")
    for stage in _STAGE_NAMES:
        rc = main(["--workspace", str(ws), "stage", stage, "inv1"])
        assert rc == 0, f"stage {stage} exited {rc}"
        out = capsys.readouterr().out
        num, name, meaning, reads, acts = _STAGES[stage]
        assert name in out, f"stage {stage} view missing its name"
        assert "read-only views" in out
        # the view must not have written to the investigation
        assert inv_yaml.read_text(encoding="utf-8") == before, f"stage {stage} mutated the investigation"


def test_unknown_stage_is_rejected(tmp_path):
    ws = _ws(tmp_path)
    import pytest
    with pytest.raises(SystemExit):   # argparse choices rejects an invalid stage
        main(["--workspace", str(ws), "stage", "nonsense", "inv1"])
