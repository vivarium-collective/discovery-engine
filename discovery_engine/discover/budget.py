"""Budget — a cost meter for a bounded autonomous run.

The plan forbids free-form recursive cycles without explicit budgets or stops. A Budget caps
the three costly actions — simulation runs, model builds, and generation (LLM) calls — and a
drain/orchestrator charges it before each one; when a kind is exhausted the run stops with a
``budget_exhausted`` decision rather than continuing. A ``None`` cap means unbounded for that
kind. Pure bookkeeping; AI-free.
"""
from __future__ import annotations

from dataclasses import dataclass, field

SIMULATION_RUNS = "simulation_runs"
MODEL_BUILDS = "model_builds"
GENERATION_CALLS = "generation_calls"
KINDS = (SIMULATION_RUNS, MODEL_BUILDS, GENERATION_CALLS)


@dataclass
class Budget:
    """Caps + running spend for one bounded run. Mutated in place as actions are charged."""
    simulation_runs: int | None = None
    model_builds: int | None = None
    generation_calls: int | None = None
    spent: dict = field(default_factory=dict)

    @classmethod
    def from_manifest(cls, d: dict | None) -> "Budget":
        """Build a Budget from a manifest ``budget`` block (missing keys = unbounded)."""
        d = d or {}
        return cls(simulation_runs=d.get(SIMULATION_RUNS),
                   model_builds=d.get(MODEL_BUILDS),
                   generation_calls=d.get(GENERATION_CALLS))

    def _cap(self, kind: str) -> int | None:
        if kind not in KINDS:
            raise ValueError(f"unknown budget kind {kind!r}; expected one of {KINDS}")
        return getattr(self, kind)

    def remaining(self, kind: str) -> int | None:
        cap = self._cap(kind)
        return None if cap is None else max(0, cap - self.spent.get(kind, 0))

    def can(self, kind: str) -> bool:
        """True if at least one more ``kind`` action is affordable (or the cap is unbounded)."""
        r = self.remaining(kind)
        return r is None or r > 0

    def charge(self, kind: str, n: int = 1) -> bool:
        """Spend ``n`` of ``kind`` if affordable; return ``True`` on success, ``False``
        (spending nothing) when it would exceed the cap."""
        cap = self._cap(kind)
        if cap is not None and self.spent.get(kind, 0) + n > cap:
            return False
        self.spent[kind] = self.spent.get(kind, 0) + n
        return True

    def as_dict(self) -> dict:
        return {"caps": {k: self._cap(k) for k in KINDS}, "spent": dict(self.spent)}
