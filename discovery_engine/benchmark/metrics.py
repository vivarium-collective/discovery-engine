"""Pure scoring of a BenchmarkResult against ground truth."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Metrics:
    mechanism_proposed: bool
    overreach_critiqued: bool
    proposal_count: int
    agent_calls: int
    acquire_model_skipped: bool


def score(result, ground_truth) -> Metrics:
    props = [p for p in (getattr(result, "recorded_proposals", None) or []) if isinstance(p, dict)]
    crits = [c for c in (getattr(result, "recorded_critiques", None) or []) if isinstance(c, dict)]
    skipped = getattr(result, "skipped", None) or []
    kinds = {(s.get("kind") if isinstance(s, dict) else s) for s in skipped}
    return Metrics(
        mechanism_proposed=any(p.get("mechanism") == ground_truth.concealed_mechanism
                               for p in props),
        overreach_critiqued=len(crits) >= 1,
        proposal_count=len(props),
        agent_calls=int(getattr(result, "agent_calls", 0) or 0),
        acquire_model_skipped="acquire_model" in kinds)
