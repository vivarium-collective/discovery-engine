"""Evidence authority: an applied artifact never rewrites a measured/derived value."""
import pytest
import yaml

from discovery_engine.phases.evidence_authority import (
    assert_no_authority_leak,
    measured_fingerprint,
)
from discovery_engine.phases.generation import GenerationRequest, GenerationResult
from discovery_engine.phases.generation_apply import apply_result


def test_fingerprint_captures_observed_and_computed_outcomes():
    study = {
        "findings": [{"id": "f1", "evidence": {"observed": 0.9, "measured_value": 0.9}}],
        "runs": [{"name": "r1", "computed_outcomes": {"t": {"result": "pass"}}}],
    }
    fp = measured_fingerprint(study)
    assert fp["findings[f1].evidence.observed"] == 0.9
    assert fp["runs[r1].computed_outcomes"] == {"t": {"result": "pass"}}


def test_leak_raises_when_observed_changes():
    before = {"findings": [{"id": "f1", "evidence": {"observed": 0.9}}]}
    after = {"findings": [{"id": "f1", "evidence": {"observed": 0.5}}]}   # measured value rewritten
    with pytest.raises(ValueError, match="authority leak"):
        assert_no_authority_leak(before, after)


def test_prose_only_change_passes():
    before = {"findings": [{"id": "f1", "evidence": {"observed": 0.9}, "statement": "old"}]}
    after = {"findings": [{"id": "f1", "evidence": {"observed": 0.9}, "statement": "new"}]}
    assert_no_authority_leak(before, after)   # interpretation changed, measurement did not


def test_leak_raises_when_computed_outcomes_change():
    before = {"runs": [{"name": "r1", "computed_outcomes": {"t": {"measured_value": 1.0}}}]}
    after = {"runs": [{"name": "r1", "computed_outcomes": {"t": {"measured_value": 2.0}}}]}
    with pytest.raises(ValueError, match="authority leak"):
        assert_no_authority_leak(before, after)


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


def test_apply_interpret_finding_ignores_injected_measured_value(tmp_path):
    """An artifact that smuggles an `observed` key alongside its prose writes the prose
    and leaves the measurement untouched — the guard confirms no authority leak."""
    ws = _ws(tmp_path)
    req = GenerationRequest(kind="interpret_finding", target="claim:s1",
                            produces="finding/v1", provenance={"op": "synthesize"})
    res = GenerationResult(request_id=req.id, artifact={
        "study": "s1", "finding": "f1",
        "statement": "The measured rate sits inside the window.",
        "observed": 0.0,            # injected — must be ignored, never applied
        "evidence": {"observed": 0.0},
    })
    out = apply_result(req, res, ws_root=ws, inv_slug="demo")
    assert out["status"] == "applied"
    s = yaml.safe_load((ws / "studies" / "s1" / "study.yaml").read_text())
    f = s["findings"][0]
    assert f["statement"].startswith("The measured rate")
    assert f["evidence"]["observed"] == 1.0    # measurement unchanged; guard did not fire
