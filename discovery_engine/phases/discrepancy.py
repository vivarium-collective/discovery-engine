"""Discrepancy — the typed, sourced trigger a generated hypothesis answers.

The engine already raises *untyped* signals when a measured value no live hypothesis
predicted turns up (an ``anomaly``) or when model arms disagree on the same observable
(a ``model_disagreement``). This wraps those signals into one typed object that always
names the measured evidence it came from (``source_evidence_ref``), so a ``hypothesize``
request can carry the exact surprise it must explain — a generated alternative is tied to
evidence, never to a prompt.

A Discrepancy is *derived* from the cycle bundle; it asserts no mechanism and is never
scored as evidence. Pure; AI-free; raises nothing except on an invalid hand-built object.

Shape (``cycle_result["discrepancies"][]``)::

    - id: dc:9f2a1c7b40de
      study: colonies-s1
      observable: obs.val
      kind: anomaly                       # anomaly | model_disagreement | coverage_gap
      observed: 0.95
      source_evidence_ref: colonies-s1#obs.val
      note: "observed outside every live band"
      proposed_question: "What mechanism puts obs.val at 0.95?"
"""
from __future__ import annotations

import hashlib

ANOMALY = "anomaly"
MODEL_DISAGREEMENT = "model_disagreement"
COVERAGE_GAP = "coverage_gap"
KINDS = (ANOMALY, MODEL_DISAGREEMENT, COVERAGE_GAP)


def _discrepancy_id(study: str, observable: str, kind: str) -> str:
    h = hashlib.sha1(f"{study}|{observable}|{kind}".encode()).hexdigest()[:12]
    return f"dc:{h}"


def validate_discrepancy(d: dict) -> dict:
    """Raise ``ValueError`` unless ``d`` satisfies the Discrepancy contract; else return it.

    Required: a ``study``, an ``observable``, a ``kind`` in :data:`KINDS`, and a non-empty
    ``source_evidence_ref`` (a discrepancy always names where its surprise came from)."""
    if not isinstance(d, dict):
        raise ValueError("discrepancy must be a mapping")
    for key in ("study", "observable", "source_evidence_ref"):
        if not str(d.get(key) or "").strip():
            raise ValueError(f"discrepancy needs a non-empty {key!r}")
    kind = d.get("kind")
    if kind not in KINDS:
        raise ValueError(f"discrepancy kind must be one of {KINDS}, got {kind!r}")
    return d


def build_discrepancy(study: str, observable: str, *, kind: str,
                      source_evidence_ref: str | None = None, observed=None,
                      live_bands: list | None = None, arms: dict | None = None,
                      spread=None, note: str = "", proposed_question: str = "") -> dict:
    """Assemble + validate a Discrepancy. Pure. ``source_evidence_ref`` defaults to
    ``study#observable`` when not supplied; the ``id`` is content-addressed."""
    d: dict = {
        "id": _discrepancy_id(str(study), str(observable), str(kind)),
        "study": str(study),
        "observable": str(observable),
        "kind": kind,
        "source_evidence_ref": str(source_evidence_ref or f"{study}#{observable}"),
        "note": str(note or ""),
    }
    if observed is not None:
        d["observed"] = observed
    if live_bands:
        d["live_bands"] = list(live_bands)
    if arms:
        d["arms"] = dict(arms)
    if spread is not None:
        d["spread"] = spread
    if proposed_question:
        d["proposed_question"] = str(proposed_question)
    return validate_discrepancy(d)


def discrepancies_from_cycle(cycle_result: dict) -> list[dict]:
    """Typed Discrepancies derived from the cycle bundle's ``signals`` (pure).

    ``anomalies`` -> ``anomaly``; *live* ``model_disagreements`` -> ``model_disagreement``
    (a resolved/averaged-away disagreement is skipped). Deduped by content-addressed id,
    stable order (anomalies first). An absent/empty ``signals`` block yields ``[]``."""
    signals = (cycle_result or {}).get("signals") or {}
    out: list[dict] = []
    seen: set[str] = set()

    def _add(d: dict) -> None:
        if d["id"] not in seen:
            seen.add(d["id"])
            out.append(d)

    for a in signals.get("anomalies") or []:
        if not isinstance(a, dict):
            continue
        study, obs = a.get("study"), a.get("observable")
        if not study or not obs:
            continue
        _add(build_discrepancy(
            study, obs, kind=ANOMALY, observed=a.get("observed"),
            note=str(a.get("note") or ""), proposed_question=str(a.get("proposed_question") or "")))

    for m in signals.get("model_disagreements") or []:
        if not isinstance(m, dict) or not m.get("disagreement"):
            continue
        study, obs = m.get("study"), m.get("observable")
        if not study or not obs:
            continue
        _add(build_discrepancy(
            study, obs, kind=MODEL_DISAGREEMENT, arms=m.get("arms"),
            spread=m.get("spread"), note=str(m.get("note") or "")))

    return out
