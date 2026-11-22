"""Workspace benchmark providers: run a workspace-supplied benchmark through the harness.

A benchmark manifest may name a *provider* under ``fixture.provider`` --- a dotted
``module:callable`` (or ``module.callable``) that a workspace implements. The harness
imports it read-only and calls ``provider(ws_root)``, which authors the scenario on
disk under ``ws_root`` and returns a :class:`BenchmarkProviderBundle`: the scenario,
and the workspace's own ``build_hook`` / ``runner`` / optional ``oracle``.

This is the only seam by which a workspace plugs its real models and simulator into
the engine's proposal / recovery tracks. The engine imports nothing from the workspace
except through this runtime, manifest-declared hook, so the engine package stays
domain-free: all biological specifics live in the workspace provider.
"""
from __future__ import annotations

import importlib
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class BenchmarkProviderBundle:
    """What a workspace provider returns from ``build(ws_root)``.

    Attributes:
        scenario: a Scenario / RecoveryScenario, already authored on disk under
            ``ws_root``. Must expose ``ws_root``, ``inv_slug``, and ``ground_truth``
            (the recovery track also reads ``test_kind``).
        build_hook: ``build_hook(gap, ws_root) -> dict`` that builds the real candidate
            composite (recovery track). Optional.
        runner: ``runner(command, ws_root, *, worktree=None, **kw) -> dict`` that runs
            the real composite (recovery track). Optional; the harness falls back to the
            deterministic benchmark runner when absent.
        oracle: a proposal-track filler ``oracle(req) -> GenerationResult | None``.
            Optional; the harness falls back to the deterministic ``oracle_filler``.
    """

    scenario: Any
    build_hook: Optional[Callable] = None
    runner: Optional[Callable] = None
    oracle: Optional[Callable] = None


def resolve_provider(spec: str) -> Callable[[Any], "BenchmarkProviderBundle"]:
    """Import a provider callable from ``"module:callable"`` or ``"module.callable"``.

    Read-only dotted import (the ``phases.model_inspect._load`` pattern). Raises
    ``ValueError`` with a clear message when the spec does not resolve to a callable.
    """
    if not isinstance(spec, str) or not spec.strip():
        raise ValueError("fixture.provider must be a non-empty 'module:callable' string")
    if ":" in spec:
        mod_name, attr = spec.split(":", 1)
    elif "." in spec:
        mod_name, attr = spec.rsplit(".", 1)
    else:
        raise ValueError(f"provider spec {spec!r} is not 'module:callable' or 'module.callable'")
    try:
        mod = importlib.import_module(mod_name)
    except Exception as e:  # noqa: BLE001 — an unimportable provider is a contract error, not a crash
        raise ValueError(f"cannot import provider module {mod_name!r}: {type(e).__name__}: {e}") from e
    fn = getattr(mod, attr, None)
    if not callable(fn):
        raise ValueError(f"provider {spec!r}: {attr!r} is not a callable in {mod_name!r}")
    return fn


def load_provider_bundle(manifest, ws_root) -> BenchmarkProviderBundle:
    """Resolve and call the manifest's ``fixture.provider``; validate the bundle shape.

    Accepts a :class:`BenchmarkProviderBundle`, a plain ``dict`` of its fields, or any
    object exposing ``.scenario`` (and optionally ``.build_hook`` / ``.runner`` /
    ``.oracle``) for forward compatibility.
    """
    spec = (manifest.fixture or {}).get("provider")
    if not spec:
        raise ValueError("manifest has no fixture.provider; cannot run a workspace benchmark")
    build = resolve_provider(spec)
    bundle = build(ws_root)
    if isinstance(bundle, BenchmarkProviderBundle):
        pass
    elif isinstance(bundle, dict):
        bundle = BenchmarkProviderBundle(**bundle)
    elif hasattr(bundle, "scenario"):
        bundle = BenchmarkProviderBundle(
            scenario=bundle.scenario,
            build_hook=getattr(bundle, "build_hook", None),
            runner=getattr(bundle, "runner", None),
            oracle=getattr(bundle, "oracle", None))
    else:
        raise ValueError(
            f"provider {spec!r} must return a BenchmarkProviderBundle, a dict of its fields, "
            "or an object with a .scenario attribute")
    if getattr(bundle.scenario, "ground_truth", None) is None:
        raise ValueError(f"provider {spec!r}: scenario has no ground_truth")
    return bundle
