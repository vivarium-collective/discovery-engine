from discovery_engine.agents.discovery_agent import DiscoveryAgent
from discovery_engine.phases.generation import GenerationResult


def _opp():
    return {"id": "anomaly:s:o", "source": "s:o", "action_type": "revise_model",
            "kind": "anomaly", "value": {"score": 0.5}}


def test_propose_routes_fills_captures_and_validates():
    good = {"statement": "...", "mechanism": "...",
            "predictions": [{"observable": "c", "expected": {"op": "<", "value": 0.2},
                             "falsifier": "c>=0.2"}],
            "provenance": {"trigger": "unexplained_anomaly"}}

    def fill(req):
        return GenerationResult(request_id=req.id, artifact=good,
                                filler={"kind": "ai", "id": "stub", "model": "m"})

    port = DiscoveryAgent().propose([_opp()], fill=fill)
    assert [c.kind for c in port.captures] == ["hypothesize"]
    assert port.captures[0].outcome == "proposed"
    assert port.captures[0].validation["ok"] is True
    assert port.captures[0].filler["model"] == "m"
    assert len(port.proposals) == 1


def test_filler_raising_becomes_error_capture_not_crash():
    def fill(req):
        raise RuntimeError("boom")

    port = DiscoveryAgent().propose([_opp()], fill=fill)
    assert port.captures[0].outcome == "error"
    assert "boom" in port.captures[0].validation["errors"][0]
    assert port.proposals == []


def test_empty_fill_is_empty_capture():
    port = DiscoveryAgent().propose([_opp()], fill=lambda r: None)
    assert port.captures[0].outcome == "empty"
    assert port.proposals == []


def test_bad_artifact_is_captured_invalid_not_proposed():
    def fill(req):
        return GenerationResult(request_id=req.id, artifact={"statement": "x"},
                                filler={"kind": "ai", "id": "s", "model": "m"})

    port = DiscoveryAgent().propose([_opp()], fill=fill)
    assert port.captures[0].outcome == "invalid"
    assert port.captures[0].validation["ok"] is False
    assert port.proposals == []


def _fill_with(artifact):
    def fill(req):
        return GenerationResult(request_id=req.id, artifact=artifact,
                                filler={"kind": "ai", "id": "s", "model": "m"})
    return fill


def test_malformed_type_artifacts_are_invalid_not_crash():
    bad_pred = {"statement": "s", "mechanism": "m", "predictions": "not-a-list",
                "provenance": {"trigger": "t"}}
    for art in (bad_pred, []):
        port = DiscoveryAgent().propose([_opp()], fill=_fill_with(art))
        assert port.captures[0].outcome == "invalid"
        assert port.captures[0].validation["ok"] is False
        assert port.proposals == []


def test_non_generation_result_is_invalid_not_crash():
    port = DiscoveryAgent().propose([_opp()], fill=lambda r: {"artifact": {}})
    assert port.captures[0].outcome == "invalid"
    assert port.proposals == []


def test_invalid_keeps_raw_artifact_and_empty_has_reason():
    port = DiscoveryAgent().propose([_opp()], fill=_fill_with({"statement": "x"}))
    assert port.captures[0].artifact == {"statement": "x"}
    port = DiscoveryAgent().propose([_opp()], fill=lambda r: None)
    assert port.captures[0].validation["errors"] == ["filler returned no result"]


def test_unregistered_kind_is_skipped_without_capture():
    opp = {"id": "x:1", "source": "x:1", "action_type": "branch"}
    called = []
    port = DiscoveryAgent().propose([opp], fill=lambda r: called.append(r))
    assert port.captures == [] and called == []
    assert port.skipped[0]["kind"] == "propose_study"
    assert port.skipped[0]["reason"]


def test_native_kind_is_filled_and_captured_not_skipped():
    # interpret_finding is a NATIVE kind (native apply handler, no registry skill). It must be
    # filled + captured, not dropped — registry-only routing used to skip the whole interpretation
    # frontier of an investigation before the filler ever ran (the campaign's applied:0 stall).
    opp = {"id": "chal:s:o", "source": "s:o", "action_type": "synthesize", "kind": "open_challenge"}
    called = []

    def fill(req):
        called.append(req.kind)
        return GenerationResult(request_id=req.id,
                                artifact={"study": "s", "finding": "f", "statement": "interpreted"},
                                filler={"kind": "ai", "id": "stub", "model": "m"})

    port = DiscoveryAgent().propose([opp], fill=fill)
    assert called == ["interpret_finding"]                     # the filler WAS invoked
    assert port.skipped == []
    assert [c.kind for c in port.captures] == ["interpret_finding"]
    assert port.captures[0].outcome == "proposed"
    assert port.captures[0].skill == "native:interpret_finding"
    assert len(port.proposals) == 1


def test_ablated_registry_skill_is_not_resurrected_via_default_registry():
    # A trimmed registry (what the benchmark matrix passes to ablate a skill) must actually drop
    # its removed skill. The native-kind check must test native handlers ONLY, never fall back to
    # the default registry — otherwise an ablated registry kind gets filled anyway.
    class _EmptyRegistry:
        def get(self, kind):
            return None

    called = []
    port = DiscoveryAgent(registry=_EmptyRegistry()).propose([_opp()], fill=lambda r: called.append(r))
    assert called == []                                    # hypothesize (a registry kind) NOT filled
    assert port.captures == []
    assert port.skipped and port.skipped[0]["kind"] == "hypothesize"
