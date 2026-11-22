"""The manifest -> provider -> run plumbing drives the real recovery loop.

Domain-free: uses the engine's own ``example_provider`` (which wraps the bundled recovery
scenario), proving the plumbing without any workspace dependency. A real workspace supplies
its own provider + real simulator runner; that end-to-end validation lives in the workspace.
"""
from __future__ import annotations

import pytest

from discovery_engine.benchmark.manifest import from_dict
from discovery_engine.benchmark.manifest_run import run_manifest_benchmark
from discovery_engine.benchmark.provider import (
    BenchmarkProviderBundle,
    load_provider_bundle,
    resolve_provider,
)

PROVIDER = "discovery_engine.benchmark.example_provider:build"


def _manifest(provider=PROVIDER):
    return from_dict({
        "id": "test_manifest_provider",
        "question": "does the manifest->provider->run path drive the recovery loop?",
        "fixture": {"provider": provider, "oracle_visibility": "evaluator_only"},
        "allowed_skills": ["hypothesize", "critique"],
        "budget": {"simulation_runs": 4, "model_builds": 1, "generation_calls": 4},
    })


def test_resolve_provider_forms():
    assert resolve_provider("discovery_engine.benchmark.example_provider:build") is not None
    assert resolve_provider("discovery_engine.benchmark.example_provider.build") is not None
    with pytest.raises(ValueError):
        resolve_provider("not_a_dotted_path")
    with pytest.raises(ValueError):
        resolve_provider("discovery_engine.benchmark.example_provider:nope")


def test_provider_schema_field_accepted():
    # `provider` must be allowed by the (strict, additionalProperties:false) fixture schema.
    m = _manifest()
    assert m.fixture["provider"] == PROVIDER


def test_load_provider_bundle_shape(tmp_path):
    bundle = load_provider_bundle(_manifest(), tmp_path)
    assert isinstance(bundle, BenchmarkProviderBundle)
    assert bundle.scenario.ground_truth is not None
    assert callable(bundle.build_hook)
    assert callable(bundle.runner)


def test_recovery_track_runs_and_recovers(tmp_path):
    # The example provider is the faithful ("full") config -> the built model recovers.
    matrix = run_manifest_benchmark(_manifest(), tmp_path, recovery=True)
    (name, metrics), = matrix.items()
    assert name == "test_manifest_provider"
    assert metrics.ran is True
    assert metrics.mechanism_recovered is True
    assert metrics.held_out_error is not None


def test_missing_provider_is_a_clear_error(tmp_path):
    m = from_dict({
        "id": "no_provider", "question": "q",
        "fixture": {"oracle_visibility": "evaluator_only"},
        "budget": {"simulation_runs": 1},
    })
    with pytest.raises(ValueError, match="no fixture.provider"):
        run_manifest_benchmark(m, tmp_path, recovery=True)
