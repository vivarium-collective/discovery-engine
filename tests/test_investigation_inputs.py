from pathlib import Path
import yaml
from discovery_engine.investigation_inputs import investigation_inputs

def _ws(tmp):
    (tmp / "workspace.yaml").write_text("name: demo\n", encoding="utf-8")
    inv = tmp / "investigations" / "inv-a"; (inv / "inputs" / "datasets").mkdir(parents=True)
    return tmp, inv

def test_reads_declared_inputs(tmp_path):
    ws, inv = _ws(tmp_path)
    (inv / "investigation.yaml").write_text(yaml.safe_dump({
        "name": "inv-a",
        "inputs": {"datasets": [{"name": "d1", "path": "inputs/datasets/d1.csv"}],
                   "references": ["Boesen2024"], "expert_docs": ["inputs/notes/x.md"]},
    }), encoding="utf-8")
    out = investigation_inputs(ws, "inv-a")
    assert out["datasets"][0]["name"] == "d1"
    assert out["references"] == ["Boesen2024"]
    assert out["expert_docs"] == ["inputs/notes/x.md"]

def test_empty_when_no_inputs_block(tmp_path):
    ws, inv = _ws(tmp_path)
    (inv / "investigation.yaml").write_text("name: inv-a\n", encoding="utf-8")
    out = investigation_inputs(ws, "inv-a")
    assert out == {"datasets": [], "references": [], "expert_docs": [], "_repo_fallback": False}

def test_repo_fallback_when_unmigrated(tmp_path):
    ws, inv = _ws(tmp_path)
    (inv / "investigation.yaml").write_text("name: inv-a\n", encoding="utf-8")
    (ws / "datasets").mkdir(); (ws / "datasets" / "shared.csv").write_text("x")
    out = investigation_inputs(ws, "inv-a", repo_fallback=True)
    assert out["_repo_fallback"] is True
    assert any("shared.csv" in d.get("path", "") for d in out["datasets"])
