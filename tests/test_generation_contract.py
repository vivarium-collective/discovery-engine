"""Phase 1 contract types: GenerationRequest (id + schema) and GateConfig."""
import pytest

from discovery_engine.generation import (
    GateConfig,
    GenerationRequest,
    validate_request,
)


def _req(**kw):
    base = {"kind": "propose_study", "target": "inv-x", "produces": "study.yaml/v4",
            "provenance": {"source": "opportunities", "op": "branch", "opportunity_id": "o1"}}
    base.update(kw)
    return GenerationRequest(**base)


def test_id_is_stable_and_content_addressed():
    a = _req()
    b = _req()  # same kind/target/provenance -> same id
    assert a.id == b.id and a.id.startswith("gr-")
    # a different target (or provenance) changes the id
    assert _req(target="inv-y").id != a.id
    assert _req(provenance={"op": "steer"}).id != a.id


def test_rejects_unknown_kind_and_gate():
    with pytest.raises(ValueError):
        _req(kind="not_a_kind")
    with pytest.raises(ValueError):
        _req(gate="maybe")
    with pytest.raises(ValueError):
        GenerationRequest(kind="explain", target="", produces="explanation/v1")


def test_validate_request_against_schema():
    validate_request(_req().to_dict())                       # valid passes
    with pytest.raises(ValueError):
        validate_request({"id": "x", "kind": "explain"})     # missing target/produces
    with pytest.raises(ValueError):
        validate_request({**_req().to_dict(), "surprise": 1})  # strict: unknown top-level field


def test_gateconfig_resolution_and_presets():
    gc = GateConfig()  # defaults
    assert gc.resolve("propose_study") == "review"
    assert gc.resolve("visualize") == "auto"
    assert GateConfig(default="auto").resolve("propose_study") == "auto"
    assert GateConfig(by_kind={"visualize": "review"}).resolve("visualize") == "review"
    assert GateConfig.preset("free_run").resolve("propose_study") == "auto"
    assert GateConfig.preset("all_gated").resolve("visualize") == "review"
    assert GateConfig.preset("propose_review").resolve("author_tests") == "auto"
    assert GateConfig.preset("propose_review").resolve("propose_study") == "review"
