"""Hard release gates — invariants a run must satisfy regardless of its rubric score.

A gate violation fails the run even with a high score: the plan forbids shipping a "discovery"
that was not reviewed, has no held-out report, or does not replay. ``check_gates`` decides the
gates that are checkable from a run summary; the unauthorized-evidence-change gate is enforced
at write time by the evidence-authority guard (C2), and conservation/interface gates belong to
the model-composition benchmark (Benchmark 7). Pure; AI-free.
"""
from __future__ import annotations

from dataclasses import dataclass

UNREVIEWED_PROMOTION = "unreviewed_discovery_promotion"
MISSING_HOLDOUT_REPORT = "missing_observed_vs_heldout_report"
SNAPSHOT_REPLAY = "snapshot_not_identical_under_same_versions"


@dataclass(frozen=True)
class GateViolation:
    gate: str
    detail: str


def check_gates(run: dict) -> list:
    """Return the hard-gate violations of a run summary (``[]`` = clean).

    ``run`` carries ``inv`` (the final investigation spec), optional ``holdout`` (a held-out
    report/score), and optional ``snapshot_fingerprints`` (fingerprints produced under the
    same evaluator versions, which must be identical). Pure."""
    inv = run.get("inv") or {}
    viols: list = []

    discoveries = [d for d in (inv.get("discoveries") or []) if isinstance(d, dict)]
    promoted = {c.get("id") for c in (inv.get("finding_candidates") or [])
                if isinstance(c, dict) and c.get("status") == "promoted"}
    reviewed = {d.get("finding_candidate_id") for d in (inv.get("review_decisions") or [])
                if isinstance(d, dict)}
    for d in discoveries:
        fcid = d.get("finding_candidate_id")
        if not fcid or (fcid not in promoted and fcid not in reviewed):
            viols.append(GateViolation(
                UNREVIEWED_PROMOTION,
                f"discovery {d.get('id')!r} has no reviewed/promoted finding_candidate"))

    if discoveries and not run.get("holdout"):
        viols.append(GateViolation(
            MISSING_HOLDOUT_REPORT,
            "a discovery was recorded without an observed-vs-held-out report"))

    fps = run.get("snapshot_fingerprints")
    if isinstance(fps, (list, tuple)) and len(fps) >= 2 and len(set(fps)) > 1:
        viols.append(GateViolation(
            SNAPSHOT_REPLAY,
            "evaluation snapshot differs under identical grader/resolver versions"))

    return viols


def passes_gates(run: dict) -> bool:
    return not check_gates(run)
