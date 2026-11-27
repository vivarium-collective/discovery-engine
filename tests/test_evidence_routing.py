"""Within-investigation evidence routing + EvidenceStep (WP2 seam)."""
from discovery_engine.projection.evidence_routing import referenced_claims, study_evidence


def test_referenced_claims_reads_knowledge_inputs():
    spec = {"knowledge_inputs": {"claims": ["claim:alpha", "", "claim:beta"]}}
    assert referenced_claims(spec) == ["claim:alpha", "claim:beta"]
    assert referenced_claims({}) == []


def test_verdict_routes_as_evidence_about_referenced_claims():
    spec = {"verdict": "supported",
            "knowledge_inputs": {"claims": ["claim:shared-X", "claim:shared-Y"]}}
    evs = study_evidence(spec, "my-study")
    assert len(evs) == 2
    assert {e["claim_ref"] for e in evs} == {"claim:shared-X", "claim:shared-Y"}
    assert all(e["polarity"] == "supports" for e in evs)       # supported -> supports
    assert all(e["from_study"] == "my-study" for e in evs)
    assert all(e["id"].startswith("ev-") for e in evs)


def test_polarity_follows_the_verdict():
    assert study_evidence({"verdict": "refuted",
                           "knowledge_inputs": {"claims": ["claim:c"]}}, "s")[0]["polarity"] == "challenges"
    assert study_evidence({"verdict": "qualified",
                           "knowledge_inputs": {"claims": ["claim:c"]}}, "s")[0]["polarity"] == "qualifies"


def test_legacy_study_falls_back_to_self_evidence():
    # a study that references no claims still produces evidence about its own claim
    evs = study_evidence({"verdict": "supported"}, "legacy-study")
    assert len(evs) == 1
    assert evs[0]["claim_ref"] == "claim:legacy-study"
    assert evs[0]["polarity"] == "supports"


def test_evidence_step_reads_a_spec_or_a_context():
    from discovery_engine.integration.evidence_step import EvidenceStep
    import process_bigraph
    core = process_bigraph.ProcessTypes() if hasattr(process_bigraph, "ProcessTypes") else None
    step = EvidenceStep.__new__(EvidenceStep)          # bypass core wiring for a unit test
    step.config = {}
    # raw spec dict as the study input
    spec = {"name": "s1", "verdict": "refuted", "knowledge_inputs": {"claims": ["claim:z"]}}
    out = step.update({"study": spec})
    assert out["evidence"][0]["claim_ref"] == "claim:z"
    assert out["evidence"][0]["polarity"] == "challenges"

    # a StudyContext-like object (has .spec + .study_name)
    class _Ctx:
        spec = {"verdict": "supported", "knowledge_inputs": {"claims": ["claim:w"]}}
        study_name = "ctx-study"
    out2 = step.update({"study": _Ctx()})
    assert out2["evidence"][0]["from_study"] == "ctx-study"
    assert out2["evidence"][0]["polarity"] == "supports"

    assert step.update({"study": None}) == {"evidence": []}
