"""`discover investigate` — one bounded orchestration over the existing verbs.

The plan asks for a single headless workflow that drives the cycle without duplicating
execution logic: project the cycle, emit the frontier's generation requests, fill + apply the
auto-gated ones under a budget, log every decision, and repeat until the investigation closes,
the budget is exhausted, or no progress is made. It composes `load_cycle_inputs` /
`_cycle_with_evidence` / `opportunities` / `generation_requests` / `run_requests` — it adds no
new phase logic, and every write goes through the existing apply ops + the decision log.

A legitimate halt (closed / budget_exhausted / no_informative_experiment / insufficient_evidence)
is a first-class outcome, not a failure. Pure composition over its injected `fill`; the only
LLM is behind that seam.
"""
from __future__ import annotations


def _gates_for(inv_spec, override=None):
    from discovery_engine.phases.generation import GateConfig
    if override is not None:
        return override
    gc = (inv_spec.get("discovery") or {}).get("gates") if isinstance(inv_spec, dict) else None
    if isinstance(gc, dict):
        return GateConfig(default=gc.get("default"), by_kind=gc.get("by_kind") or {})
    return GateConfig()


def _steer_fill(fill, goal: str | None):
    """Wrap a filler so every request carries the run's free-text ``goal`` in its
    ``context["goal"]`` before it reaches the filler (the LLM behind ``$VIVA_FILLER_CMD``,
    or a skill). The steering is the one durable lever on WHAT the engine produces: the
    frontier/opportunities stay derived from the investigation's state, but the goal shapes
    how each generative act (hypothesis, design, analysis, synthesis, viz) is filled. Uses
    ``dataclasses.replace`` so the request's content-addressed ``id`` — derived from
    ``(kind, target, provenance)``, not ``context`` — is unchanged (idempotent)."""
    if not goal or fill is None:
        return fill
    import dataclasses

    def steered(request):
        return fill(dataclasses.replace(request, context={**(request.context or {}), "goal": goal}))

    return steered


# Goal cue words → the request KIND they gesture at. A goal names what it WANTS
# ("a visual exposition", "test this", "decide X") in intent words that rarely appear
# verbatim in a request's target string — so string overlap alone misses them (a goal of
# "exposition in great detail" shares no substring with a `visualize` / `fig:…` request).
# This maps the intent to the kind. Substrings (stems), matched anywhere in the goal.
_KIND_CUES: dict[str, tuple[str, ...]] = {
    "visualize": ("visual", "figure", "fig ", "chart", "plot", "diagram", "graphic",
                  "illustrat", "exposition", "showcase", "showcasing", "display", "render",
                  "picture", "panel", "detail", "image"),
    "propose_study": ("study", "studies", "hypothes", "investigat", "explore", "scenario",
                      "question"),
    "author_tests": ("test", "validat", "verif", "assert", "criteri", "gate", "falsif"),
    "interpret_finding": ("interpret", "finding", "meaning", "insight", "takeaway"),
    "explain": ("explain", "narrat", "describe", "exposition", "walkthrough"),
    "resolve_decision": ("decid", "decision", "resolv", "adjudicat", "choose", "choice"),
    "acquire_model": ("model", "composite", "simulat", "mechanism"),
    "critique": ("critiqu", "scrutin", "adversar", "challenge", "stress"),
}
_KIND_BOOST = 2  # weight of a kind-intent match, blended with per-token target overlap


def _goal_order(reqs, goal: str | None):
    """Stable-sort auto-gated requests so those most relevant to the free-text ``goal`` fill
    first — the budget-aware half of steering (which requests run before the budget is spent),
    complementing :func:`_steer_fill` (how each is filled). Two transparent, general signals,
    blended: (1) per-token overlap of goal words (≥4 chars) with a request's
    kind/target/rationale/produces, and (2) a KIND-intent boost when the goal names what it
    wants (see :data:`_KIND_CUES`) and a request is of that kind — because a goal like
    "a visual exposition in great detail" shares no substring with a ``visualize`` / ``fig:…``
    request, yet clearly asks for visuals. Neither is hardcoded to one kind; ties keep emission
    order (``sorted`` is stable). The frontier is unchanged — this only reorders what the cycle
    already emitted, so nothing is invented and no honest request is dropped."""
    if not goal:
        return reqs
    import re
    g = goal.lower()
    tokens = {t for t in re.findall(r"[a-z0-9]+", g) if len(t) >= 4}
    intent_kinds = {k for k, cues in _KIND_CUES.items() if any(c in g for c in cues)}
    if not tokens and not intent_kinds:
        return reqs

    def score(r):
        hay = " ".join(str(x) for x in (r.kind, r.target, r.rationale, r.produces)).lower()
        overlap = sum(1 for t in tokens if t in hay)
        kind_boost = _KIND_BOOST if r.kind in intent_kinds else 0
        return overlap + kind_boost

    return sorted(reqs, key=lambda r: -score(r))


