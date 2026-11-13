"""Pure emission projection: the engine's frontier opportunities -> typed
GenerationRequests (Phase 1 of docs/specs/2026-10-30-ai-drivable-discovery-cycle.md).

This is a projection, not an action. It reads the already-derived
``discovery_engine.opportunities(...)`` list (which the frontier builds from the
cycle result + inv_spec, folding in decisions, capability gaps, and viz state)
and returns the typed request envelope the workbench panel renders and a filler
(a /viva-* skill, or an AI filler) consumes. No disk I/O, no LLM, no writes —
the engine stays pure; filling happens across the seam.
"""
from __future__ import annotations

from discovery_engine.phases.generation import GateConfig, GenerationRequest

# Frontier op (opportunity["action_type"]) -> request kind. Ops deliberately
# absent are NOT generative acts: ``revalidate`` (stale validation) is a
# deterministic re-run on the existing spine, not something a filler authors.
_OP_TO_KIND = {
    "branch": "propose_study",       # unaddressed_criterion -> a new study
    "test": "author_tests",          # pending_gate / open_question -> author the gate's tests
    "steer": "resolve_decision",     # open_decision -> the expert/AI decides
    "synthesize": "interpret_finding",   # open_challenge -> adjudicate into a finding
    "reconcile": "interpret_finding",    # divergence -> reconcile into a finding
    "revise": "interpret_finding",       # validation_finding -> revise the interpretation
    "revise_model": "acquire_model",     # revision_proposed / model_disagreement / anomaly
    "build_process": "acquire_model",    # capability_gap -> build a process/model
    "build_viz": "visualize",
    "refine_viz": "visualize",
    "relocate_viz": "visualize",
}

# The contract each kind's filled artifact must satisfy (written back into the
# existing structured fields by the apply step; Phase 1 only emits).
_PRODUCES = {
    "propose_study": "study.yaml/v4",
    "acquire_model": "model_spec/v1",
    "author_tests": "behavior_tests[]",
    "interpret_finding": "finding/v1",
    "resolve_decision": "decision/v1",
    "explain": "explanation/v1",
    "visualize": "viz_spec/v1",
    "hypothesize": "hypothesis_proposal/v1",
    "critique": "critique_report/v1",
}

_CONTEXT_KEYS = ("kind", "competing_predictions", "unknowns", "confidence", "state", "priority",
                 "observed")


def _request_kind(opp: dict) -> str | None:
    op = opp.get("action_type")
    # A ``branch`` that fills a missing narrative is an ``explain`` (write the
    # prose for a promoted claim), not a proposal for a new study.
    if op == "branch" and opp.get("kind") == "narrative_gap":
        return "explain"
    # A raw surprise (anomaly / models disagreeing) is first a hypothesis to
    # generate, not yet a model to acquire; revision_proposed stays acquire_model.
    if opp.get("kind") in ("anomaly", "model_disagreement"):
        return "hypothesize"
    if opp.get("kind") == "verdict_overreach":
        return "critique"
    return _OP_TO_KIND.get(op)


def generation_requests(opportunities, *, gates: GateConfig | None = None,
                        inv_spec: dict | None = None) -> list[GenerationRequest]:
    """Project the frontier ``opportunities`` into typed :class:`GenerationRequest`s.

    Pure. ``opportunities`` is the list from ``discovery_engine.opportunities``.
    Each maps by its frontier op to one request kind; ops that are deterministic
    re-runs (``revalidate``) or unknown emit nothing. ``gates`` (default
    :class:`GateConfig`) stamps each request's gate. ``inv_spec`` is accepted for
    forward context enrichment (unused in Phase 1). Order follows the
    opportunities' own ranking (already sorted by -score, -priority, id).
    """
    gates = gates or GateConfig()
    out: list[GenerationRequest] = []
    for opp in opportunities or []:
        if not isinstance(opp, dict):
            continue
        rkind = _request_kind(opp)
        if rkind is None:
            continue
        target = opp.get("source") or opp.get("id") or ""
        if not target:
            continue
        context = {k: opp[k] for k in _CONTEXT_KEYS if opp.get(k) is not None}
        out.append(GenerationRequest(
            kind=rkind,
            target=str(target),
            produces=_PRODUCES[rkind],
            rationale=opp.get("question") or "",
            context=context,
            value=opp["value"] if isinstance(opp.get("value"), dict) else {},
            provenance={"source": "opportunities", "op": opp.get("action_type") or "",
                        "opportunity_id": opp.get("id") or ""},
            gate=gates.resolve(rkind),
        ))
    return out


def summarize_requests(requests) -> str:
    """A human-readable listing for the ``discover requests`` CLI."""
    reqs = list(requests)
    if not reqs:
        return "generation requests: none (frontier has no generative work)"
    lines = [f"generation requests: {len(reqs)}"]
    for r in reqs:
        score = ""
        if isinstance(r.value, dict) and r.value.get("score") is not None:
            score = f"  value={r.value['score']}"
        lines.append(f"  [{r.gate:6}] {r.kind:17} {r.target}{score}")
        if r.rationale:
            lines.append(f"            {r.rationale[:100]}")
    return "\n".join(lines)
