import sys

import pytest

from discovery_engine import discover
from discovery_engine.projection import engine as de
from discovery_engine.study.io import load_yaml, save_yaml_atomic

OPP = {"id": "anomaly:s3:colo", "source": "s3:colo", "action_type": "revise_model",
       "kind": "anomaly", "question": "colo=0.9 outside all live bands", "value": {"score": 0.5}}

ARTIFACT = {"statement": "colo is high", "mechanism": "overflow",
            "predictions": [{"observable": "colo", "expected": {"min": 0.8, "max": 1.0}, "falsifier": "colo<0.5"}],
            "provenance": {"trigger": "anomaly:s3:colo"}}


def _ws(tmp_path, monkeypatch):
    (tmp_path / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    inv = tmp_path / "investigations" / "og"
    inv.mkdir(parents=True)
    save_yaml_atomic(inv / "investigation.yaml", {"studies": []})
    monkeypatch.setattr(de, "opportunities", lambda result, spec: [OPP])
    return inv / "investigation.yaml"


def _run(tmp_path, *argv):
    return discover.main(["--workspace", str(tmp_path), "propose", "og", *argv])


def test_dry_run_lists_routing_and_writes_nothing(tmp_path, monkeypatch, capsys):
    p = _ws(tmp_path, monkeypatch)
    before = p.read_text()
    assert _run(tmp_path) == 0
    out = capsys.readouterr().out
    assert "hypothesize" in out and "skill=hypothesize" in out and "gate=review" in out
    assert p.read_text() == before and "agent_captures" not in load_yaml(p)


def test_fill_captures_and_queues_review_gated(tmp_path, monkeypatch, capsys):
    p = _ws(tmp_path, monkeypatch)
    import json
    stub = tmp_path / "filler.py"
    stub.write_text(
        "import sys, json\nreq = json.load(sys.stdin)\n"
        f"print(json.dumps({{'request_id': req['id'], 'artifact': {json.dumps(ARTIFACT)},"
        " 'filler': {'kind': 'ai', 'id': 'stub', 'model': 'm'}}))\n")
    monkeypatch.setenv("VIVA_FILLER_CMD", f"{sys.executable} {stub}")
    assert _run(tmp_path, "--fill", "--autonomous") == 0
    spec = load_yaml(p)
    assert len(spec["agent_captures"]) == 1 and spec["agent_captures"][0]["outcome"] == "proposed"
    assert not spec.get("hypotheses") and not spec.get("hypothesis_proposals")
    assert "queued 1" in capsys.readouterr().out
