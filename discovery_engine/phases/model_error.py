"""FR-07.04 — model-error diagnosis: on a refutation, the MODEL or the HYPOTHESIS?

A refuted claim can mean two different things, and they lead to different next studies:

* ``revise-model`` — competing model arms disagree on the refuting observable, so the
  model *choice* is implicated. Build/fix the mechanism, don't touch the claim.
* ``revise-hypothesis`` — no live model disagreement; the model is coherent and the
  hypothesis's own prediction was wrong. Revise the claim and re-test.

The typed ``EvidenceUpdate`` this produces steers *which* child seed
``suggest_studies`` emits — a model-revision study vs a hypothesis-revision study.
Pure.
"""
from __future__ import annotations

REVISE_MODEL = "revise-model"
REVISE_HYPOTHESIS = "revise-hypothesis"


def diagnose_refutation(parent_slug: str, finding: dict, inv_spec: dict, studies: dict) -> dict:
    """The ``EvidenceUpdate`` for a refuting finding:
    ``{study, finding, diagnosis, rationale}``. ``diagnosis`` is ``revise-model`` when a
    live model disagreement stands on the study's refuting observable, else
    ``revise-hypothesis``. Pure."""
    from discovery_engine.phases.evaluation import model_disagreement_map
    disagree = any(d.get("study") == parent_slug and d.get("disagreement")
                   for d in model_disagreement_map(inv_spec, studies))
    if disagree:
        diagnosis = REVISE_MODEL
        rationale = ("competing model arms disagree on the refuting observable — the model "
                     "choice is implicated, so revise the model/mechanism, not the hypothesis.")
    else:
        diagnosis = REVISE_HYPOTHESIS
        rationale = ("no live model disagreement on the refuting observable — the model is "
                     "coherent and the hypothesis's prediction was refuted, so revise the "
                     "hypothesis and re-test.")
    return {"study": parent_slug, "finding": (finding or {}).get("id"),
            "diagnosis": diagnosis, "rationale": rationale}


def steer_refutation_proposal(proposal: dict, update: dict) -> dict:
    """Rewrite a refuted-finding child ``proposal`` in place so the EvidenceUpdate
    changes *which* seed is emitted: a model-revision vs a hypothesis-revision study
    (title, motivation, and the seed's own framing). Returns the proposal. Pure w.r.t.
    everything but the passed proposal dict."""
    diagnosis = update.get("diagnosis")
    slug = update.get("study")
    base = str(proposal.get("motivation") or "")
    proposal["evidence_update"] = update
    seed = proposal.get("seed") if isinstance(proposal.get("seed"), dict) else None
    if diagnosis == REVISE_MODEL:
        proposal["title"] = f"Revise the model — refuted finding in {slug}"
        proposal["motivation"] = (f"Model-error diagnosis ({REVISE_MODEL}): {update['rationale']} {base}").strip()[:300]
        if seed is not None:
            mc = dict(seed.get("model_change") or {})
            mc["diagnosis"] = REVISE_MODEL
            mc.setdefault("notes", "Revise the model mechanism the refutation implicates.")
            seed["model_change"] = mc
    else:
        proposal["title"] = f"Revise the hypothesis — refuted finding in {slug}"
        proposal["motivation"] = (f"Model-error diagnosis ({REVISE_HYPOTHESIS}): {update['rationale']} {base}").strip()[:300]
        if seed is not None:
            seed["revise_hypothesis"] = True
    return proposal
