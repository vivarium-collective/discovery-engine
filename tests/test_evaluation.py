"""Scientific evaluation + integration (DE 2.0 Wave 3/4 / Cycle N+1 Phase F).

Gate: a claim backed only by PROVISIONAL evidence is provisionally-supported, never
promoted; model disagreement is surfaced not averaged; an upstream change invalidates
dependents; the DiscoveryDelta records what the cycle established and what it gated.
"""
from __future__ import annotations

from discovery_engine import evaluation as E
from discovery_engine.evidence_integrity import VERIFIED, PROVISIONAL


def test_claim_support_gated_by_evidence_integrity():
    assert E.evaluate_claim_support({"net": 2}, VERIFIED) == E.SUPPORTED
    assert E.evaluate_claim_support({"net": 2}, PROVISIONAL) == E.PROVISIONAL_SUPPORT
    assert E.evaluate_claim_support({"net": 2}, None) == E.PROVISIONAL_SUPPORT
    assert E.evaluate_claim_support({"net": -1}, VERIFIED) == E.CONTESTED
    assert E.evaluate_claim_support({"net": 0}, VERIFIED) == E.UNRESOLVED


def _inv_and_studies():
    inv = {"studies": ["s2", "s3"], "hypotheses": [
        {"id": "h-s2-spatial", "study": "s2", "status": "open",
         "predictions": [{"observable": "flux", "when": {"structure": "proximate"}, "expected": {"op": ">=", "value": 1e-22}}]},
        {"id": "h-s2-null", "study": "s2", "status": "open",
         "predictions": [{"observable": "flux", "when": {"structure": "proximate"}, "expected": {"op": "<=", "value": 5e-23}}]},
        {"id": "h-s3-topology", "study": "s3", "status": "open",
         "predictions": [{"observable": "enclosure", "expected": {"op": ">=", "value": 0.5}}]}]}
    studies = {
        "s2": {"name": "s2", "findings": [
            {"id": "f-s2", "evidence": {"measure": "flux", "observed": 1e-22,
                                        "conditions": {"structure": "proximate"},
                                        "from_test": "flux_gate"}}]},
        "s3": {"name": "s3", "findings": [
            {"id": "f-s3", "evidence": {"measure": "enclosure", "observed": 0.61,
                                        "by_topology": {"classical": 0.27, "inside_out": 0.83},
                                        "superseded": {"source": "CPM", "observed": 1.0}}}]}}
    return inv, studies


def test_compare_hypotheses_yields_verdicts_and_favored():
    inv, studies = _inv_and_studies()
    cmp = E.compare_hypotheses(inv, studies)
    assert "s2" in cmp and "s3" in cmp
    assert set(h["id"] for h in cmp["s2"]["hypotheses"]) == {"h-s2-spatial", "h-s2-null"}
    # every verdict is drawn from the claim-verdict vocabulary
    assert all(h["verdict"] in (E.SUPPORTED, E.PROVISIONAL_SUPPORT, E.CONTESTED, E.UNRESOLVED)
               for h in cmp["s2"]["hypotheses"])


def test_model_disagreement_surfaced_not_averaged():
    inv, studies = _inv_and_studies()
    dmap = E.model_disagreement_map(inv, studies)
    s3 = next(d for d in dmap if d["study"] == "s3")
    assert s3["disagreement"] is True          # 0.27 … 1.0 spans > 0.2
    assert "superseded:CPM" in s3["arms"] and s3["arms"]["superseded:CPM"] == 1.0
    assert "do not average" in s3["note"]


def test_invalidate_dependents_marks_downstream_review_required():
    inv, studies = _inv_and_studies()
    affected = E.invalidate_dependents({"kind": "model", "id": "s3"}, inv, studies)
    arts = {a["artifact"]: a["status"] for a in affected}
    assert arts.get("hypothesis:h-s3-topology") == "REVIEW_REQUIRED"
    assert arts.get("claim:s3") == "REVIEW_REQUIRED"
    assert arts.get("narrative") == "STALE"


def test_adjudicated_supersession_is_not_a_live_disagreement():
    """A supersession a higher-fidelity run has adjudicated (``adjudicated_by``), on a
    finding with a resolved numeric observed, is resolved history — not gated, not a
    live model disagreement."""
    inv = {"studies": ["s3"], "hypotheses": [
        {"id": "h-s3-reach", "study": "s3",
         "predictions": [{"observable": "enclosure", "expected": {"op": ">=", "value": 0.5}}]}]}
    studies = {"s3": {"name": "s3", "findings": [
        {"id": "f-s3", "evidence": {"measure": "enclosure", "observed": 0.61,
                                    "by_topology": {"classical": 0.27, "inside_out": 0.83},
                                    "superseded": {"source": "CPM", "observed": 1.0,
                                                   "adjudicated_by": "run:membrane-arbiter"}}}]}}
    dmap = E.model_disagreement_map(inv, studies)
    assert all(not d["disagreement"] for d in dmap)          # no live disagreement
    cmp = E.compare_hypotheses(inv, studies)
    assert cmp["s3"]["status"] != E.GATED                     # gate lifted


def test_discovery_delta_records_disagreements_and_fingerprint():
    inv, studies = _inv_and_studies()
    delta = E.discovery_delta(inv, studies)
    assert any(d["study"] == "s3" for d in delta["model_disagreements"])
    assert any(r["study"] == "s3" for r in delta["regressions"])   # CPM superseded = recorded progress
    assert len(delta["fingerprint"]) == 16
