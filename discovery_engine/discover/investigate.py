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


def investigate(ws_root, inv_slug, *, fill, budget=None, policy=None, gates=None,
                max_iterations: int = 8, actor: str = "orchestrator") -> dict:
    """Run a bounded investigation loop. ``fill(request) -> GenerationResult | None`` is the
    filler seam (a ``/viva-*`` skill, ``$VIVA_FILLER_CMD``, or a test oracle); ``budget`` a
    :class:`~discovery_engine.discover.budget.Budget`; ``gates`` an optional
    :class:`GateConfig` (default: the investigation's own ``discovery.gates``). Returns
    ``{iterations, stop_reason, applied, decisions, inv_path}``."""
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
    for i in range(1, max_iterations + 1):
        inv_path, inv_spec, studies = discover.load_cycle_inputs(ws_root, inv_slug)
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
        outs = run_requests(reqs, fill=fill, ws_root=ws_root, inv_slug=inv_slug, budget=budget)
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
            "decisions": decisions, "inv_path": str(inv_path) if inv_path else None}
