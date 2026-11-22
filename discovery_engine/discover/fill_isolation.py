"""Isolation tool for the filler seam — reusable grounding + recording wrappers that any driver
(``discover investigate``, ``discover campaign``, or a bespoke loop) composes around its
``fill(request) -> GenerationResult | None``.

Two generic, additive wrappers plus a composer:

- :func:`resource_fill` — inject author-declared knowledge ``resources`` into every request's
  ``context["resources"]`` so the filler reasons from engine-supplied background instead of reaching
  outside the request. Like the steering goal, it rides in ``context`` via ``dataclasses.replace``,
  so the content-addressed request ``id`` (derived from ``kind``/``target``/``provenance``, not
  ``context``) is unchanged.
- :func:`recording_fill` — append each ``(request, result)`` to a JSONL transcript, making a run's
  controlled prompts and the filler's responses an auditable, reproducible artifact. Transparent:
  it records and returns the result unchanged, and a write failure never blocks a fill.
- :func:`isolate_fill` — compose the two so the transcript captures the FULLY-injected request
  (resources, then recording innermost): ``resource_fill(recording_fill(fill, transcript), resources)``.

All wrappers are identity when there is nothing to inject (no resources / no transcript / ``fill is
None``), so a run without them is byte-identical to one that never imported this module. Nothing here
touches the frontier, gates, budget, or the honesty rules — only the seam the filler is called through.
"""
from __future__ import annotations

import dataclasses
import datetime
import json
from pathlib import Path


def load_resources(resources_dir) -> list[dict]:
    """Load declared text resources (``*.md``) from a directory into a JSON-safe list the engine
    injects as ``context["resources"]`` — ``[{"name": str, "content": str}, ...]``. Returns an empty
    list when the directory is absent or empty, so ``--resources`` on a bad path is a no-op."""
    d = Path(resources_dir) if resources_dir else None
    if not d or not d.is_dir():
        return []
    return [{"name": p.name, "content": p.read_text(encoding="utf-8")}
            for p in sorted(d.glob("*.md")) if p.is_file()]


def resource_fill(fill, resources):
    """Wrap ``fill`` so every request carries ``resources`` in ``context["resources"]``. Identity
    when there are no resources (or ``fill is None``)."""
    if not resources or fill is None:
        return fill

    def grounded(request):
        return fill(dataclasses.replace(
            request, context={**(request.context or {}), "resources": resources}))

    return grounded


def _result_summary(result):
    """A JSON-safe summary of a ``GenerationResult`` (or ``None``) for the transcript."""
    if result is None:
        return None
    if isinstance(result, dict):
        return result
    return {k: getattr(result, k, None)
            for k in ("request_id", "artifact", "filler", "rationale", "confidence")
            if hasattr(result, k)}


def recording_fill(fill, transcript_path):
    """Wrap ``fill`` so every ``(request, result)`` is appended to ``transcript_path`` (JSONL).
    Identity when there is no transcript (or ``fill is None``); a write failure never blocks a fill."""
    if not transcript_path or fill is None:
        return fill
    path = Path(transcript_path)

    def recorded(request):
        result = fill(request)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({
                    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
                    "request": {"id": getattr(request, "id", None),
                                "kind": getattr(request, "kind", None),
                                "target": getattr(request, "target", None),
                                "context": getattr(request, "context", None) or {}},
                    "result": _result_summary(result),
                }, default=str) + "\n")
        except Exception:  # noqa: BLE001 — recording is best-effort, never block the loop
            pass
        return result

    return recorded


def isolate_fill(fill, *, resources=None, transcript=None):
    """Compose the isolation wrappers so the transcript captures the fully-injected request:
    ``resource_fill(recording_fill(fill, transcript), resources)``. Identity when both are empty."""
    return resource_fill(recording_fill(fill, transcript), resources)
