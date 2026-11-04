"""The typed generation contract (discovery-engine side).

The engine is a pure, derive-first projection. It already computes *which*
generative work should happen next — ``opportunities``, ``candidate_actions``,
the frontier, ``executive.decisions_needed`` — but today it only *prints that
intent as text* for a human to re-read and act on. This module gives the intent
a typed envelope, a :class:`GenerationRequest`, so a runner/filler outside the
engine (the existing ``/viva-*`` skills as human fillers, or an AI filler behind
``$VIVA_FILLER_CMD``) can consume it, and the workbench panel can render it.

The engine stays LLM-free: :func:`discovery_engine.generation_emit.generation_requests`
EMITS requests (a pure projection over already-derived cycle state); *filling* a
request happens across the seam, never here. This is Phase 1 of
``docs/specs/2026-10-30-ai-drivable-discovery-cycle.md`` — the contract + emission;
no gates persistence, no fillers, no writes.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

# The seven generative acts every GenerationRequest is one of. They project from
# the engine's seven frontier ops (revalidate, test, branch, reconcile, revise,
# steer, synthesize) via the mapping in generation_emit.generation_requests.
KINDS = (
    "propose_study",
    "acquire_model",
    "author_tests",
    "interpret_finding",
    "resolve_decision",
    "explain",
    "visualize",
)
GATES = ("auto", "review")

_SCHEMA_DIR = Path(__file__).parent / "schemas"

# Default gate per kind (spec §3): branch-points (new studies, model choices,
# decisions, interpretations) default to human review; cheap derivable outputs
# (tests, explanations, figures) default to auto. The operator overrides these
# per investigation via a persisted GateConfig (Phase 2).
_DEFAULT_BY_KIND = {
    "propose_study": "review",
    "acquire_model": "review",
    "author_tests": "auto",
    "interpret_finding": "review",
    "resolve_decision": "review",
    "explain": "auto",
    "visualize": "auto",
}


@dataclass(frozen=True)
class GateConfig:
    """Per-investigation autonomy control (spec §3). ``by_kind`` overrides
    ``default`` for a kind; :meth:`resolve` returns the gate for a kind. Phase 1
    uses it only to stamp each emitted request's ``gate``; persistence + panel
    editing land in Phase 2."""

    default: str | None = None
    by_kind: dict = field(default_factory=dict)

    def resolve(self, kind: str) -> str:
        gate = self.by_kind.get(kind) or self.default or _DEFAULT_BY_KIND.get(kind, "review")
        return gate if gate in GATES else "review"

    @classmethod
    def preset(cls, name: str) -> GateConfig:
        """One-click panel presets (spec §3)."""
        if name == "free_run":
            return cls(default="auto", by_kind={})
        if name == "all_gated":
            return cls(default="review", by_kind={})
        # "propose_review": gate only the branch-points, auto the rest.
        if name == "propose_review":
            return cls(default="auto", by_kind={
                "propose_study": "review", "acquire_model": "review",
                "resolve_decision": "review", "interpret_finding": "review",
            })
        raise ValueError(f"unknown GateConfig preset {name!r}")


@dataclass(frozen=True)
class GenerationRequest:
    """One typed generative act the cycle wants performed. The ``id`` is stable
    and content-addressed on ``(kind, target, provenance)`` so the same frontier
    state re-emits the same request (idempotent queueing). ``produces`` names the
    contract the filled artifact must satisfy (e.g. ``study.yaml/v4``,
    ``finding/v1``, ``decision/v1``, ``viz_spec/v1``). The artifact is written
    back into the EXISTING structured fields by the apply step — the request is
    the envelope, not a new storage format."""

    kind: str
    target: str
    produces: str
    rationale: str = ""
    context: dict = field(default_factory=dict)
    value: dict = field(default_factory=dict)
    provenance: dict = field(default_factory=dict)
    gate: str = "review"
    id: str = ""

    def __post_init__(self) -> None:
        if self.kind not in KINDS:
            raise ValueError(f"unknown GenerationRequest.kind {self.kind!r}; expected one of {KINDS}")
        if self.gate not in GATES:
            raise ValueError(f"unknown gate {self.gate!r}; expected one of {GATES}")
        if not self.target:
            raise ValueError("GenerationRequest.target is required")
        if not self.id:
            object.__setattr__(self, "id", self._derive_id())

    def _derive_id(self) -> str:
        key = json.dumps([self.kind, self.target, self.provenance], sort_keys=True, default=str)
        return "gr-" + hashlib.sha1(key.encode()).hexdigest()[:10]

    def to_dict(self) -> dict:
        return asdict(self)


def _schema(name: str) -> dict:
    return json.loads((_SCHEMA_DIR / name).read_text(encoding="utf-8"))


def validate_request(doc: dict) -> None:
    """Raise ``ValueError`` if ``doc`` doesn't satisfy generation-request.schema.json."""
    import jsonschema
    try:
        jsonschema.validate(json.loads(json.dumps(doc, default=str)),
                            _schema("generation-request.schema.json"))
    except jsonschema.ValidationError as e:  # pragma: no cover - message formatting
        raise ValueError(f"invalid generation_request: {e.message}") from e
