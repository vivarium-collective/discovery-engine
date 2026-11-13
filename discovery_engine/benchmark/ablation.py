"""Ablation configs: the full engine vs. the engine with one skill dropped.

A config may also override the oracle (``oracle_factory``). ``full_decoy`` keeps
the full registry but swaps in the decoy oracle — identical skills, wrong
proposed mechanism — so it isolates the mechanism-string match from the mere
presence of a proposal (the negative control for ``mechanism_proposed``).
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from discovery_engine.agents.skill_registry import SkillRegistry, default_registry
from discovery_engine.benchmark.oracle import decoy_oracle_filler, oracle_filler


@dataclass(frozen=True)
class BenchConfig:
    name: str
    drop_skills: frozenset
    oracle_factory: Callable | None = None  # None -> the ground-truth oracle_filler

    def registry(self) -> SkillRegistry:
        full = default_registry()
        return SkillRegistry({k: full.get(k) for k in full.kinds() if k not in self.drop_skills})

    def oracle(self, ground_truth):
        return (self.oracle_factory or oracle_filler)(ground_truth)


CONFIGS = {
    "full": BenchConfig("full", frozenset()),
    "no_acquire_model": BenchConfig("no_acquire_model", frozenset({"acquire_model"})),
    "no_critique": BenchConfig("no_critique", frozenset({"critique"})),
    "full_decoy": BenchConfig("full_decoy", frozenset(), oracle_factory=decoy_oracle_filler),
}
