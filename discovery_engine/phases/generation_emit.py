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

from discovery_engine.phases.generation import GateConfig, GenerationRequest, skill_for

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

_CONTEXT_KEYS = ("kind", "competing_predictions", "unknowns", "confidence", "state", "priority")


def _request_kind(opp: dict) -> str | None:
    op = opp.get("action_type")
    # A ``branch`` that fills a missing narrative is an ``explain`` (write the
    # prose for a promoted claim), not a proposal for a new study.
    if op == "branch" and opp.get("kind") == "narrative_gap":
        return "explain"
    # The anomaly's observed value rides in the request rationale (the
    # opportunity question); richer trigger enrichment is the coordinator's job.
    # A raw surprise (anomaly / models disagreeing) is first a hypothesis to
    # generate, not yet a model to acquire; revision_proposed stays acquire_model.
    # A proactive `coverage_gap` (a fundamental dimension no study probes, #2) is
    # also authored as a hypothesis — the agent writes the forward prediction about
    # what SHOULD happen on that dimension (trigger: coverage_gap), rather than the
    # engine proposing a bare study. The hypothesis then drives a discriminating
    # study via the existing suggest/discriminating path.
    if opp.get("kind") in ("anomaly", "model_disagreement", "coverage_gap"):
        return "hypothesize"
    if opp.get("kind") == "verdict_overreach":
        return "critique"
    return _OP_TO_KIND.get(op)


def _discrepancies_by_target(discrepancies) -> dict:
    """Index discrepancies by the ``study:observable`` key the anomaly / model_disagreement
    frontier items use as their target, so a hypothesize request can carry the typed surprise."""
    out: dict = {}
    for d in discrepancies or []:
        if isinstance(d, dict) and d.get("study") and d.get("observable"):
            out[f"{d['study']}:{d['observable']}"] = d
    return out


def _baseline_composite(spec: dict) -> str | None:
    """The study's declared baseline composite id — v4 ``conditions.baseline.composite`` first,
    else the first ``baseline``/``arms`` entry. Pure: reads only declared fields."""
    cond = (spec.get("conditions") or {}).get("baseline") or {}
    if isinstance(cond, dict) and cond.get("composite"):
        return str(cond["composite"])
    for arm in (spec.get("baseline") or spec.get("arms") or []):
        if isinstance(arm, dict) and arm.get("composite"):
            return str(arm["composite"])
    return None


def _declared_model_contract(study_spec) -> "dict | None":
    """A compact, HONEST summary of a study's model interface for the generation context, built
    ONLY from DECLARED fields — the baseline composite id, the study's declared observables, and
    any authored ``contracts`` / ``process_contracts``. It resolves/imports nothing and fabricates
    nothing, so surfacing it into a request's context lets the filler author tests / interpret a
    finding / acquire a model against the ports the model actually declares, instead of guessing,
    while staying inside the no-fabrication rule. Returns ``None`` when the study declares no
    model interface."""
    if not isinstance(study_spec, dict):
        return None
    out: dict = {}
    comp = _baseline_composite(study_spec)
    if comp:
        out["composite"] = comp
    obs = [o for o in (study_spec.get("observables") or []) if isinstance(o, str)]
    if obs:
        out["observables"] = obs
    contracts = study_spec.get("contracts") or study_spec.get("process_contracts")
    if contracts:
        out["contracts"] = contracts
    return out or None


def generation_requests(opportunities, *, gates: GateConfig | None = None,
                        inv_spec: dict | None = None,
                        discrepancies: list | None = None,
                        studies: dict | None = None) -> list[GenerationRequest]:
    """Project the frontier ``opportunities`` into typed :class:`GenerationRequest`s.

    Pure. ``opportunities`` is the list from ``discovery_engine.opportunities``.
    Each maps by its frontier op to one request kind; ops that are deterministic
    re-runs (``revalidate``) or unknown emit nothing. ``gates`` (default
    :class:`GateConfig`) stamps each request's gate. ``inv_spec`` is accepted for
    forward context enrichment. ``discrepancies`` (the cycle's typed anomaly /
    model-disagreement triggers) enrich each ``hypothesize`` request's context with
    the exact measured surprise it must explain, so a generated alternative is tied
    to evidence, not a prompt. ``studies`` (slug -> study spec) enriches a request
    that targets a study with that study's DECLARED model contract (baseline
    composite, observables, authored process contracts), so the filler reasons from
    the model's real interface rather than guessing — declared fields only, nothing
    resolved or fabricated. Order follows the opportunities' own ranking.
    """
    gates = gates or GateConfig()
    # Per-workspace skill-map override (inv_spec["discovery"]["skill_map"]); falls
    # back to the _KIND_TO_SKILL default per kind when absent.
    skill_overrides = ((inv_spec or {}).get("discovery") or {}).get("skill_map") \
        if isinstance(inv_spec, dict) else None
    disc_by_target = _discrepancies_by_target(discrepancies)
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
        if rkind == "hypothesize":
            disc = disc_by_target.get(str(target))
            if disc is not None:
                context["discrepancy"] = disc
        # Surface the target study's DECLARED model contract (ports/observables the model
        # actually declares) so the filler reasons from the real interface, not a guess.
        if studies:
            mc = _declared_model_contract(studies.get(str(target)))
            if mc:
                context["model_contract"] = mc
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
            skill=skill_for(rkind, skill_overrides),
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
        sk = f" ->{r.skill}" if getattr(r, "skill", "") else ""
        lines.append(f"  [{r.gate:6}] {r.kind:17}{sk:22} {r.target}{score}")
        if r.rationale:
            lines.append(f"            {r.rationale[:100]}")
    return "\n".join(lines)
