"""A reference benchmark provider, wrapping the bundled recovery scenario.

A workspace implements its own ``build(ws_root)`` returning a
:class:`~discovery_engine.benchmark.provider.BenchmarkProviderBundle` with its real
scenario, ``build_hook`` (builds the real candidate composite), and ``runner`` (runs
it via the workspace's simulator). This domain-free example shows the required shape
and is what the manifest-provider test exercises end to end.

Copy this into a workspace, swap ``build_recovery_scenario`` for one that authors the
workspace's own investigation + study (raising the intended anomaly / capability gap),
and swap the deterministic ``build_hook`` / ``runner`` for ones that build and run the
workspace's real composite. The manifest's ``fixture.provider`` then points at the copy.
"""
from __future__ import annotations

from discovery_engine.benchmark import recovery_build, recovery_run
from discovery_engine.benchmark.provider import BenchmarkProviderBundle
from discovery_engine.benchmark.recovery_scenario import build_recovery_scenario


def build(ws_root) -> BenchmarkProviderBundle:
    """Author the bundled recovery scenario under ``ws_root`` and return the provider bundle."""
    scenario = build_recovery_scenario(ws_root)
    return BenchmarkProviderBundle(
        scenario=scenario,
        build_hook=recovery_build.build_hook(scenario.ground_truth.concealed_mechanism),
        runner=recovery_run.benchmark_runner())
