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


def _novelty_text(to_verdict: str) -> str:
    return ("claim overturned on verified counterevidence" if to_verdict == CONTESTED
            else "claim newly established on verified evidence")


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
        "novelty": _novelty_text(to_verdict),
        "counterevidence": _counterevidence(study_spec) if refuted else [],
        "evidence_refs": _evidence_refs(study_spec),
        "fingerprint": fingerprint,
        "at": at or _now(),
    }


def _verdict_flips(prev_snapshot: dict | None, new_snapshot: dict | None):
    """Yield ``(slug, from_verdict, to_verdict)`` for every study whose claim verdict
    FLIPPED to a settled outcome (``supported``/``contested``) on VERIFIED evidence.

    A flip to ``unresolved``/``gated``/``provisionally-supported``, a settled verdict on
    evidence that is not ``verified`` (passing a gate), or a verdict that did not change,
    is not yielded. Pure. The single source of truth for "what moved this cycle" — both
    the FindingCandidate detector and the (back-compat) Discovery detector build on it."""
    prev_by = (prev_snapshot or {}).get("by_study") or {}
    new_by = (new_snapshot or {}).get("by_study") or {}
    for slug, nb in sorted(new_by.items()):
        if not isinstance(nb, dict):
            continue
        to_v = nb.get("status")
        if to_v not in _DISCOVERY_VERDICTS:
            continue
        if nb.get("evidence") != VERIFIED:          # passing a gate != a settled result
            continue
        from_v = (prev_by.get(slug) or {}).get("status")
        if from_v == to_v:                          # no flip
            continue
        yield slug, from_v, to_v


def detect_discoveries(prev_snapshot: dict | None, new_snapshot: dict | None,
                       studies: dict | None, *, fingerprint: str | None = None) -> list[dict]:
    """Back-compat: the Discovery records a verdict flip *implies*, unreviewed.

    Retained for callers/tests that want the direct flip→Discovery mapping. The cycle no
    longer uses this path: a flip now yields a :func:`FindingCandidate` that a reviewed
    promotion (:func:`promote_finding_candidate`) turns into a Discovery, so a Discovery is
    a reviewed characterization of novelty — not an automatic synonym for a verdict flip.
    """
    fp = fingerprint if fingerprint is not None else (new_snapshot or {}).get("fingerprint")
    return [build_discovery(slug, from_v, to_v, (studies or {}).get(slug), fingerprint=fp)
            for slug, from_v, to_v in _verdict_flips(prev_snapshot, new_snapshot)]


# ---------------------------------------------------------------------------
# FindingCandidate → (reviewed) → Discovery
#
# A verified verdict flip is an evidence fact, not yet a Discovery. The cycle records
# it as a FindingCandidate (what the evidence now supports/contests, with its refs and
# counterevidence). A Discovery is minted only by a *reviewed* promotion that attaches a
# DiscoveryAssessment (novelty / significance / validation / decision) — deterministic
# first-pass by the engine, overridable by a human or the DiscoveryAgent. Nothing writes
# a Discovery directly from a flip.
# ---------------------------------------------------------------------------

CANDIDATE = "candidate"
PROMOTED = "promoted"
HELD = "held"
_NOVELTY_ESTABLISHED = "established"
_NOVELTY_OVERTURNED = "overturned"
_NOVELTY_REDUNDANT = "redundant"


def _candidate_id(study: str, to_verdict: str, fingerprint: str | None) -> str:
    h = hashlib.sha1(f"{study}|{to_verdict}|{fingerprint or ''}".encode()).hexdigest()[:12]
    return f"fc:{study}:{h}"


def build_finding_candidate(study: str, from_verdict, to_verdict: str, study_spec: dict | None,
                            *, fingerprint: str | None, at: str | None = None) -> dict:
    """One FindingCandidate for a verified verdict flip. Pure. Carries the evidence the
    flip rests on (claim, evidence_refs, counterevidence) but makes NO novelty claim —
    that is the reviewer's job (:func:`assess_finding_candidate`)."""
    claim = (study_spec or {}).get("claim") or (study_spec or {}).get("question") or study
    return {
        "id": _candidate_id(study, to_verdict, fingerprint),
        "kind": "finding_candidate",
        "study": study,
        "from_verdict": from_verdict,
        "to_verdict": to_verdict,
        "claim": str(claim)[:400],
        "validation_status": "verified",
        "evidence_refs": _evidence_refs(study_spec),
        "counterevidence": _counterevidence(study_spec) if to_verdict == CONTESTED else [],
        "fingerprint": fingerprint,
        "status": CANDIDATE,
        "at": at or _now(),
    }


def detect_finding_candidates(prev_snapshot: dict | None, new_snapshot: dict | None,
                              studies: dict | None, *, fingerprint: str | None = None) -> list[dict]:
    """FindingCandidates for every verified verdict flip since ``prev_snapshot``. Pure.
    This is the cycle's discovery-side output; promotion to a Discovery is a separate,
    reviewed step."""
    fp = fingerprint if fingerprint is not None else (new_snapshot or {}).get("fingerprint")
    return [build_finding_candidate(slug, from_v, to_v, (studies or {}).get(slug), fingerprint=fp)
            for slug, from_v, to_v in _verdict_flips(prev_snapshot, new_snapshot)]


def assess_finding_candidate(candidate: dict, existing_discoveries=None, *,
                             reviewer: str = "engine", at: str | None = None) -> dict:
    """A deterministic first-pass DiscoveryAssessment over a candidate. Pure.

    Novelty is ``redundant`` (→ hold) when a Discovery already stands for this study's
    flip to the same verdict; otherwise ``established`` (supported) / ``overturned``
    (contested) → promote. ``significance`` and ``external_validity`` are deterministic
    placeholders a human or the DiscoveryAgent can sharpen before promotion."""
    study, to_v = candidate.get("study"), candidate.get("to_verdict")
    already = any(isinstance(d, dict) and d.get("study") == study and d.get("to_verdict") == to_v
                  for d in (existing_discoveries or []))
    if already:
        novelty, decision, why = _NOVELTY_REDUNDANT, "hold", "a Discovery already records this verdict flip"
    else:
        novelty = _NOVELTY_OVERTURNED if to_v == CONTESTED else _NOVELTY_ESTABLISHED
        decision, why = "promote", "a settled verdict change on verified evidence, not previously recorded"
    return {
        "novelty": novelty,
        "significance": "settled-verdict-change",      # deterministic placeholder
        "external_validity": "single-investigation",   # deterministic placeholder
        "review_decision": decision,                   # promote | hold
        "reviewed_by": reviewer,
        "rationale": why,
        "at": at or _now(),
    }


def promote_finding_candidate(candidate: dict, assessment: dict, *, at: str | None = None) -> dict:
    """Build the Discovery a reviewed candidate promotion yields. Pure. The Discovery
    carries its ``assessment`` and links back to the ``finding_candidate_id`` it came
    from; its id matches :func:`build_discovery` so the two paths stay idempotent."""
    study, to_v = candidate["study"], candidate["to_verdict"]
    return {
        "id": _discovery_id(study, to_v, candidate.get("fingerprint")),
        "study": study,
        "from_verdict": candidate.get("from_verdict"),
        "to_verdict": to_v,
        "scope": candidate.get("claim"),
        "novelty": _novelty_text(to_v),
        "counterevidence": candidate.get("counterevidence") or [],
        "evidence_refs": candidate.get("evidence_refs") or [],
        "fingerprint": candidate.get("fingerprint"),
        "finding_candidate_id": candidate.get("id"),
        "assessment": assessment,
        "schema": "reviewed-0.6",
        "at": at or _now(),
    }
