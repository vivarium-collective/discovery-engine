"""Typed Discrepancy: the sourced trigger a generated hypothesis answers."""
import json
from pathlib import Path

import pytest

from discovery_engine.phases.discrepancy import (
    ANOMALY,
    KINDS,
    MODEL_DISAGREEMENT,
    build_discrepancy,
    discrepancies_from_cycle,
    validate_discrepancy,
)

_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1]
     / "discovery_engine" / "schemas" / "discrepancy.schema.json").read_text())


def test_discrepancy_is_typed_and_sourced():
    d = build_discrepancy("s1", "obs.val", kind=ANOMALY, observed=0.95)
    assert d["id"].startswith("dc:")
    assert d["kind"] in KINDS
    assert d["source_evidence_ref"] == "s1#obs.val"   # always names its evidence
    assert d["observed"] == 0.95
    # the built object satisfies the published JSON schema
    import jsonschema
    jsonschema.validate(json.loads(json.dumps(d)), _SCHEMA)


def test_id_is_content_addressed_and_stable():
    a = build_discrepancy("s1", "obs.val", kind=ANOMALY)
    b = build_discrepancy("s1", "obs.val", kind=ANOMALY)
    c = build_discrepancy("s1", "obs.val", kind=MODEL_DISAGREEMENT)
    assert a["id"] == b["id"] and a["id"] != c["id"]


def test_validate_rejects_unknown_kind_and_missing_source():
    with pytest.raises(ValueError):
        validate_discrepancy({"study": "s", "observable": "o", "kind": "nope",
                              "source_evidence_ref": "s#o"})
    with pytest.raises(ValueError):
        validate_discrepancy({"study": "s", "observable": "o", "kind": ANOMALY,
                              "source_evidence_ref": ""})


def test_from_cycle_wraps_anomalies_and_live_disagreements():
    bundle = {"signals": {
        "anomalies": [{"study": "s1", "observable": "obs.val", "observed": 0.95,
                       "note": "outside every live band"}],
        "model_disagreements": [
            {"study": "s2", "observable": "flux", "arms": {"a": 1.0, "b": 2.0},
             "spread": 1.0, "disagreement": True},
            {"study": "s3", "observable": "x", "arms": {"a": 1.0, "b": 1.0},
             "spread": 0.0, "disagreement": False},   # resolved -> skipped
        ],
    }}
    ds = discrepancies_from_cycle(bundle)
    kinds = {(d["study"], d["kind"]) for d in ds}
    assert ("s1", ANOMALY) in kinds
    assert ("s2", MODEL_DISAGREEMENT) in kinds
    assert not any(d["study"] == "s3" for d in ds)    # the resolved disagreement is not a discrepancy
    assert all(d["source_evidence_ref"] for d in ds)


def test_from_cycle_empty_signals_is_empty():
    assert discrepancies_from_cycle({}) == []
    assert discrepancies_from_cycle({"signals": {}}) == []
