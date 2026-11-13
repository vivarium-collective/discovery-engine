"""Phase 1 emission projection: opportunities -> GenerationRequests."""
from discovery_engine.phases.generation import GateConfig
from discovery_engine.phases.generation_emit import generation_requests


def _opp(op, source="og-x", kind="k", value=None, oid=None, question="why"):
    return {"id": oid or f"{kind}:{source}", "source": source, "action_type": op,
            "kind": kind, "value": value or {"score": 0.5}, "question": question,
            "priority": 30, "state": "open"}


def test_op_to_kind_mapping_covers_the_vocabulary():
    pairs = {
        "branch": "propose_study", "test": "author_tests", "steer": "resolve_decision",
        "synthesize": "interpret_finding", "reconcile": "interpret_finding",
        "revise": "interpret_finding", "revise_model": "acquire_model",
        "build_process": "acquire_model", "build_viz": "visualize",
        "refine_viz": "visualize", "relocate_viz": "visualize",
    }
    for op, kind in pairs.items():
        reqs = generation_requests([_opp(op)])
        assert len(reqs) == 1, op
        assert reqs[0].kind == kind, op
        assert reqs[0].produces                      # every kind has a contract


def test_narrative_gap_branch_is_explain_not_propose():
    assert [r.kind for r in generation_requests([_opp("branch", kind="narrative_gap")])] == ["explain"]


def test_skips_revalidate_and_unknown_ops():
    reqs = generation_requests([_opp("revalidate"), _opp("no_such_op"), _opp("branch")])
    assert [r.kind for r in reqs] == ["propose_study"]   # revalidate = deterministic re-run, not a request


def test_carries_target_value_rationale_provenance_and_gate():
    opp = _opp("steer", source="og-decide", value={"score": 0.9, "info": 0.3},
               oid="open_decision:og-decide", question="which prior?")
    r = generation_requests([opp], gates=GateConfig(default="auto"))[0]
    assert r.target == "og-decide"
    assert r.value == {"score": 0.9, "info": 0.3}
    assert r.rationale == "which prior?"
    assert r.provenance == {"source": "opportunities", "op": "steer",
                            "opportunity_id": "open_decision:og-decide"}
    assert r.gate == "auto"                                   # from the passed GateConfig
    assert generation_requests([opp])[0].gate == "review"     # default for resolve_decision


def test_order_preserved_and_malformed_items_skipped():
    reqs = generation_requests(["not a dict", {}, _opp("branch", source="a"),
                                _opp("steer", source="b")])
    assert [r.target for r in reqs] == ["a", "b"]


def test_empty_and_none_input():
    assert generation_requests([]) == []
    assert generation_requests(None) == []


def test_anomaly_opportunity_emits_hypothesize_with_trigger_context():
    opp = {"id": "anomaly:s3:colo", "source": "s3:colo", "action_type": "revise_model",
           "kind": "anomaly", "question": "colo=0.9 outside all live bands",
           "competing_predictions": None, "value": {"score": 0.5}}
    reqs = generation_requests([opp])
    assert [r.kind for r in reqs] == ["hypothesize"]
    r = reqs[0]
    assert r.produces == "hypothesis_proposal/v1"
    assert r.target == "s3:colo"
    assert r.gate == "review"                      # branch-point default
    assert r.context.get("kind") == "anomaly"      # trigger carried for the SKILL.md
    assert "0.9" in r.rationale                    # observed value reaches the filler


def test_model_disagreement_opportunity_emits_hypothesize():
    opp = {"id": "model_disagreement:s2:k", "source": "s2:k", "action_type": "revise_model",
           "kind": "model_disagreement", "value": {"score": 0.4}}
    assert [r.kind for r in generation_requests([opp])] == ["hypothesize"]


def test_revision_proposed_still_emits_acquire_model():
    opp = {"id": "revision_proposed:rp1", "source": "rp1", "action_type": "revise_model",
           "kind": "revision_proposed", "value": {"score": 0.6}}
    assert [r.kind for r in generation_requests([opp])] == ["acquire_model"]


def test_overreaching_verdict_opportunity_emits_critique():
    opp = {"id": "verdict_overreach:claim:x", "source": "claim:x", "action_type": "synthesize",
           "kind": "verdict_overreach", "value": {"score": 0.3}}
    assert [r.kind for r in generation_requests([opp])] == ["critique"]


def test_competing_predictions_carried_into_hypothesize_context():
    preds = [{"model": "a", "value": 0.1}, {"model": "b", "value": 0.8}]
    opp = {"id": "model_disagreement:s2:k", "source": "s2:k", "action_type": "revise_model",
           "kind": "model_disagreement", "competing_predictions": preds,
           "value": {"score": 0.4}}
    reqs = generation_requests([opp])
    assert reqs[0].kind == "hypothesize"
    assert reqs[0].context["competing_predictions"] == preds
