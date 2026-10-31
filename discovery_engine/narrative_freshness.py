"""Investigation-level narrative freshness (PROTOTYPE, review-only).

The generalization of ``viz_freshness`` from figures to investigation-level
artifacts (verdict / decisions / narrative). A chart is stale when its stamped
``source_run_id`` no longer matches the study's latest run; an investigation tab is
EXPIRED when its authored basis no longer matches the current derived basis.

Design (per the engine owner's steer; mirrors viz_freshness exactly):
  * freshness is purely DERIVED — authored basis vs current basis, no hidden state;
  * NON-BLOCKING — an expired tab is an affordance, never a closure block;
  * refresh/accept is RATIONALE-GATED — authored prose is never silently overwritten.

Per-tab basis (the generalization of a chart's ``source_run_id``):
  * Verdict      — authored ``executive.verdict_status`` vs the cycle's
    ``computed_verdict_status`` (this IS the existing ``verdict_divergence`` signal).
  * Decisions    — authored ``decisions_needed`` vs the cycle's ``resolutions``
    (the ``_renew_executive_decisions`` reconciliation, surfaced as a state).
  * Narrative    — a content fingerprint of the member studies' claims/findings the
    prose was authored against (stamped on ``executive.narrative_basis``), vs current.

Wiring (for the engine owner): call ``investigation_freshness`` where the investigation
summary is built so each top tab carries its state; call ``renew_executive`` from
``persist_cycle`` beside ``_renew_executive_decisions`` (it supersets it) so an
accepted refresh stamps the narrative + (rationale-gated) accepts the computed verdict.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

FRESH, EXPIRED, UNSTAMPED = "fresh", "expired", "unstamped"


def _hash8(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:8]


def _studies_map(studies: dict | None) -> dict:
    return {k: v for k, v in (studies or {}).items() if isinstance(v, dict)}


def narrative_basis(studies: dict | None) -> str:
    """Content fingerprint of what the narrative prose is authored against — each
    member study's one-line claim + verdict + finding count. Changes when the studies'
    conclusions change, which is exactly when Background/Narrative/Biology go stale."""
    sm = _studies_map(studies)
    rows = sorted(
        (slug, sp.get("claim") or "", sp.get("verdict") or "", len(sp.get("findings") or []))
        for slug, sp in sm.items()
    )
    return _hash8(rows)


def _resolved_targets(inv_spec: dict) -> set:
    return {r.get("target") for r in (inv_spec.get("resolutions") or []) if isinstance(r, dict)}


def classify_verdict(inv_spec: dict, computed_verdict_status: str | None) -> dict:
    """Derived: EXPIRED when the authored verdict_status diverges from the computed one."""
    authored = (inv_spec.get("executive") or {}).get("verdict_status")
    if computed_verdict_status and authored and authored != computed_verdict_status:
        return {
            "state": EXPIRED,
            "authored": authored,
            "computed": computed_verdict_status,
            "detail": f"authored verdict '{authored}' diverges from computed '{computed_verdict_status}'",
        }
    return {"state": FRESH, "authored": authored, "computed": computed_verdict_status}


def classify_decisions(inv_spec: dict) -> dict:
    """Derived: EXPIRED when a listed decision has been resolved by the cycle but not
    yet renewed out of ``decisions_needed``."""
    decisions = (inv_spec.get("executive") or {}).get("decisions_needed") or []
    resolved = _resolved_targets(inv_spec)

    def _q(d):
        return d.get("question") if isinstance(d, dict) else d

    stale = [_q(d) for d in decisions if _q(d) in resolved]
    if stale:
        return {"state": EXPIRED, "resolved_pending": stale,
                "detail": f"{len(stale)} decision(s) resolved by the cycle but still listed — refresh to renew"}
    return {"state": FRESH}


def classify_narrative(inv_spec: dict, studies: dict | None) -> dict:
    """Derived: FRESH when the stamped narrative basis matches the current study
    claims/findings; EXPIRED when they changed since authoring; UNSTAMPED if never
    stamped (untracked — no false alarm)."""
    stamp = (inv_spec.get("executive") or {}).get("narrative_basis")
    current = narrative_basis(studies)
    if not stamp:
        return {"state": UNSTAMPED, "basis": current}
    if stamp == current:
        return {"state": FRESH, "basis": current}
    return {"state": EXPIRED, "basis": current, "stamped": stamp,
            "detail": "member-study claims/findings changed since the narrative was authored"}


def investigation_freshness(inv_spec: dict, studies: dict | None, *,
                            computed_verdict_status: str | None = None) -> dict:
    """Per-tab freshness for the investigation top tabs — rendered ON each tab, not just
    as a global badge. Pure + non-blocking."""
    inv_spec = inv_spec or {}
    return {
        "verdict": classify_verdict(inv_spec, computed_verdict_status),
        "decisions": classify_decisions(inv_spec),
        "narrative": classify_narrative(inv_spec, studies),
        "any_expired": any(
            c["state"] == EXPIRED for c in (
                classify_verdict(inv_spec, computed_verdict_status),
                classify_decisions(inv_spec),
                classify_narrative(inv_spec, studies),
            )
        ),
    }


def stamp_narrative(inv_spec: dict, studies: dict | None) -> dict:
    """Record the current narrative basis (the refresh/accept action for the Narrative
    tabs). Mirrors viz_freshness.stamp_meta."""
    ex = inv_spec.setdefault("executive", {})
    ex["narrative_basis"] = narrative_basis(studies)
    return inv_spec


def renew_executive(on_disk: dict, studies: dict | None = None, *,
                    computed_verdict_status: str | None = None,
                    accept_verdict_rationale: str | None = None) -> dict:
    """Supersets ``_renew_executive_decisions``: reconcile resolved decisions AND
    (rationale-gated) accept the computed verdict AND stamp the narrative basis.

    Never overwrites authored prose without a rationale: the verdict is only accepted
    when ``accept_verdict_rationale`` is given (the Verdict tab's accept action). The
    decisions reconciliation + narrative stamp are the mechanical refresh."""
    ex = on_disk.get("executive")
    if not isinstance(ex, dict):
        return on_disk
    # --- decisions: move cycle-resolved out of the open list (mirrors existing renew) ---
    decisions = ex.get("decisions_needed") or []
    resolved = _resolved_targets(on_disk)

    def _q(d):
        return d.get("question") if isinstance(d, dict) else d

    newly = [d for d in decisions if _q(d) in resolved]
    if newly:
        archived = list(ex.get("decisions_resolved") or [])
        seen = {_q(d) for d in archived}
        for d in newly:
            if _q(d) not in seen:
                archived.append(d)
                seen.add(_q(d))
        ex["decisions_needed"] = [d for d in decisions if _q(d) not in resolved]
        ex["decisions_resolved"] = archived
    # --- verdict: accept the computed status only with a rationale (never silent) ---
    if accept_verdict_rationale and computed_verdict_status:
        ex.setdefault("verdict_history", []).append(
            {"from": ex.get("verdict_status"), "to": computed_verdict_status,
             "rationale": accept_verdict_rationale, "by": "accept"})
        ex["verdict_status"] = computed_verdict_status
    # --- narrative: stamp the current basis (refresh) ---
    ex["narrative_basis"] = narrative_basis(studies)
    return on_disk
