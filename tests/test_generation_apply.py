"""Phase 3: applying a filled GenerationResult through the canonical write op."""
import sys

import pytest
import yaml

from discovery_engine.generation import (
    GenerationRequest,
    GenerationResult,
    validate_result,
)
from discovery_engine.generation_apply import (
    apply_result,
    run_requests,
    subprocess_filler,
)


def _ws(tmp_path):
    (tmp_path / "investigations" / "demo").mkdir(parents=True)
    (tmp_path / "investigations" / "demo" / "investigation.yaml").write_text(
        yaml.safe_dump({"name": "demo", "studies": ["s1"]}))
    (tmp_path / "studies" / "s1").mkdir(parents=True)
    (tmp_path / "studies" / "s1" / "study.yaml").write_text(yaml.safe_dump({
        "name": "s1",
        "findings": [{"id": "f1", "evidence": {"from_test": "t", "observed": 1.0}}],
    }))
    return tmp_path


def test_apply_resolve_decision_records_a_resolution(tmp_path):
    ws = _ws(tmp_path)
    req = GenerationRequest(kind="resolve_decision", target="Which prior?",
                            produces="decision/v1", provenance={"op": "steer"})
    res = GenerationResult(request_id=req.id, artifact={"rationale": "use the Beulig prior"},
                           filler={"kind": "human", "id": "expert"})
    out = apply_result(req, res, ws_root=ws, inv_slug="demo")
    assert out["status"] == "applied"
    inv = yaml.safe_load((ws / "investigations" / "demo" / "investigation.yaml").read_text())
    rows = inv.get("resolutions") or []
    assert rows and rows[-1]["target"] == "Which prior?"
    assert rows[-1]["rationale"] == "use the Beulig prior"


def test_apply_interpret_finding_writes_prose_not_evidence(tmp_path):
    ws = _ws(tmp_path)
    req = GenerationRequest(kind="interpret_finding", target="claim:s1",
                            produces="finding/v1", provenance={"op": "synthesize"})
    res = GenerationResult(request_id=req.id, artifact={
        "study": "s1", "finding": "f1",
        "statement": "The measured rate sits inside the window.",
        "explanation": "At steady state the outlet rate stays within [40, 91].",
    })
    out = apply_result(req, res, ws_root=ws, inv_slug="demo")
    assert out["status"] == "applied"
    assert "findings[f1].statement" in out["applied_to"]
    s = yaml.safe_load((ws / "studies" / "s1" / "study.yaml").read_text())
    f = s["findings"][0]
    assert f["statement"].startswith("The measured rate")
    assert f["explanation"].startswith("At steady state")
    assert f["evidence"]["observed"] == 1.0          # numeric evidence untouched


def test_unwired_kind_queues_for_review(tmp_path):
    ws = _ws(tmp_path)
    req = GenerationRequest(kind="propose_study", target="s1", produces="study.yaml/v4")
    out = apply_result(req, GenerationResult(request_id=req.id), ws_root=ws, inv_slug="demo")
    assert out["status"] == "queued_for_review"


def test_malformed_artifacts_raise(tmp_path):
    ws = _ws(tmp_path)
    rd = GenerationRequest(kind="resolve_decision", target="Q", produces="decision/v1")
    with pytest.raises(ValueError):                  # no rationale
        apply_result(rd, GenerationResult(request_id=rd.id, artifact={}), ws_root=ws, inv_slug="demo")
    inf = GenerationRequest(kind="interpret_finding", target="claim:s1", produces="finding/v1")
    with pytest.raises(ValueError):                  # missing study/finding
        apply_result(inf, GenerationResult(request_id=inf.id, artifact={"statement": "x"}),
                     ws_root=ws, inv_slug="demo")


