"""EvidenceRecord: quantitative predicted-vs-observed, derived from grading output."""
import json
from pathlib import Path

from discovery_engine.study.evaluator.evidence_record import (
    band_prediction,
    build_evidence_record,
    evidence_records_for_run,
    evidence_records_for_study,
)

_SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1]
     / "discovery_engine" / "schemas" / "evidence-record.schema.json").read_text())


def test_band_prediction_range_threshold_equality():
    assert band_prediction({"low": 0.3, "high": 0.8}) == (0.55, 0.25)
    assert band_prediction({"op": "<", "threshold": 0.2}) == (0.2, None)
    assert band_prediction({"value": 1.0, "tolerance": 0.1}) == (1.0, 0.1)
    assert band_prediction({}) == (None, None)


def test_record_has_residual_within_and_immutable_source():
    rec = build_evidence_record(
        "obs.val",
        predicted={"value": 0.55, "uncertainty": 0.25, "band": {"low": 0.3, "high": 0.8}},
        observed={"value": 0.6},
        source={"run_id": "r1", "model_version": "mv:abc", "grader": "3-freshness"})
    assert abs(rec["residual"] - 0.05) < 1e-9
    assert rec["within_uncertainty"] is True
    assert rec["source"]["run_id"] == "r1" and rec["source"]["model_version"] == "mv:abc"
    import jsonschema
    jsonschema.validate(json.loads(json.dumps(rec)), _SCHEMA)


def test_out_of_band_observation_is_not_within_uncertainty():
    rec = build_evidence_record(
        "obs.val", predicted={"value": 0.55, "uncertainty": 0.25},
        observed={"value": 0.95})
    assert abs(rec["residual"] - 0.40) < 1e-9
    assert rec["within_uncertainty"] is False


def test_non_numeric_observed_yields_null_residual():
    rec = build_evidence_record("o", predicted={"value": 1.0, "uncertainty": 0.1},
                                observed={"value": None})
    assert rec["residual"] is None and rec["within_uncertainty"] is None


def test_records_for_run_derive_from_computed_outcomes():
    study = {
        "behavior_tests": [{"name": "t", "measure": {"kind": "range_check", "path": "obs.val"},
                            "pass_if": {"low": 0.3, "high": 0.8}}],
        "runs": [{"name": "r1", "model_version": "mv:abc",
                  "computed_outcomes": {"t": {"result": "pass", "measured_value": 0.55,
                                              "evaluated_by": "code"},
                                        "_grader": {"version": "3-freshness"}}}],
    }
    recs = evidence_records_for_run(study, study["runs"][0])
    r = recs["t"]
    assert r["observable"] == "obs.val"
    assert r["predicted"]["value"] == 0.55 and r["observed"]["value"] == 0.55
    assert r["residual"] == 0.0 and r["within_uncertainty"] is True
    assert r["source"]["grader"] == "3-freshness" and r["source"]["model_version"] == "mv:abc"
    # the derived record satisfies the schema; the _grader meta key is skipped
    import jsonschema
    jsonschema.validate(json.loads(json.dumps(r)), _SCHEMA)
    assert evidence_records_for_study(study) == {"r1": recs}
