"""Ablation configs: the full engine vs. the engine with one skill dropped."""
from __future__ import annotations

from dataclasses import dataclass

from discovery_engine.agents.skill_registry import SkillRegistry, default_registry


@dataclass(frozen=True)
class BenchConfig:
    name: str
    drop_skills: frozenset

    def registry(self) -> SkillRegistry:
        full = default_registry()
        return SkillRegistry({k: full.get(k) for k in full.kinds() if k not in self.drop_skills})


CONFIGS = {
    "full": BenchConfig("full", frozenset()),
    "no_acquire_model": BenchConfig("no_acquire_model", frozenset({"acquire_model"})),
    "no_critique": BenchConfig("no_critique", frozenset({"critique"})),
}
