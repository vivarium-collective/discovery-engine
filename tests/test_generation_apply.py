"""Phase 3: applying a filled GenerationResult through the canonical write op."""
import sys

import pytest
import yaml

from discovery_engine.phases.generation import (
    GenerationRequest,
    GenerationResult,
    validate_result,
)
from discovery_engine.phases.generation_apply import (
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


def test_apply_author_tests_appends_and_is_idempotent(tmp_path):
    ws = _ws(tmp_path)
    req = GenerationRequest(kind="author_tests", target="s1",
                            produces="behavior_tests[]", provenance={"op": "test"})
    art = {"study": "s1", "tests": [
        {"name": "val_in_band", "measure": {"kind": "range_check", "path": "x"},
         "pass_if": {"low": 0.1, "high": 0.3}},
    ]}
    out = apply_result(req, GenerationResult(request_id=req.id, artifact=dict(art)),
                       ws_root=ws, inv_slug="demo")
    assert out["status"] == "applied"
    assert out["applied_to"] == ["behavior_tests[val_in_band]"]
    s = yaml.safe_load((ws / "studies" / "s1" / "study.yaml").read_text())
    names = [t["name"] for t in s["behavior_tests"]]
    assert names == ["val_in_band"]

    # idempotent: applying the same artifact again adds nothing, never duplicates
    out2 = apply_result(req, GenerationResult(request_id=req.id, artifact=dict(art)),
                        ws_root=ws, inv_slug="demo")
    assert out2["status"] == "applied"
    assert out2["applied_to"] == []
    s2 = yaml.safe_load((ws / "studies" / "s1" / "study.yaml").read_text())
    assert [t["name"] for t in s2["behavior_tests"]] == ["val_in_band"]


def test_apply_author_tests_malformed_raises(tmp_path):
    ws = _ws(tmp_path)
    req = GenerationRequest(kind="author_tests", target="s1", produces="behavior_tests[]")
    with pytest.raises(ValueError):                  # no named tests
        apply_result(req, GenerationResult(request_id=req.id, artifact={"study": "s1", "tests": []}),
                     ws_root=ws, inv_slug="demo")
    with pytest.raises(ValueError):                  # no study
        apply_result(req, GenerationResult(request_id=req.id, artifact={"tests": [{"name": "t"}]}),
                     ws_root=ws, inv_slug="demo")


def test_apply_explain_sets_finding_explanation_and_never_clobbers(tmp_path):
    ws = _ws(tmp_path)
    req = GenerationRequest(kind="explain", target="claim:s1",
                            produces="explanation/v1", provenance={"op": "branch"})
    res = GenerationResult(request_id=req.id, artifact={
        "study": "s1", "finding": "f1",
        "explanation": "The transition holds because the outlet rate stays in band.",
    })
    out = apply_result(req, res, ws_root=ws, inv_slug="demo")
    assert out["status"] == "applied"
    assert out["applied_to"] == ["findings[f1].explanation"]
    s = yaml.safe_load((ws / "studies" / "s1" / "study.yaml").read_text())
    assert s["findings"][0]["explanation"].startswith("The transition holds")
    assert s["findings"][0]["evidence"]["observed"] == 1.0   # numeric evidence untouched

    # derive-first: a non-empty existing explanation is NOT clobbered
    res2 = GenerationResult(request_id=req.id, artifact={
        "study": "s1", "finding": "f1", "explanation": "a different, overriding explanation"})
    out2 = apply_result(req, res2, ws_root=ws, inv_slug="demo")
    assert out2["applied_to"] == []
    s2 = yaml.safe_load((ws / "studies" / "s1" / "study.yaml").read_text())
    assert s2["findings"][0]["explanation"].startswith("The transition holds")


def test_apply_explain_malformed_raises(tmp_path):
    ws = _ws(tmp_path)
    req = GenerationRequest(kind="explain", target="claim:s1", produces="explanation/v1")
    with pytest.raises(ValueError):                  # missing study/finding
        apply_result(req, GenerationResult(request_id=req.id, artifact={"explanation": "x"}),
                     ws_root=ws, inv_slug="demo")
    with pytest.raises(ValueError):                  # no prose
        apply_result(req, GenerationResult(request_id=req.id,
                                           artifact={"study": "s1", "finding": "f1"}),
                     ws_root=ws, inv_slug="demo")


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


_HYP = {"statement": "s", "mechanism": "m",
        "predictions": [{"observable": "o", "expected": {"op": "<", "value": 0.2},
                         "falsifier": "o>=0.2"}],
        "provenance": {"trigger": "unexplained_anomaly"}}


def test_apply_hypothesize_routes_via_registry(tmp_path):
    ws = _ws(tmp_path)
    req = GenerationRequest(kind="hypothesize", target="anomaly", produces="hypothesis/v1")
    out = apply_result(req, GenerationResult(request_id=req.id, artifact=dict(_HYP)),
                       ws_root=ws, inv_slug="demo")
    assert out["status"] == "applied"
    assert out["applied_to"] == ["demo/hypothesis_proposals"]
    inv = yaml.safe_load((ws / "investigations" / "demo" / "investigation.yaml").read_text())
    assert inv["hypothesis_proposals"]


def test_apply_hypothesize_malformed_raises(tmp_path):
    ws = _ws(tmp_path)
    req = GenerationRequest(kind="hypothesize", target="anomaly", produces="hypothesis/v1")
    with pytest.raises(ValueError):
        apply_result(req, GenerationResult(request_id=req.id, artifact={"statement": "x"}),
                     ws_root=ws, inv_slug="demo")


_CRIT = {"target": "claim:s1",
         "unsupported_claims": ["rate is steady-state"],
         "provenance": {"trigger": "unexplained_anomaly"}}


def test_apply_critique_writes_critique_reports_block(tmp_path):
    ws = _ws(tmp_path)
    req = GenerationRequest(kind="critique", target="claim:s1", produces="critique_report/v1")
    out = apply_result(req, GenerationResult(request_id=req.id, artifact=dict(_CRIT)),
                       ws_root=ws, inv_slug="demo")
    assert out["status"] == "applied"
    assert out["applied_to"] == ["demo/critique_reports"]
    inv = yaml.safe_load((ws / "investigations" / "demo" / "investigation.yaml").read_text())
    assert inv["critique_reports"] and inv["critique_reports"][-1]["target"] == "claim:s1"
    assert "hypothesis_proposals" not in inv


_MA = {"mechanism": "distance-limited dispersal kernel",
       "expected_behavioral_test": {"behavior_test": "colonization_in_band",
                                    "pass_if": {"low": 0.1, "high": 0.3}},
       "provenance": {"trigger": "needs_author"}}


def test_apply_acquire_model_writes_block(tmp_path):
    ws = _ws(tmp_path)
    req = GenerationRequest(kind="acquire_model", target="mech", produces="model_spec/v1")
    out = apply_result(req, GenerationResult(request_id=req.id, artifact=dict(_MA)),
                       ws_root=ws, inv_slug="demo")
    assert out["status"] == "applied"
    assert out["applied_to"] == ["demo/model_acquisition_proposals"]
    inv = yaml.safe_load((ws / "investigations" / "demo" / "investigation.yaml").read_text())
    assert inv["model_acquisition_proposals"][0]["status"] == "proposed"


def test_apply_acquire_model_malformed_raises(tmp_path):
    ws = _ws(tmp_path)
    req = GenerationRequest(kind="acquire_model", target="mech", produces="model_spec/v1")
    with pytest.raises(ValueError):
        apply_result(req, GenerationResult(request_id=req.id, artifact={"mechanism": "x"}),
                     ws_root=ws, inv_slug="demo")


# --- the unified dispatch contract ------------------------------------------
# Every apply-applicable kind resolves to exactly one uniform handler — the
# native table OR the registry adapter, never both, never neither — and the two
# CLI-verb kinds (propose_study/visualize) plus any unknown kind resolve to no
# handler (queued, never silently applied). This pins the single-seam invariant:
# wiring a new kind means adding a handler, not another branch.

def test_handler_resolves_for_every_applicable_kind():
    from discovery_engine.agents.skill_registry import default_registry
    from discovery_engine.phases.generation import KINDS
    from discovery_engine.phases.generation_apply import (
        _NATIVE_HANDLERS,
        _apply_registry_skill,
        _handler_for,
    )

    native = set(_NATIVE_HANDLERS)
    registry = set(default_registry().kinds())
    assert native.isdisjoint(registry)                      # one seam per kind, never both

    for kind in native:
        assert _handler_for(kind) is _NATIVE_HANDLERS[kind]
    for kind in registry:
        assert _handler_for(kind) is _apply_registry_skill

    # the CLI-verb kinds author via a /viva-* flow, not through apply_result
    cli_verb = set(KINDS) - native - registry
    assert cli_verb == {"propose_study", "visualize"}
    for kind in cli_verb | {"no_such_kind"}:
        assert _handler_for(kind) is None


def test_applied_to_is_a_list_for_every_wired_kind(tmp_path):
    # the uniform handler contract: a successful apply always returns a list of
    # applied_to strings, whichever seam served it.
    ws = _ws(tmp_path)
    cases = [
        GenerationRequest(kind="resolve_decision", target="Which prior?", produces="decision/v1"),
        GenerationRequest(kind="hypothesize", target="anomaly", produces="hypothesis/v1"),
    ]
    arts = [{"rationale": "use the Beulig prior"}, dict(_HYP)]
    for req, art in zip(cases, arts):
        out = apply_result(req, GenerationResult(request_id=req.id, artifact=art),
                           ws_root=ws, inv_slug="demo")
        assert out["status"] == "applied"
        assert isinstance(out["applied_to"], list) and out["applied_to"]
