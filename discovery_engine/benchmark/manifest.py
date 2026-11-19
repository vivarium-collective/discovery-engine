"""Benchmark manifest — a declarative benchmark the harness loads and runs.

A benchmark is data, not code: one scientific question, a reference/candidate model fixture,
the evidence + held-out split, the skills/adapters the agent may use, a cost budget, the
required artifacts, metrics, and legitimate stop conditions (the plan's appendix template).

A :class:`Manifest` is a drop-in for the ablation ``BenchConfig`` — it exposes ``name`` and a
``registry()`` restricted to its ``allowed_skills`` — and it yields a
:class:`~discovery_engine.discover.budget.Budget` via :meth:`to_budget`. Pure; AI-free.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schemas"
MANIFEST_DIR = Path(__file__).resolve().parent / "manifests"


def _schema() -> dict:
    return json.loads((_SCHEMA_DIR / "benchmark-manifest.schema.json").read_text(encoding="utf-8"))


def validate_manifest(doc: dict) -> dict:
    """Raise ``ValueError`` unless ``doc`` satisfies benchmark-manifest.schema.json; else
    return it. Strict: an unknown top-level field is an error."""
    import jsonschema
    try:
        jsonschema.validate(json.loads(json.dumps(doc, default=str)), _schema())
    except jsonschema.ValidationError as e:  # pragma: no cover - message formatting
        raise ValueError(f"invalid benchmark manifest: {e.message}") from e
    return doc


@dataclass(frozen=True)
class Manifest:
    id: str
    question: str
    fixture: dict = field(default_factory=dict)
    allowed_skills: tuple = ()
    allowed_adapters: tuple = ()
    budget: dict = field(default_factory=dict)
    required_artifacts: tuple = ()
    metrics: tuple = ()
    stop_conditions: tuple = ()

    @property
    def name(self) -> str:
        return self.id

    def allows(self, skill: str) -> bool:
        return skill in self.allowed_skills

    def to_budget(self):
        """The :class:`Budget` this manifest authorizes."""
        from discovery_engine.discover.budget import Budget
        return Budget.from_manifest(self.budget)

    def registry(self):
        """A SkillRegistry restricted to the manifest's ``allowed_skills`` (intersected with
        the engine's actually-registered skill kinds, so the plan's richer skill names are
        forward-compatible — only the ones the engine can run take effect)."""
        from discovery_engine.agents.skill_registry import SkillRegistry, default_registry
        full = default_registry()
        allowed = set(self.allowed_skills)
        return SkillRegistry({k: full.get(k) for k in full.kinds() if k in allowed})


def from_dict(doc: dict) -> Manifest:
    """Validate ``doc`` against the schema and build a :class:`Manifest`."""
    validate_manifest(doc)
    return Manifest(
        id=doc["id"],
        question=doc["question"],
        fixture=dict(doc.get("fixture") or {}),
        allowed_skills=tuple(doc.get("allowed_skills") or ()),
        allowed_adapters=tuple(doc.get("allowed_adapters") or ()),
        budget=dict(doc.get("budget") or {}),
        required_artifacts=tuple(doc.get("required_artifacts") or ()),
        metrics=tuple(doc.get("metrics") or ()),
        stop_conditions=tuple(doc.get("stop_conditions") or ()))


def load_manifest(path) -> Manifest:
    """Load + validate a benchmark manifest from a YAML/JSON file."""
    import yaml
    doc = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    return from_dict(doc)


def load_named(name: str) -> Manifest:
    """Load a bundled manifest from ``benchmark/manifests/<name>.yaml``."""
    return load_manifest(MANIFEST_DIR / f"{name}.yaml")
