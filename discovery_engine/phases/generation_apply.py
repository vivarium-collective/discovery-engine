"""Apply a filled GenerationResult through the engine's canonical write op.

The engine EMITS GenerationRequests (``generation_emit``); once a filler (a
``/viva-*`` skill, or an AI filler behind ``$VIVA_FILLER_CMD``) has produced the
artifact, it is APPLIED here by routing to the EXISTING invariant-preserving
write op for the request's kind — ``record_resolution`` for a decision, the
finding writers for an interpretation, and so on. The generation (the artifact)
happens in the filler across the seam; this module only routes and writes, so the
engine stays pure and LLM-free. Phase 3 of the AI-drivable discovery cycle spec
(human fillers + apply routing).

Wired kinds: ``resolve_decision``, ``interpret_finding``. Others return
``queued_for_review`` (no apply op yet) rather than raising, so a runner can drain
the wired work and leave the rest for review.
"""
from __future__ import annotations

from io import StringIO
from pathlib import Path

from discovery_engine.phases.generation import GenerationRequest, GenerationResult


def _inv_path(ws_root, inv_slug: str) -> Path:
    from discovery_engine.investigation.import_ import investigations_dir
    return investigations_dir(Path(ws_root)) / inv_slug / "investigation.yaml"


def _study_dir(ws_root, inv_slug: str, study: str) -> Path | None:
    from discovery_engine.investigation.import_ import investigations_dir, studies_dir
    flat = studies_dir(Path(ws_root)) / study
    if (flat / "study.yaml").exists():
        return flat
    nested = investigations_dir(Path(ws_root)) / inv_slug / "studies" / study
    return nested if (nested / "study.yaml").exists() else None


def _apply_finding_interpretation(sdir: Path, finding_id: str, art: dict) -> list[str]:
    """Write the authored interpretation (statement / explanation / status) onto
    the named finding, comment-preserving + atomic. Only those prose slots — never
    the numeric evidence, which is ``finding_observations``' job."""
    from ruamel.yaml import YAML

    from discovery_engine.study import io as study_io
    sp = sdir / "study.yaml"
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    doc = y.load(sp.read_text(encoding="utf-8"))
    target = next((f for f in (doc.get("findings") or [])
                   if isinstance(f, dict) and (f.get("id") or f.get("name")) == finding_id), None)
    if target is None:
        raise ValueError(f"no finding {finding_id!r} in {sp}")
    applied: list[str] = []
    for key in ("statement", "explanation", "status"):
        val = art.get(key)
        if isinstance(val, str) and val.strip():
            target[key] = val
            applied.append(f"findings[{finding_id}].{key}")
    if not applied:
        raise ValueError("interpret_finding artifact set no statement/explanation/status")
    buf = StringIO()
    y.dump(doc, buf)
    study_io.atomic_write(sp, buf.getvalue())
    return applied


def apply_result(request: GenerationRequest, result: GenerationResult, *,
                 ws_root, inv_slug: str) -> dict:
    """Route ``(request, result)`` to the canonical write op for ``request.kind``.

    Returns ``{status, applied_to, ...}`` — ``status`` is ``applied`` when a wired
    op ran, else ``queued_for_review``. Raises ``ValueError`` on a malformed
    artifact for a wired kind. Pure routing over existing writers; no generation."""
    kind = request.kind
    art = result.artifact or {}
    filler_id = (result.filler or {}).get("id") or (result.filler or {}).get("kind") or "filler"

    if kind == "resolve_decision":
        from discovery_engine.discover import record_resolution
        rationale = (art.get("rationale") or result.rationale or "").strip()
        if not rationale:
            raise ValueError("resolve_decision artifact needs a rationale")
        rec = record_resolution(_inv_path(ws_root, inv_slug), "open_decision",
                                request.target, rationale, by=filler_id)
        return {"status": "applied", "applied_to": [f"{inv_slug}/resolutions"], "detail": rec}

    if kind == "interpret_finding":
        study = art.get("study")
        finding_id = art.get("finding") or art.get("finding_id")
        if not study or not finding_id:
            raise ValueError("interpret_finding artifact needs {study, finding}")
        sdir = _study_dir(ws_root, inv_slug, str(study))
        if sdir is None:
            raise ValueError(f"no study.yaml for {study!r}")
        applied = _apply_finding_interpretation(sdir, str(finding_id), art)
        return {"status": "applied", "applied_to": applied}

    return {"status": "queued_for_review", "applied_to": [],
            "reason": f"apply not wired for kind {kind!r} yet"}


