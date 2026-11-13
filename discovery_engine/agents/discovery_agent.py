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
        captures: list[ProposalCapture] = []
        proposals: list[dict] = []
        skipped: list[dict] = []
        for req in generation_requests(opportunities, gates=gates):
            sk = self.registry.get(req.kind)
            if sk is None:
                skipped.append({"request_id": req.id, "kind": req.kind,
                                "reason": f"no skill registered for kind {req.kind!r}"})
                continue
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
                        artifact = sk.coerce(raw or {})
                        outcome, validation = "proposed", {"ok": True, "errors": []}
                    except Exception as e:  # noqa: BLE001 - never crash on a bad filler
                        artifact = raw if isinstance(raw, dict) else {}
                        outcome, validation = "invalid", {"ok": False, "errors": [str(e)]}
            captures.append(ProposalCapture(
                request_id=req.id, kind=req.kind, target=req.target, skill=sk.name,
                filler=filler, context=dict(req.context or {}), artifact=artifact,
                validation=validation, outcome=outcome,
                at=datetime.now(timezone.utc).isoformat()))
            if outcome == "proposed":
                proposals.append(artifact)
        return ProposalPortfolio(captures=captures, proposals=proposals, skipped=skipped)
