"""Skill-level invariants (the plan's required guards): truth-preservation, abstention, contrast."""
import yaml

from discovery_engine.phases.discrepancy import ANOMALY, build_discrepancy
from discovery_engine.phases.generation import GenerationRequest, GenerationResult
from discovery_engine.phases.generation_apply import apply_result, run_requests


def _ws(tmp_path):
    (tmp_path / "investigations" / "demo").mkdir(parents=True)
    (tmp_path / "investigations" / "demo" / "investigation.yaml").write_text(
        yaml.safe_dump({"name": "demo", "studies": ["s1"]}))
    (tmp_path / "studies" / "s1").mkdir(parents=True)
    (tmp_path / "studies" / "s1" / "study.yaml").write_text(yaml.safe_dump({
        "name": "s1", "findings": [{"id": "f1", "evidence": {"observed": 1.0, "from_test": "t"}}]}))
    return tmp_path


def _inv(ws):
    return yaml.safe_load((ws / "investigations" / "demo" / "investigation.yaml").read_text())


# --- truth-preservation: a filler can neither fabricate a Discovery nor alter a measurement ---

def test_hypothesize_filler_records_a_proposal_never_a_discovery(tmp_path):
    ws = _ws(tmp_path)
    req = GenerationRequest(kind="hypothesize", target="s1:obs", produces="hypothesis_proposal/v1",
                            gate="auto", provenance={"op": "revise_model"})
    res = GenerationResult(request_id=req.id, artifact={
        "statement": "A hidden regulator explains the surprise.",
        "mechanism": "feedback inhibition saturates the enzyme",
        "predictions": [{"observable": "obs", "expected": {"op": "<", "value": 0.2},
                         "falsifier": "obs >= 0.2 under the perturbation"}],
        "provenance": {"trigger": "anomaly", "source": "s1#obs"}})
    out = apply_result(req, res, ws_root=ws, inv_slug="demo")
    inv = _inv(ws)
    assert out["status"] == "applied"
    assert inv["hypothesis_proposals"][-1]["status"] == "proposed"   # recorded as a proposal
    assert not inv.get("discoveries")                                 # NEVER a discovery via the seam


def test_interpret_finding_cannot_overwrite_the_measurement(tmp_path):
    ws = _ws(tmp_path)
    req = GenerationRequest(kind="interpret_finding", target="claim:s1", produces="finding/v1",
                            gate="auto", provenance={"op": "synthesize"})
    res = GenerationResult(request_id=req.id, artifact={
        "study": "s1", "finding": "f1", "statement": "an interpretation",
        "observed": 999, "evidence": {"observed": 999}})       # injected measured value
    apply_result(req, res, ws_root=ws, inv_slug="demo")
    s = yaml.safe_load((ws / "studies" / "s1" / "study.yaml").read_text())
    assert s["findings"][0]["evidence"]["observed"] == 1.0     # measurement preserved


# --- calibration / abstention: an honest no-answer queues, it does not fabricate ---

def test_abstention_queues_without_writing(tmp_path):
    ws = _ws(tmp_path)
    reqs = [GenerationRequest(kind="resolve_decision", target="d1", produces="decision/v1",
                              gate="auto", provenance={"op": "steer"})]
    outs = run_requests(reqs, fill=lambda r: None, ws_root=ws, inv_slug="demo")  # the filler abstains
    assert outs[0]["outcome"] == "queued_for_review"
    assert not _inv(ws).get("resolutions")                     # nothing fabricated


def test_malformed_artifact_is_an_error_not_a_silent_apply(tmp_path):
    ws = _ws(tmp_path)
    reqs = [GenerationRequest(kind="hypothesize", target="s1:obs", produces="hypothesis_proposal/v1",
                              gate="auto", provenance={"op": "revise_model"})]
    # no mechanism / no falsifiable prediction -> the honesty gate rejects it
    bad = lambda r: GenerationResult(request_id=r.id, artifact={"statement": "vibes"})
    outs = run_requests(reqs, fill=bad, ws_root=ws, inv_slug="demo")
    assert outs[0]["outcome"] == "error"
    assert not _inv(ws).get("hypothesis_proposals")


# --- contrast sensitivity: a trigger's identity is paraphrase-invariant but evidence-sensitive ---

def test_discrepancy_identity_is_paraphrase_invariant_but_evidence_sensitive():
    a = build_discrepancy("s1", "obs.val", kind=ANOMALY, note="observed is high", observed=0.9)
    b = build_discrepancy("s1", "obs.val", kind=ANOMALY, note="the value sits above the band",
                          observed=0.9)                          # same surprise, paraphrased
    c = build_discrepancy("s1", "obs.other", kind=ANOMALY, observed=0.9)  # different observable
    assert a["id"] == b["id"]        # paraphrase does not flip the trigger identity
    assert a["id"] != c["id"]        # a different observable is a different surprise
