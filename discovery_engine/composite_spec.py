"""Composite spec format: a declarative, JSON-able description of a composite.

A composite spec lives in a *.composite.yaml or *.composite.json file inside
any installed bigraph-schema-dependent package. It declares the composite's
state document plus optional parameters that callers can override.

This is the data-first counterpart to process-bigraph's class-based discovery:
processes are CODE (subclasses of Edge), but composites are DATA (declarative
state documents). Discovering them by file glob avoids running arbitrary
Python at discovery time and makes them easy to inspect, diff, render, and
version-control.

Task 8: substitution, type normalisation, and composite construction delegate
to the unified engine in process_bigraph.composite_spec; this module keeps only
file I/O (load_spec) and pbg-superpowers-specific wrapping behaviour
(validate_spec, build_composite_from_spec with install_default_emitters).
"""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Unified engine — single source for substitution + type vocabulary
# ---------------------------------------------------------------------------

# Re-export so callers `from discovery_engine.composite_spec import substitute_parameters`
# still work while the implementation is single-sourced from process-bigraph.
from process_bigraph.composite_spec import (  # noqa: F401  (re-export)
    substitute_parameters,
    normalize_type,
    CANONICAL_TYPES,
)


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def load_spec(path: Path) -> dict:
    """Parse a composite spec file (YAML or JSON). Returns the spec dict."""
    text = path.read_text(encoding="utf-8")
    suffix = path.suffix.lower()
    if suffix == ".json":
        return json.loads(text)
    # default to YAML for .yaml / .yml / anything else
    return yaml.safe_load(text)


def validate_spec(spec: dict) -> None:
    """Raise ValueError if the spec doesn't match the convention.

    Required: name (str), state (dict)
    Optional: description, requires, parameters
    """
    if not isinstance(spec, dict):
        raise ValueError("spec must be a dict")
    name = spec.get("name")
    if not isinstance(name, str) or not name:
        raise ValueError("spec.name is required (non-empty string)")
    state = spec.get("state")
    if not isinstance(state, dict):
        raise ValueError("spec.state is required (dict)")

    params = spec.get("parameters") or {}
    if not isinstance(params, dict):
        raise ValueError("spec.parameters must be a dict if present")
    for pname, pdef in params.items():
        if not isinstance(pdef, dict):
            raise ValueError(f"parameter '{pname}' must be a dict")
        # Widened: accept the full canonical+alias vocabulary via normalize_type
        # (previously only allowed float|int|string|str|bool).
        if "type" in pdef and normalize_type(pdef["type"]) not in CANONICAL_TYPES:
            raise ValueError(
                f"parameter '{pname}': type must be one of {sorted(CANONICAL_TYPES)} "
                f"(or a recognised alias); got {pdef['type']!r}"
            )

    requires = spec.get("requires") or {}
    if requires and not isinstance(requires, dict):
        raise ValueError("spec.requires must be a dict if present")

    # Optional default-emitter declaration (the static-spec analogue of
    # @composite_generator(emitters=[...])). Each entry is a lightweight
    # {address, config?, paths?} selection; see composite_generator.emitter_defaults.
    emitters = spec.get("emitters")
    if emitters is not None:
        if not isinstance(emitters, list):
            raise ValueError("spec.emitters must be a list if present")
        for i, em in enumerate(emitters):
            if not isinstance(em, dict) or not em.get("address"):
                raise ValueError(f"spec.emitters[{i}] must be a dict with an 'address'")


# ---------------------------------------------------------------------------
# Composite construction
# ---------------------------------------------------------------------------

def build_composite_from_spec(spec: dict, overrides: dict[str, Any] | None = None, core=None):
    """Construct a process_bigraph.Composite from a parsed spec.

    Delegates document production (substitution + schema resolution) to the
    unified CompositeSpec engine in process_bigraph.composite_spec, then
    installs the declared default emitters via install_default_emitters so
    composites built outside the dashboard's observable-injection flow still
    ship with their sink.

    overrides: optional dict of parameter overrides (keys match spec.parameters).
    core:      optional Core; if None, calls allocate_core().
    """
    validate_spec(spec)
    from process_bigraph import Composite, allocate_core
    from process_bigraph.composite_spec import CompositeSpec

    if core is None:
        core = allocate_core()

    # Pre-flight: verify requires.processes are in the registry.
    requires = spec.get("requires") or {}
    proc_required = requires.get("processes") or []
    link_registry = getattr(core, "link_registry", {}) or {}
    missing = [p for p in proc_required if p not in link_registry]
    if missing:
        raise RuntimeError(
            f"composite spec '{spec.get('name')}' requires processes not in registry: {missing}. "
            f"Install the package(s) that provide them."
        )

    # Build the document via the unified engine.
    cspec = CompositeSpec(
        id=spec.get("id") or f"spec.{spec.get('name')}",
        name=spec.get("name"),
        state=spec.get("state") or {},
        schema=dict(spec.get("schema") or {}),
        parameters=dict(spec.get("parameters") or {}),
        requires=requires,
        emitters=list(spec.get("emitters") or []),
    )
    doc = cspec.to_document(overrides)
    state = doc["state"]

    # Install the composite's declared default emitter(s) (spec.emitters), so a
    # composite built outside the dashboard's observable-injection flow still
    # ships with its sink. No-op when nothing is declared. The declared address
    # degrades to RAMEmitter if it isn't registered on `core`.
    from process_bigraph.composite_generator import install_default_emitters
    state = install_default_emitters(state, spec, core=core)

    return Composite({"schema": doc.get("schema") or {}, "state": state}, core=core)