def test_run_requests_drains_auto_queues_review_and_errors_safely(tmp_path):
    ws = _ws(tmp_path)
    reqs = [
        GenerationRequest(kind="resolve_decision", target="Q?", produces="decision/v1", gate="auto"),
        GenerationRequest(kind="interpret_finding", target="claim:s1", produces="finding/v1", gate="review"),
        GenerationRequest(kind="resolve_decision", target="Q2?", produces="decision/v1", gate="auto"),
    ]

    def fill(req):
        if req.kind == "resolve_decision" and req.target == "Q?":
            return GenerationResult(request_id=req.id, artifact={"rationale": "ok"})
        if req.kind == "resolve_decision" and req.target == "Q2?":
            return GenerationResult(request_id=req.id, artifact={})      # malformed -> error, must not block
        return None

    outcomes = run_requests(reqs, fill=fill, ws_root=ws, inv_slug="demo")
    assert len(outcomes) == 3
    assert [o["outcome"] for o in outcomes] == ["applied", "queued_for_review", "error"]
    # the review-gated interpret_finding was NOT filled/applied
    assert outcomes[1]["gate"] == "review"
    # the applied decision really landed
    inv = yaml.safe_load((ws / "investigations" / "demo" / "investigation.yaml").read_text())
    assert any(r.get("target") == "Q?" for r in (inv.get("resolutions") or []))
    assert not any(r.get("target") == "Q2?" for r in (inv.get("resolutions") or []))   # error never applied


def test_run_requests_survives_a_raising_filler(tmp_path):
    ws = _ws(tmp_path)
    req = GenerationRequest(kind="resolve_decision", target="Q?", produces="decision/v1", gate="auto")

    def boom(_):
        raise RuntimeError("filler blew up")

    out = run_requests([req], fill=boom, ws_root=ws, inv_slug="demo")
    assert out[0]["outcome"] == "error" and "blew up" in out[0]["reason"]


def test_subprocess_filler_runs_a_command_and_applies(tmp_path):
    ws = _ws(tmp_path)
    stub = tmp_path / "filler.py"   # a stub $VIVA_FILLER_CMD: request JSON in -> result JSON out
    stub.write_text(
        "import sys, json\n"
        "req = json.load(sys.stdin)\n"
        "print(json.dumps({'request_id': req['id'],"
        " 'artifact': {'rationale': 'auto: ' + req['target']},"
        " 'filler': {'kind': 'ai', 'id': 'stub'}}))\n"
    )
    fill = subprocess_filler(f"{sys.executable} {stub}")
    req = GenerationRequest(kind="resolve_decision", target="Which prior?",
                            produces="decision/v1", gate="auto")
    out = run_requests([req], fill=fill, ws_root=ws, inv_slug="demo")
    assert out[0]["outcome"] == "applied"
    inv = yaml.safe_load((ws / "investigations" / "demo" / "investigation.yaml").read_text())
    assert any(r.get("rationale") == "auto: Which prior?" for r in (inv.get("resolutions") or []))


def test_subprocess_filler_is_none_without_a_command(monkeypatch):
    monkeypatch.delenv("VIVA_FILLER_CMD", raising=False)
    fill = subprocess_filler(None)
    req = GenerationRequest(kind="resolve_decision", target="Q", produces="decision/v1", gate="auto")
    assert fill(req) is None


def test_subprocess_filler_rejects_a_malformed_result(tmp_path):
    stub = tmp_path / "bad.py"
    stub.write_text("print('{\"request_id\": \"x\", \"status\": \"bogus\"}')\n")   # invalid status enum
    fill = subprocess_filler(f"{sys.executable} {stub}")
    req = GenerationRequest(kind="resolve_decision", target="Q", produces="decision/v1", gate="auto")
    with pytest.raises(ValueError):
        fill(req)
    # and via the runner that malformed result is an isolated error, never applied
    out = run_requests([req], fill=fill, ws_root=_ws(tmp_path), inv_slug="demo")
    assert out[0]["outcome"] == "error"


def test_result_contract_and_schema():
    with pytest.raises(ValueError):
        GenerationResult(request_id="")
    validate_result(GenerationResult(request_id="gr-1", artifact={"a": 1},
                                     filler={"kind": "ai", "id": "f"}).to_dict())
    with pytest.raises(ValueError):
        validate_result({"request_id": "x", "status": "bogus"})
