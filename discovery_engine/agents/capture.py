"""ProposalCapture: a replayable record of one agent invocation."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProposalCapture:
    request_id: str
    kind: str
    target: object
    skill: str
    filler: dict
    context: dict
    artifact: dict
    validation: dict  # {"ok": bool, "errors": list}
    outcome: str      # proposed | error | empty | invalid
    at: str
