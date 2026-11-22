"""Run a benchmark from its manifest + a workspace provider (manifest -> provider -> run).

Additive: the bundled ablation benchmark (``scenario.build_scenario`` /
``recovery_scenario.build_recovery_scenario``) and the deterministic oracle / build-hook
defaults are unchanged. This path is taken only when a manifest declares a
``fixture.provider`` and the caller asks for it (``viva-discover benchmark --manifest``).

The engine drives its real loop against the provider's scenario + hooks; grading stays on
the one measured path (``compute_outcomes`` over the real ``runs.db``) whenever the provider
supplies a runner that actually executes its composite.
"""
from __future__ import annotations

from typing import Optional

from discovery_engine.benchmark.manifest import Manifest, load_manifest
from discovery_engine.benchmark.provider import load_provider_bundle


def run_manifest_benchmark(manifest: Manifest, ws_root, *, recovery: bool = False,
                           real_llm: bool = False, filler_cmd: Optional[str] = None) -> dict:
    """Drive the proposal or recovery track for ``manifest`` using its workspace provider.

    Returns ``{manifest.name: Metrics | RecoveryMetrics}`` --- the same row shape the
    ablation matrix produces, so existing renderers apply.
    """
    bundle = load_provider_bundle(manifest, ws_root)
    ground_truth = bundle.scenario.ground_truth

    if recovery:
        from discovery_engine.benchmark.recovery_metrics import score_recovery
        from discovery_engine.benchmark.recovery_runner import RecoveryConfig, run_recovery_benchmark
        build_hook = bundle.build_hook
        if real_llm:
            from discovery_engine.benchmark.recovery_build import real_build_hook
            build_hook = real_build_hook(filler_cmd)
        config = RecoveryConfig(name=manifest.name, build_hook_factory=None)
        result = run_recovery_benchmark(bundle.scenario, config,
                                        build_hook=build_hook, runner=bundle.runner)
        return {manifest.name: score_recovery(result, ground_truth)}

    # proposal track
    from discovery_engine.benchmark.metrics import score
    from discovery_engine.benchmark.oracle import oracle_filler
    from discovery_engine.benchmark.runner import run_benchmark
    fill = bundle.oracle
    if real_llm:
        from discovery_engine.phases.generation_apply import subprocess_filler
        fill = subprocess_filler(filler_cmd)
    elif fill is None:
        fill = oracle_filler(ground_truth)
    result = run_benchmark(bundle.scenario, manifest, oracle=fill)
    return {manifest.name: score(result, ground_truth)}


def run_manifest_path(manifest_path, ws_root, **kw) -> dict:
    """Convenience: load + validate a manifest file, then :func:`run_manifest_benchmark`."""
    return run_manifest_benchmark(load_manifest(manifest_path), ws_root, **kw)