def investigate(ws_root, inv_slug, *, fill, budget=None, policy=None, gates=None,
                max_iterations: int = 8, actor: str = "orchestrator",
                goal: str | None = None) -> dict:
    """Run a bounded investigation loop. ``fill(request) -> GenerationResult | None`` is the
    filler seam (a ``/viva-*`` skill, ``$VIVA_FILLER_CMD``, or a test oracle); ``budget`` a
    :class:`~discovery_engine.discover.budget.Budget`; ``gates`` an optional
    :class:`GateConfig` (default: the investigation's own ``discovery.gates``).

    ``goal`` is a free-text steering prompt threaded into every request's ``context["goal"]``
    (see :func:`_steer_fill`); when omitted it falls back to the investigation's durable
    ``discovery.goal``, so a goal written onto the investigation steers every run. Returns
    ``{iterations, stop_reason, applied, decisions, inv_path, goal}``."""
    from discovery_engine import discover
    from discovery_engine.discover._policy import (
        BUDGET_EXHAUSTED, INSUFFICIENT_EVIDENCE, NO_INFORMATIVE_EXPERIMENT,
        AutonomyPolicy, decision_record)
    from discovery_engine.discover.actions import record_decision
    from discovery_engine.discover.cycle import _cycle_with_evidence
    from discovery_engine.phases.generation_apply import run_requests
    from discovery_engine.phases.generation_emit import generation_requests
    from discovery_engine.projection import engine as de

    pol = AutonomyPolicy.resolve(policy)
    decisions = 0
    applied_total = 0
    stop_reason = None
    inv_path = None
    i = 0
    eff_goal = goal
    for i in range(1, max_iterations + 1):
        inv_path, inv_spec, studies = discover.load_cycle_inputs(ws_root, inv_slug)
        # Explicit goal wins; else the investigation's durable discovery.goal.
        eff_goal = goal or (inv_spec.get("discovery") or {}).get("goal")
        cyc = _cycle_with_evidence(ws_root, inv_slug, inv_spec, studies)
        # Act on the frontier while there is auto-gated generative work — anomalies and other
        # non-blocking items count, so `closed` (no blocking kinds) does not pre-empt the loop.
        reqs = [r for r in generation_requests(de.opportunities(cyc, inv_spec),
                                               gates=_gates_for(inv_spec, gates), inv_spec=inv_spec,
                                               discrepancies=cyc.get("discrepancies"))
                if r.gate == "auto"]
        if not reqs:
            stop_reason = "closed" if de.closure(cyc, inv_spec).get("closed") else NO_INFORMATIVE_EXPERIMENT
            break
        reqs = _goal_order(reqs, eff_goal)       # on-goal requests fill first under the budget
        outs = run_requests(reqs, fill=_steer_fill(fill, eff_goal), ws_root=ws_root,
                            inv_slug=inv_slug, budget=budget)
        applied_here = 0
        budget_hit = False
        for o in outs:
            record_decision(inv_path, decision_record(
                pol, actor=actor, consequence=f"generation:{o['kind']}",
                target=o.get("request_id"), rationale=f"{o['kind']} -> {o['outcome']}",
                result=o.get("outcome")))
            decisions += 1
            if o.get("outcome") == "applied":
                applied_here += 1
            if o.get("outcome") == "stop" and o.get("reason") == BUDGET_EXHAUSTED:
                budget_hit = True
        applied_total += applied_here
        if budget_hit:
            stop_reason = BUDGET_EXHAUSTED
            break
        if applied_here == 0:
            stop_reason = INSUFFICIENT_EVIDENCE
            break

    if stop_reason is None:
        stop_reason = "max_iterations"
    return {"iterations": i, "stop_reason": stop_reason, "applied": applied_total,
            "decisions": decisions, "inv_path": str(inv_path) if inv_path else None,
            "goal": eff_goal}
