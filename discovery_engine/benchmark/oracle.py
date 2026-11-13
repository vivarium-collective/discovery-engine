"""Deterministic oracle filler: authors the ground-truth ModelAcquisitionProposal."""
from __future__ import annotations

from discovery_engine.phases.generation import GenerationResult


def oracle_filler(ground_truth):
    def fill(req):
        if req.kind != "acquire_model":
            return None
        art = {"mechanism": ground_truth.concealed_mechanism,
               "expected_behavioral_test": {"behavior_test": "enc_in_band",
                                            "pass_if": ground_truth.band},
               "provenance": {"trigger": "needs_author_mechanism", "source": req.target,
                              "by": "oracle"}}
        return GenerationResult(request_id=req.id, artifact=art,
                                filler={"kind": "ai", "id": "oracle", "model": "oracle"})
    return fill
