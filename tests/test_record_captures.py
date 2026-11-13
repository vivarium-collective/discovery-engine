import pytest

from discovery_engine.discover.actions import record_captures
from discovery_engine.study.io import load_yaml, save_yaml_atomic


def _inv(tmp_path):
    p = tmp_path / "investigation.yaml"
    save_yaml_atomic(p, {"studies": []})
    return p


def _cap(**kw):
    c = {"request_id": "r1", "kind": "hypothesize", "target": "s:o", "skill": "hypothesize",
         "filler": {"id": "stub", "model": "m"}, "context": {}, "artifact": {"statement": "x"},
         "validation": {"ok": True, "errors": []}, "outcome": "proposed", "at": "2026-01-01T00:00:00"}
    c.update(kw)
    return c


def test_appends_and_is_idempotent(tmp_path):
    p = _inv(tmp_path)
    assert len(record_captures(p, [_cap()])) == 1
    assert record_captures(p, [_cap()]) == []
    caps = load_yaml(p)["agent_captures"]
    assert len(caps) == 1 and caps[0]["id"].startswith("cap:")


def test_rejects_missing_request_id(tmp_path):
    with pytest.raises(ValueError):
        record_captures(_inv(tmp_path), [_cap(request_id="")])


def test_rejects_proposed_without_artifact_or_filler(tmp_path):
    p = _inv(tmp_path)
    with pytest.raises(ValueError):
        record_captures(p, [_cap(artifact={})])
    with pytest.raises(ValueError):
        record_captures(p, [_cap(filler={})])
    assert "agent_captures" not in load_yaml(p)


@pytest.mark.parametrize("outcome", ["error", "empty", "invalid"])
def test_accepts_failure_captures_without_artifact(tmp_path, outcome):
    p = _inv(tmp_path)
    out = record_captures(p, [_cap(outcome=outcome, artifact={}, filler={})])
    assert len(out) == 1 and len(load_yaml(p)["agent_captures"]) == 1
