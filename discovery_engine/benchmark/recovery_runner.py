"""Recovery-track runner: drive the engine's capability_gap build+run loop and read back
the recovered observable from the BUILD-MATERIALIZED study (``built-<mechanism>``).

``observed`` is whatever the engine's grade computed from the built model's implemented
mechanism; it is never taken from the config name or ground truth.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import yaml

from discovery_engine import discover
from discovery_engine.benchmark import recovery_build, recovery_run
from discovery_engine.benchmark.recovery_scenario import RecoveryScenario
from discovery_engine.investigation.import_ import investigations_dir, studies_dir


@dataclass(frozen=True)
class RecoveryConfig:
    name: str
    build_hook_factory: Callable[[str], Callable] | None
    implements: str = "concealed"   # "concealed" | "decoy"

    def build_hook(self, ground_truth) -> Callable | None:
        if self.build_hook_factory is None:
            return None
        mech = (ground_truth.decoy_mechanism if self.implements == "decoy"
                else ground_truth.concealed_mechanism)
        return self.build_hook_factory(mech)


RECOVERY_CONFIGS = {
    "full": RecoveryConfig("full", recovery_build.build_hook, "concealed"),
    "full_decoy": RecoveryConfig("full_decoy", recovery_build.build_hook, "decoy"),
    "no_acquire": RecoveryConfig("no_acquire", None),
}


@dataclass
class RecoveryResult:
    config_name: str
    observed: float | None
    gap_status: str | None
    ran: bool
    final_study: dict


def _scalar(v: Any) -> float | None:
    if isinstance(v, dict):
        for k in ("value", "mean", "scalar"):
            if k in v:
                return _scalar(v[k])
        nums = [x for x in v.values() if isinstance(x, (int, float))]
        return float(nums[-1]) if nums else None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return float(v)
    return None


def _read_observed(study: dict, test_kind: str) -> float | None:
    runs = study.get("runs") or []
    for run in reversed(runs if isinstance(runs, list) else []):
        co = (run or {}).get("computed_outcomes") or {}
        for key in (test_kind, *co.keys()):
            ent = co.get(key)
            if isinstance(ent, dict) and "measured_value" in ent:
                s = _scalar(ent["measured_value"])
                if s is not None:
                    return s
    for f in study.get("findings") or []:
        s = _scalar((f.get("evidence") or {}).get("observed"))
        if s is not None:
            return s
    return None


def run_recovery_benchmark(scenario: RecoveryScenario, config: RecoveryConfig) -> RecoveryResult:
    ws, slug = scenario.ws_root, scenario.inv_slug
    inv_path, inv, st = discover.load_cycle_inputs(ws, slug)
    res = discover._cycle_with_evidence(ws, slug, inv, st)
    discover.persist_cycle(inv_path, inv, res, studies=st)
    gaps = (yaml.safe_load(inv_path.read_text(encoding="utf-8")).get("capability_gaps") or [])
    if gaps:
        discover.accept_capability_gap(
            ws, slug, gaps[0]["id"], rationale="recover the concealed mechanism")

    result = discover.advance_cycle(
        ws, slug, autonomous=True, build_hook=config.build_hook(scenario.ground_truth),
        runner=recovery_run.benchmark_runner(), max_runs=4)

    disk = yaml.safe_load(inv_path.read_text(encoding="utf-8")) or {}
    gap_list = disk.get("capability_gaps") or []
    gap_status = gap_list[0].get("status") if gap_list else None

    built = studies_dir(ws) / recovery_build.built_study_slug(scenario.ground_truth.concealed_mechanism) / "study.yaml"
    study = yaml.safe_load(built.read_text(encoding="utf-8")) if built.exists() else {}
    ran = any(r.get("status") == "executed" for r in result.get("runs", [])) and bool(study)
    observed = _read_observed(study, scenario.test_kind) if ran else None
    return RecoveryResult(config.name, observed, gap_status, ran, study)
