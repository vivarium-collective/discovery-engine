"""Run one benchmark config over a scenario: recover -> propose -> apply -> reload."""
from __future__ import annotations

from dataclasses import dataclass

from discovery_engine import discover
from discovery_engine.agents.discovery_agent import DiscoveryAgent
from discovery_engine.benchmark.oracle import oracle_filler
from discovery_engine.phases import evidence_integrity as ei
from discovery_engine.phases.generation import GenerationResult
from discovery_engine.phases.generation_apply import apply_result
from discovery_engine.phases.generation_emit import generation_requests
from discovery_engine.projection import engine as de
from discovery_engine.study.io import load_yaml


@dataclass(frozen=True)
class BenchmarkResult:
    config_name: str
    final_inv: dict
    recorded_proposals: list
    agent_calls: int
    skipped: list


def run_benchmark(scenario, config, *, oracle=None) -> BenchmarkResult:
    ws, slug = scenario.ws_root, scenario.inv_slug
    ip, inv, studies = discover.load_cycle_inputs(ws, slug)
    evs = ei.reconcile_evidence_state(
        inv, studies, discover._load_runs_by_study(ws, slug, studies))
    cyc = discover.run_cycle(inv, studies, evidence_state=evs)
    opps = de.opportunities(cyc, inv)
    reqs = generation_requests(opps)
    fill = oracle or oracle_filler(scenario.ground_truth)
    port = DiscoveryAgent(registry=config.registry()).propose(opps, fill=fill)
    for c in port.captures:
        if c.outcome != "proposed":
            continue
        req = next((r for r in reqs if r.id == c.request_id), None)
        if req is None:
            continue
        apply_result(req, GenerationResult(request_id=req.id, artifact=c.artifact,
                                           filler=c.filler),
                     ws_root=ws, inv_slug=slug)
    final = load_yaml(ip) or {}
    return BenchmarkResult(
        config_name=config.name, final_inv=final,
        recorded_proposals=list(final.get("model_acquisition_proposals") or []),
        agent_calls=len(port.captures), skipped=list(port.skipped))
