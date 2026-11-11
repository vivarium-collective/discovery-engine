"""FR-08.02 — a Discovery record on a verdict flip (PR 5).

Passing a gate is not a discovery. A *scientific* discovery is a claim verdict that
FLIPS to a settled outcome on VERIFIED evidence: a claim newly established
(``supported``) or overturned (``contested``). This module diffs the persisted
evaluation snapshot against the freshly computed one and builds a first-class
``Discovery`` record — ``scope`` (what the claim is), ``novelty`` (established vs
overturned), ``counterevidence`` (the refuting findings, for an overturn) and
``evidence_refs`` (the findings + graded runs behind it).

Pure. ``persist_cycle`` calls :func:`detect_discoveries` with the prior and new
``cycle_state.evaluation_snapshot`` and appends the results to
``investigation.yaml:discoveries[]`` (idempotent by content id).
"""
from __future__ import annotations

import datetime as _dt
import hashlib

from discovery_engine.phases.evaluation import CONTESTED, SUPPORTED
from discovery_engine.phases.evidence_integrity import VERIFIED

# A flip TO one of these settled verdicts, on verified evidence, is a discovery.
# `provisionally-supported` (unverified), `unresolved` and `gated` are not.
_DISCOVERY_VERDICTS = (SUPPORTED, CONTESTED)

_REFUTING = {"contradicts", "refutes", "refuted", "contradicted"}


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def _discovery_id(study: str, to_verdict: str, fingerprint: str | None) -> str:
    h = hashlib.sha1(f"{study}|{to_verdict}|{fingerprint or ''}".encode()).hexdigest()[:12]
    return f"disc:{study}:{h}"


def _counterevidence(study_spec: dict | None) -> list[dict]:
    """The refuting findings behind a ``contested`` flip (statement + observed value)."""
    out: list[dict] = []
    for f in (study_spec or {}).get("findings") or []:
        if isinstance(f, dict) and str(f.get("status") or "").strip().lower() in _REFUTING:
            out.append({"finding": f.get("id"), "statement": f.get("statement"),
                        "observed": (f.get("evidence") or {}).get("observed")})
    return out


def _evidence_refs(study_spec: dict | None) -> list[str]:
    """Finding ids + graded-run ids backing the verdict."""
    refs: list[str] = []
    for f in (study_spec or {}).get("findings") or []:
        if isinstance(f, dict) and f.get("id"):
            refs.append(f"finding:{f['id']}")
    for r in (study_spec or {}).get("runs") or []:
        if isinstance(r, dict) and r.get("computed_outcomes") and r.get("run_id"):
            refs.append(f"run:{r['run_id']}")
    return refs


def build_discovery(study: str, from_verdict, to_verdict: str, study_spec: dict | None,
                    *, fingerprint: str | None, at: str | None = None) -> dict:
    """Build one Discovery record for a verdict flip. Pure."""
    refuted = to_verdict == CONTESTED
    claim = (study_spec or {}).get("claim") or (study_spec or {}).get("question") or study
    return {
        "id": _discovery_id(study, to_verdict, fingerprint),
        "study": study,
        "from_verdict": from_verdict,
        "to_verdict": to_verdict,
        "scope": str(claim)[:400],
        "novelty": ("claim overturned on verified counterevidence" if refuted
                    else "claim newly established on verified evidence"),
        "counterevidence": _counterevidence(study_spec) if refuted else [],
        "evidence_refs": _evidence_refs(study_spec),
        "fingerprint": fingerprint,
        "at": at or _now(),
    }


def detect_discoveries(prev_snapshot: dict | None, new_snapshot: dict | None,
                       studies: dict | None, *, fingerprint: str | None = None) -> list[dict]:
    """Discoveries for every study whose claim verdict flipped to a settled outcome
    (``supported``/``contested``) on VERIFIED evidence since ``prev_snapshot``.

    A flip to ``unresolved``/``gated``/``provisionally-supported``, or to a settled
    verdict on evidence that is not ``verified``, is NOT a discovery (passing a gate is
    not a discovery). A verdict that did not change is not a discovery either.
    """
    prev_by = (prev_snapshot or {}).get("by_study") or {}
    new_by = (new_snapshot or {}).get("by_study") or {}
    fp = fingerprint if fingerprint is not None else (new_snapshot or {}).get("fingerprint")
    out: list[dict] = []
    for slug, nb in sorted(new_by.items()):
        if not isinstance(nb, dict):
            continue
        to_v = nb.get("status")
        if to_v not in _DISCOVERY_VERDICTS:
            continue
        if nb.get("evidence") != VERIFIED:          # passing a gate != a discovery
            continue
        from_v = (prev_by.get(slug) or {}).get("status")
        if from_v == to_v:                          # no flip
            continue
        out.append(build_discovery(slug, from_v, to_v, (studies or {}).get(slug), fingerprint=fp))
    return out
