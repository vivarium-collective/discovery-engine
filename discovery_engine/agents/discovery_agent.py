"""DiscoveryAgent: route generation requests to skills, capture, validate.

Pure except for calling the injected ``fill``; writes nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from discovery_engine.agents.capture import ProposalCapture
from discovery_engine.agents.skill_registry import default_registry
from discovery_engine.phases.generation import GenerationResult
from discovery_engine.phases.generation_emit import generation_requests


@dataclass(frozen=True)
class ProposalPortfolio:
    captures: list
    proposals: list
    skipped: list


class DiscoveryAgent:
    def __init__(self, registry=None):
        self.registry = registry if registry is not None else default_registry()

    def propose(self, opportunities, *, fill, gates=None) -> ProposalPortfolio:
        # Native-aware routing (lazy import avoids an apply<->agent cycle): a kind is fillable if
        # THIS agent's registry knows it OR it is a native kind (interpret_finding / explain /
        # author_tests / resolve_decision), which have native apply handlers and are not in any
        # registry. Registry-only routing dropped the native kinds before the filler — on an
        # interpretation-driven investigation that is the entire frontier (applied:0 stall).
        # Test ONLY _NATIVE_HANDLERS here, never `_handler_for`: the latter falls back to the
        # DEFAULT registry, which would resurrect a skill this agent's registry deliberately
        # ablated (the benchmark matrix removes skills by passing a trimmed registry).
        from discovery_engine.phases.generation_apply import _NATIVE_HANDLERS
        captures: list[ProposalCapture] = []
        proposals: list[dict] = []
        skipped: list[dict] = []
        for req in generation_requests(opportunities, gates=gates):
            sk = self.registry.get(req.kind)
            native = sk is None and req.kind in _NATIVE_HANDLERS
            if sk is None and not native:
                # CLI-verb kinds (propose_study / visualize) and unknown kinds have neither a skill
                # nor a native handler — they belong to the apply_cli_verb seam, not this fill path.
                skipped.append({"request_id": req.id, "kind": req.kind,
                                "reason": f"no skill or native handler for kind {req.kind!r}"})
                continue
            skill_name = sk.name if sk is not None else f"native:{req.kind}"
            artifact: dict = {}
            filler: dict = {}
            raw = None
            try:
                result = fill(req)
            except Exception as e:  # noqa: BLE001 - a bad filler must never crash
                outcome, validation = "error", {"ok": False, "errors": [str(e)]}
            else:
                if result is None:
                    outcome = "empty"
                    validation = {"ok": False, "errors": ["filler returned no result"]}
                else:
                    try:
                        if not isinstance(result, GenerationResult):
                            raise TypeError(
                                f"filler returned {type(result).__name__}, "
                                "expected GenerationResult")
                        filler = result.filler or {}
                        raw = result.artifact
                        # Registry kinds coerce/validate through their skill; native kinds pass the
                        # artifact through — the native apply handler validates it at apply time.
                        artifact = sk.coerce(raw or {}) if sk is not None else dict(raw or {})
                        outcome, validation = "proposed", {"ok": True, "errors": []}
                    except Exception as e:  # noqa: BLE001 - never crash on a bad filler
                        artifact = raw if isinstance(raw, dict) else {}
                        outcome, validation = "invalid", {"ok": False, "errors": [str(e)]}
            captures.append(ProposalCapture(
                request_id=req.id, kind=req.kind, target=req.target, skill=skill_name,
                filler=filler, context=dict(req.context or {}), artifact=artifact,
                validation=validation, outcome=outcome,
                at=datetime.now(timezone.utc).isoformat()))
            if outcome == "proposed":
                proposals.append(artifact)
        return ProposalPortfolio(captures=captures, proposals=proposals, skipped=skipped)
