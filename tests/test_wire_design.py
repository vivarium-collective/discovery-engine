"""DE-09 — wire the (previously orphaned) design→study path: `discover design --materialize`
turns a discriminating ExperimentDesign into a run-ready child study."""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from discovery_engine.discover.actions import materialize_design_study

PARENT = {"schema_version": 4, "name": "s2", "investigation": "inv",
          "observables": [{"path": "flux"}], "behavior_tests": [{"measure": {"path": "flux"}}],
          "baseline": [{"name": "baseline", "composite": "pkg.A", "params": {"k": 1, "d": 2}}]}
# two competing hypotheses on s2 with disjoint predicted bands on flux, keyed on a baseline
# parameter (k) so the arms become real param overrides (not needs_author).
HYPS = [
    {"id": "h-spatial", "study": "s2", "status": "open",
     "predictions": [{"observable": "flux", "when": {"k": 2}, "expected": {"op": ">=", "value": 1}}]},
    {"id": "h-null", "study": "s2", "status": "open",
     "predictions": [{"observable": "flux", "when": {"k": 2}, "expected": {"op": "<=", "value": 0.5}}]},
]


def _ws(tmp_path: Path, hyps=HYPS) -> Path:
    ws = tmp_path / "ws"
    (ws / "investigations" / "inv").mkdir(parents=True)
    (ws / "studies" / "s2").mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    (ws / "studies" / "s2" / "study.yaml").write_text(yaml.safe_dump(PARENT), encoding="utf-8")
    (ws / "investigations" / "inv" / "investigation.yaml").write_text(
        yaml.safe_dump({"name": "inv", "studies": ["s2"], "hypotheses": hyps}), encoding="utf-8")
    return ws


def test_materialize_writes_child_and_records_design(tmp_path):
    ws = _ws(tmp_path)
    rep = materialize_design_study(ws, "inv", "s2")
    assert rep["design_id"] == "design:s2" and rep["child_slug"] == "s2-discriminating"

    child = Path(rep["path"])
    assert child.exists()
    cspec = yaml.safe_load(child.read_text())
    assert cspec["variants"], "the child must carry the design's intervention arms"
    # the k-keyed arms become real param overrides, not needs_author
    assert any(v.get("params", {}).get("k") == 2 for v in cspec["variants"])
    assert cspec["hypotheses"] and all(h["origin"]["kind"] == "design" for h in cspec["hypotheses"])

    inv = yaml.safe_load((ws / "investigations" / "inv" / "investigation.yaml").read_text())
    assert any(d["id"] == "design:s2" for d in inv["experiment_designs"])   # design recorded
    assert "s2-discriminating" in inv["studies"]                            # child registered


def test_materialize_is_idempotent_on_the_design_record(tmp_path):
    ws = _ws(tmp_path)
    materialize_design_study(ws, "inv", "s2")
    materialize_design_study(ws, "inv", "s2")
    inv = yaml.safe_load((ws / "investigations" / "inv" / "investigation.yaml").read_text())
    assert len([d for d in inv["experiment_designs"] if d["id"] == "design:s2"]) == 1


def test_no_discriminable_hypotheses_raises(tmp_path):
    ws = _ws(tmp_path, hyps=[HYPS[0]])       # a single hypothesis is not discriminable
    with pytest.raises(ValueError, match="discriminable"):
        materialize_design_study(ws, "inv", "s2")
