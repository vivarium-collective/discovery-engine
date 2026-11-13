"""Deterministic oracle filler: authors the ground-truth ModelAcquisitionProposal."""
from __future__ import annotations

from discovery_engine.phases.generation import GenerationResult


def _proposal(ground_truth, mechanism, req):
    """A well-formed ModelAcquisitionProposal naming ``mechanism``.

    The artifact clears the coerce honesty gate (mechanism present, falsifiable
    ``expected_behavioral_test``, ``provenance.trigger``) regardless of which
    mechanism is named — the gate checks form, not correctness. That is what lets
    the decoy filler record a wrong-but-valid proposal.
    """
    art = {"mechanism": mechanism,
           "expected_behavioral_test": {"behavior_test": "enc_in_band",
                                        "pass_if": ground_truth.band},
           "provenance": {"trigger": "needs_author_mechanism", "source": req.target,
                          "by": "oracle"}}
    return GenerationResult(request_id=req.id, artifact=art,
                            filler={"kind": "ai", "id": "oracle", "model": "oracle"})


def oracle_filler(ground_truth):
    """Perfect oracle: always proposes the scenario's concealed (correct) mechanism."""
    def fill(req):
        if req.kind != "acquire_model":
            return None
        return _proposal(ground_truth, ground_truth.concealed_mechanism, req)
    return fill


def decoy_oracle_filler(ground_truth):
    """Negative control: proposes a well-formed but WRONG (decoy) mechanism.

    Records a real ModelAcquisitionProposal (so ``proposal_count >= 1``) whose
    mechanism is ``ground_truth.decoy_mechanism`` rather than the concealed truth.
    This makes the ``mechanism == concealed_mechanism`` comparison in ``score()``
    load-bearing: ``mechanism_proposed`` must be False here despite a proposal
    being recorded, which it cannot be if the metric merely counted proposals.
    """
    def fill(req):
        if req.kind != "acquire_model":
            return None
        return _proposal(ground_truth, ground_truth.decoy_mechanism, req)
    return fill
