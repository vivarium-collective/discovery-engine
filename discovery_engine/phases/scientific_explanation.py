"""R4 — ScientificExplanation + evidence-fingerprinted resolutions (§5, §8).

§5 asks for an evidence-linked explanation that does not let generated prose masquerade as a
measured result: a claim tied to the mechanisms it invokes, the model versions behind it, the
observations that support or refute it, its assumptions and scope, and — honestly — the
alternatives it has NOT yet ruled out.

§8 asks that resolutions be versioned against the underlying evidence/model fingerprint and reopen
when the premise changes — "the current (kind, target) discharge key is not enough." So a resolution
carries the fingerprint of the evidence+model state it rested on; when that state changes, the
resolution is stale and should reopen.

Pure; domain-neutral.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json


@dataclasses.dataclass
class ScientificExplanation:
    """An evidence-linked explanation of one claim (§5)."""
    claim: str
    mechanisms: list = dataclasses.field(default_factory=list)
    competing_hypotheses: list = dataclasses.field(default_factory=list)
    model_versions: list = dataclasses.field(default_factory=list)
    supporting_observations: list = dataclasses.field(default_factory=list)
    refuting_observations: list = dataclasses.field(default_factory=list)
    assumptions: list = dataclasses.field(default_factory=list)
    scope: str | None = None
    unresolved_alternatives: list = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def build_scientific_explanation(claim, **fields) -> ScientificExplanation:
    """Construct a ScientificExplanation for ``claim`` (string or ``{statement}``). Unknown fields
    are ignored; list fields default to empty."""
    statement = claim.get("statement") if isinstance(claim, dict) else str(claim)
    allowed = {f.name for f in dataclasses.fields(ScientificExplanation)} - {"claim"}
    return ScientificExplanation(claim=statement, **{k: v for k, v in fields.items() if k in allowed})


def validate_explanation(expl) -> list[str]:
    """Issues (empty = ok). An explanation must link at least one supporting observation (prose is
    not evidence); a claim with refuting observations or unresolved alternatives is not fully
    resolved; an explanation that invokes mechanisms should name the model versions behind them."""
    e = expl.to_dict() if isinstance(expl, ScientificExplanation) else (expl or {})
    issues: list[str] = []
    if not e.get("supporting_observations"):
        issues.append("no supporting observation — a claim needs measured evidence, not only prose")
    if e.get("refuting_observations"):
        issues.append(f"{len(e['refuting_observations'])} refuting observation(s) — the claim is "
                      "not fully supported")
    if e.get("unresolved_alternatives"):
        issues.append(f"{len(e['unresolved_alternatives'])} unresolved alternative(s) — competing "
                      "explanations are not ruled out")
    if e.get("mechanisms") and not e.get("model_versions"):
        issues.append("mechanisms invoked but no model version named to back them")
    return issues


def evidence_fingerprint(*, observations=None, model_versions=None) -> str:
    """A content hash of the evidence + model state a resolution rests on. Changes when an
    observation's identity/value/verdict changes or a model version changes — so a resolution built
    on the old fingerprint is detectably stale. Order-independent (sets of observations/versions)."""
    def _obs_key(o):
        if isinstance(o, dict):
            return (o.get("id") or o.get("observable"), o.get("value"),
                    o.get("verdict") or o.get("result"), o.get("supported"))
        return (str(o),)
    obs = sorted((_obs_key(o) for o in (observations or [])), key=lambda t: json.dumps(t, default=str))
    mvs = sorted(str(m.get("id") if isinstance(m, dict) else m) for m in (model_versions or []))
    payload = {"observations": obs, "model_versions": mvs}
    return "ev:" + hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def fingerprinted_resolution(target, *, kind, observations=None, model_versions=None,
                             rationale: str = "") -> dict:
    """A resolution bound to the evidence/model fingerprint of its premise (§8). The ``(kind,
    target)`` discharge key alone is not enough — the fingerprint is what reopens it."""
    return {"target": target, "kind": kind, "rationale": rationale,
            "premise_fingerprint": evidence_fingerprint(observations=observations,
                                                        model_versions=model_versions)}


def resolution_is_stale(resolution, *, observations=None, model_versions=None) -> bool:
    """True when the current evidence/model fingerprint differs from the resolution's premise — the
    premise changed, so the resolution should reopen (§8). A resolution with no recorded fingerprint
    is treated as stale (it cannot be shown current)."""
    premise = (resolution or {}).get("premise_fingerprint")
    if not premise:
        return True
    return premise != evidence_fingerprint(observations=observations, model_versions=model_versions)
