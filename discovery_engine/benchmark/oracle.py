"""Deterministic oracle fillers: author the ground-truth proposals a skill needs.

The oracle answers two request kinds the benchmark scenario raises:

- ``acquire_model`` — a ``ModelAcquisitionProposal`` naming a mechanism (the concealed
  truth for the perfect oracle, a decoy for the negative control);
- ``critique`` — a ``CritiqueReport`` challenging the study's overreaching verdict.

Only the acquire mechanism differs between the perfect and decoy oracles; both critique
identically, so ``full_decoy`` isolates the mechanism string from everything else.
"""
from __future__ import annotations

from discovery_engine.phases.generation import GenerationResult


def _result(req, artifact):
    return GenerationResult(request_id=req.id, artifact=artifact,
                            filler={"kind": "ai", "id": "oracle", "model": "oracle"})


def _proposal(ground_truth, mechanism, req):
    """A well-formed ModelAcquisitionProposal naming ``mechanism``.

    The artifact clears the coerce honesty gate (mechanism present, falsifiable
    ``expected_behavioral_test``, ``provenance.trigger``) regardless of which
    mechanism is named — the gate checks form, not correctness. That is what lets
    the decoy filler record a wrong-but-valid proposal.
    """
    return _result(req, {
        "mechanism": mechanism,
        "expected_behavioral_test": {"behavior_test": "enc_in_band", "pass_if": ground_truth.band},
        "provenance": {"trigger": "needs_author_mechanism", "source": req.target, "by": "oracle"}})


def _critique(req):
    """A well-formed CritiqueReport challenging the study's overreaching verdict.

    Clears ``coerce_critique_report`` (a target, >=1 non-empty section, a provenance
    trigger). Identical for the perfect and decoy oracles: critique is not what the
    decoy control perturbs."""
    return _result(req, {
        "target": req.target,
        "unsupported_claims": [{"claim": "authored gate_status outruns the evidence",
                                 "why": "no verified run supports the asserted verdict"}],
        "provenance": {"trigger": "verdict_divergence", "source": req.target, "by": "oracle"}})


def _make_filler(ground_truth, acquire_mechanism):
    def fill(req):
        if req.kind == "acquire_model":
            return _proposal(ground_truth, acquire_mechanism, req)
        if req.kind == "critique":
            return _critique(req)
        return None
    return fill


def oracle_filler(ground_truth):
    """Perfect oracle: proposes the scenario's concealed (correct) mechanism and critiques."""
    return _make_filler(ground_truth, ground_truth.concealed_mechanism)


def decoy_oracle_filler(ground_truth):
    """Negative control: proposes a well-formed but WRONG (decoy) mechanism; critiques as usual.

    Records a real ModelAcquisitionProposal (so ``proposal_count >= 1``) whose mechanism is
    ``ground_truth.decoy_mechanism`` rather than the concealed truth. This makes the
    ``mechanism == concealed_mechanism`` comparison in ``score()`` load-bearing:
    ``mechanism_proposed`` must be False here despite a proposal being recorded.
    """
    return _make_filler(ground_truth, ground_truth.decoy_mechanism)
