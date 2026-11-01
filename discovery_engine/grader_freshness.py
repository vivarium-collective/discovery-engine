"""Phase 2 of the unified graded-test ontology: resolve a computed grade's freshness.

A computed_outcomes block carries a ``_grader`` stamp (``study_evaluator._grader_stamp``):
``{version, graded_at, inputs_hash, store}``. Staleness is resolved **server-side / at publish
time** into an explicit ``fresh | stale`` flag that consumers just render — the read-only published
bundle has no engine to ask "what is the current grader?", so it must read a baked flag, never
recompute. This module is that resolver; the live server and the publish step call it and write the
flag; the Audit tab and the static bundle render it.

Rule: a grade is **stale** when its stamp is absent, its ``version`` is behind the current grader,
or (when the caller supplies a freshly-computed ``inputs_hash``) its inputs changed. A missing or
behind stamp is never rendered ``fresh`` — the point is to show "stale — re-grade" instead of a
confidently-wrong ✓met.
"""
from __future__ import annotations

from typing import Any

FRESH, STALE = "fresh", "stale"


def _current_version() -> str:
    # Imported lazily so this stays a leaf module (study_evaluator imports are heavier).
    from .study_evaluator import GRADER_VERSION
    return GRADER_VERSION


def grade_freshness(stamp: Any, *, current_version: str | None = None,
                    current_inputs_hash: str | None = None) -> str:
    """``fresh`` | ``stale`` for a ``_grader`` stamp, resolved server-side.

    ``stale`` when the stamp is absent/empty, its ``version`` differs from the current grader, or
    (only when ``current_inputs_hash`` is provided) the stamp's ``inputs_hash`` differs from it.
    Never returns ``fresh`` for an unstamped grade.
    """
    cur_v = current_version or _current_version()
    if not isinstance(stamp, dict) or not stamp.get("version"):
        return STALE
    if str(stamp.get("version")) != str(cur_v):
        return STALE
    if current_inputs_hash is not None and stamp.get("inputs_hash") != current_inputs_hash:
        return STALE
    return FRESH


def is_stale(stamp: Any, **kw: Any) -> bool:
    """Convenience boolean form of :func:`grade_freshness`."""
    return grade_freshness(stamp, **kw) == STALE


def annotate_freshness(computed_outcomes: dict, *, current_version: str | None = None,
                       current_inputs_hash: str | None = None) -> dict:
    """Write ``freshness: fresh|stale`` onto a ``computed_outcomes`` block's ``_grader`` stamp,
    in place (server-side / publish-time baking), and return the block. A block with no ``_grader``
    is stamped ``stale`` under a minimal ``_grader`` so the consumer still has a flag to render."""
    if not isinstance(computed_outcomes, dict):
        return computed_outcomes
    stamp = computed_outcomes.get("_grader")
    if not isinstance(stamp, dict):
        stamp = {}
        computed_outcomes["_grader"] = stamp
    stamp["freshness"] = grade_freshness(stamp, current_version=current_version,
                                         current_inputs_hash=current_inputs_hash)
    return computed_outcomes