def run_requests(requests, *, fill, ws_root, inv_slug: str) -> list[dict]:
    """Dispatch typed GenerationRequests to a filler and apply the auto-gated ones.

    ``fill`` is the injected filler seam — ``fill(request) -> GenerationResult | None``
    (a ``/viva-*`` skill wrapper, the ``$VIVA_FILLER_CMD`` subprocess, or a test
    stub). It is the ONLY place generation (an LLM) runs; the engine stays pure.

    Gate discipline (spec §3 / review-focus): a ``review``-gated request is QUEUED,
    never auto-filled; an ``auto`` request is filled and applied via
    :func:`apply_result`. A filler that raises, returns nothing, or yields an
    artifact the apply op rejects becomes an ``error``/``queued_for_review``
    outcome — never a silent apply — and never blocks the rest (the runner drains
    the auto work and leaves the rest). Returns one outcome dict per request:
    ``{request_id, kind, gate, outcome: applied|queued_for_review|error, ...}``."""
    outcomes: list[dict] = []
    for req in requests:
        base = {"request_id": req.id, "kind": req.kind, "gate": req.gate}
        if req.gate != "auto":
            outcomes.append({**base, "outcome": "queued_for_review"})
            continue
        try:
            result = fill(req)
        except Exception as e:  # noqa: BLE001 — a filler must never crash the drain
            outcomes.append({**base, "outcome": "error", "reason": f"filler raised: {e}"})
            continue
        if result is None:
            outcomes.append({**base, "outcome": "queued_for_review",
                             "reason": "filler produced nothing"})
            continue
        try:
            applied = apply_result(req, result, ws_root=ws_root, inv_slug=inv_slug)
        except ValueError as e:
            outcomes.append({**base, "outcome": "error", "reason": str(e)})
            continue
        outcomes.append({**base, "outcome": applied.get("status", "applied"),
                         "applied_to": applied.get("applied_to", [])})
    return outcomes


def subprocess_filler(cmd: str | None = None, *, timeout: float = 1800):
    """A filler that shells out to ``$VIVA_FILLER_CMD`` (or an explicit ``cmd``).

    Generalizes the decider seam (``$VIVA_DECIDER_CMD``): the command receives a
    :class:`GenerationRequest` as JSON on stdin and must print a
    :class:`GenerationResult` JSON on stdout. The engine stays LLM-free — the AI
    (or a deterministic filler) lives in that command (e.g. ``claude -p`` with a
    fill skill, or viva-superpowers' filler). Returns a
    ``fill(request) -> GenerationResult | None`` for :func:`run_requests`. With no
    command configured, ``fill`` returns ``None`` (nothing auto-fills). A malformed
    result is rejected (``validate_result``) so a bad artifact never applies."""
    import json
    import os
    import subprocess

    from discovery_engine.phases.generation import GenerationResult, validate_result
    command = cmd or os.environ.get("VIVA_FILLER_CMD")

    def fill(request):
        if not command:
            return None
        proc = subprocess.run(command, shell=True, check=False,
                              input=json.dumps(request.to_dict(), default=str),
                              capture_output=True, text=True, timeout=timeout)
        out = (proc.stdout or "").strip()
        if not out:
            return None
        doc = json.loads(out)
        validate_result(doc)                       # reject a malformed artifact loudly
        return GenerationResult(
            request_id=doc.get("request_id") or request.id,
            artifact=doc.get("artifact") or {},
            filler=doc.get("filler") or {"kind": "ai", "id": "VIVA_FILLER_CMD"},
            rationale=doc.get("rationale") or "",
            confidence=doc.get("confidence"),
            status=doc.get("status") or "applied",
            applied_to=doc.get("applied_to") or [],
        )

    return fill
