"""Scientific evaluation + integration (DE 2.0 Wave 3/4 / Cycle N+1 Phase F).

Gate: a claim backed only by PROVISIONAL evidence is provisionally-supported, never
promoted; model disagreement is surfaced not averaged; an upstream change invalidates
dependents; the DiscoveryDelta records what the cycle established and what it gated.
"""
from __future__ import annotations

from discovery_engine import evaluation as E
from discovery_engine.evidence_integrity import VERIFIED, PROVISIONAL


def test_claim_support_gated_by_evidence_integrity():
    assert E.evaluate_claim_support({"net": 2}, VERIFIED, has_quantitative=True) == E.SUPPORTED
    assert E.evaluate_claim_support({"net": 2}, PROVISIONAL, has_quantitative=True) == E.PROVISIONAL_SUPPORT
    assert E.evaluate_claim_support({"net": 2}, None, has_quantitative=True) == E.PROVISIONAL_SUPPORT
    assert E.evaluate_claim_support({"net": -1}, VERIFIED, has_quantitative=True) == E.CONTESTED
    assert E.evaluate_claim_support({"net": 0}, VERIFIED, has_quantitative=True) == E.UNRESOLVED


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


def _quant_case(spec_name="s2"):
    inv = {"studies": ["s2"], "hypotheses": [
        {"id": "h-q", "study": "s2", "status": "open",
         "predictions": [{"observable": "flux", "expected": {"op": ">=", "value": 1e-22}}]}]}
    studies = {"s2": {"name": spec_name, "findings": [
        {"id": "f", "evidence": {"measure": "flux", "observed": 1e-22}}]}}
    return inv, studies


def _verified(slug="s2"):
    return {"by_study": {slug: {"status": VERIFIED}}}


def test_prose_only_caps_at_provisional():
    inv = {"studies": ["s2"], "hypotheses": [{"id": "h-p", "study": "s2", "status": "open"}]}
    studies = {"s2": {"name": "s2", "discovery_implications": {"alternate_hypotheses": [
        {"hypothesis_id": "h-p", "claim": "c", "evidence_for": ["prose a", "prose b"]}]}}}
    cmp = E.compare_hypotheses(inv, studies, _verified())
    h = cmp["s2"]["hypotheses"][0]
    assert h["support"]["net"] > 0
    assert h["verdict"] == E.PROVISIONAL_SUPPORT
    assert E.evaluate_claim_support({"net": 2}, VERIFIED, has_quantitative=False) == E.PROVISIONAL_SUPPORT


def test_supported_needs_verified_and_quantitative():
    inv, studies = _quant_case()
    cmp = E.compare_hypotheses(inv, studies, _verified())
    assert cmp["s2"]["hypotheses"][0]["verdict"] == E.SUPPORTED
    assert cmp["s2"]["status"] == E.SUPPORTED
    assert cmp["s2"]["hypotheses"][0]["model_version"] is None
    # quantitative but unverified evidence still caps
    cmp2 = E.compare_hypotheses(inv, studies, {"by_study": {"s2": {"status": PROVISIONAL}}})
    assert cmp2["s2"]["hypotheses"][0]["verdict"] == E.PROVISIONAL_SUPPORT


def test_slug_join_matches_inv_studies():
    inv, studies = _quant_case(spec_name="s2-flux-full")   # spec name differs from slug
    cmp = E.compare_hypotheses(inv, studies, _verified())
    h = cmp["s2"]["hypotheses"][0]
    assert h["support"]["supports"] == 1
    assert h["verdict"] == E.SUPPORTED
    # support-log entries are keyed by the inv-studies slug, not the spec name
    dmap = E.model_disagreement_map(inv, {"s2": {"name": "other", "findings": [
        {"evidence": {"measure": "m", "by_arm": {"a": 0.1, "b": 0.9}}}]}})
    assert dmap[0]["study"] == "s2"


# ---- Task 7: snapshot + delta memory ----

def test_delta_prev_none_back_compat():
    inv, studies = _inv_and_studies()
    d = E.discovery_delta(inv, studies)
    assert d["prev_fingerprint"] is None and d["changed"] == [] and d["new_model_versions"] == []
    assert d["unchanged"] is False
    assert set(d["proposals"]) == {"opened", "applied", "evaluated"}


def test_identical_snapshots_unchanged():
    inv, studies = _inv_and_studies()
    cmp = E.compare_hypotheses(inv, studies)
    snap = E.snapshot_evaluation(cmp, None, [], n_anomalies=0)
    d = E.discovery_delta(inv, studies, None, prev=snap)
    assert d["unchanged"] is True and d["changed"] == [] and d["new_model_versions"] == []
    assert d["prev_fingerprint"] == snap["fingerprint"] == d["fingerprint"]


def test_flip_with_new_model_version_is_one_changed_row(monkeypatch):
    inv, studies = _inv_and_studies()
    real = E.compare_hypotheses

    def fake(i, s, e=None):
        out = real(i, s, e)
        out["s2"] = {**out["s2"], "status": E.SUPPORTED, "favored": "h-s2-spatial",
                     "model_version": "mv:new"}
        return out
    prev = E.snapshot_evaluation(real(inv, studies), None, [])
    prev["by_study"]["s2"]["status"] = "unassessed"
    monkeypatch.setattr(E, "compare_hypotheses", fake)
    d = E.discovery_delta(inv, studies, None, prev=prev)
    rows = [c for c in d["changed"] if c["study"] == "s2"]
    assert len(rows) == 1 and rows[0]["from"] == "unassessed" and rows[0]["to"] == E.SUPPORTED
    assert rows[0]["model_version_to"] == "mv:new"
    assert "mv:new" in d["new_model_versions"] and d["unchanged"] is False


def test_snapshot_shape_and_pure():
    inv, studies = _inv_and_studies()
    cmp = E.compare_hypotheses(inv, studies)
    snap = E.snapshot_evaluation(cmp, {"by_study": {"s2": {"status": VERIFIED}}}, [{"id": "mv:a"}])
    assert set(snap) == {"fingerprint", "by_study", "model_versions", "proposals"}
    assert snap["by_study"]["s2"]["evidence"] == VERIFIED
    assert set(snap["by_study"]["s2"]) == {"status", "favored", "evidence", "model_version"}
    assert snap["model_versions"] == ["mv:a"]


def test_compare_wires_favored_model_version():
    inv, studies = _quant_case()
    studies["s2"]["findings"][0]["provenance"] = {"model_version": "mv:abc"}
    cmp = E.compare_hypotheses(inv, studies, _verified())
    assert cmp["s2"]["favored"] == "h-q"
    assert cmp["s2"]["model_version"] == "mv:abc"
    assert cmp["s2"]["hypotheses"][0]["model_version"] == "mv:abc"
