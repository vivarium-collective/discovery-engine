"""Investigation-level narrative freshness.

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

Wiring: ``discover.build_cycle_state`` / ``persist_cycle`` attach
``investigation_freshness`` to ``cycle_state.freshness`` so each top tab carries its
state. ``persist_cycle`` does the mechanical decisions reconcile ONLY
(``_renew_executive_decisions``). The narrative stamp is ``stamp_narrative`` -- reached
through the accept-gated ``discover.refresh_narrative`` (rationale required); persist must
never call it, or the Narrative tab could never expire and prose the author never re-read
would be falsely certified. The verdict accept is ``discover.accept_verdict``.
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
    """Content fingerprint of what the narrative prose is authored against -- each
    member study's claim, verdict, and the full claims/findings content (not a count).
    Deterministic: rows sorted, no clock/uuid."""
    sm = _studies_map(studies)
    rows = sorted(
        (slug, sp.get("claim") or "", sp.get("verdict") or "",
         sp.get("claims") or [], sp.get("findings") or [])
        for slug, sp in sm.items()
    )
    return _hash8(rows)


def _resolved_targets(inv_spec: dict) -> set:
    return {r.get("target") for r in (inv_spec.get("resolutions") or []) if isinstance(r, dict)}


def _norm(v) -> str:
    return str(v or "").strip().lower()


def classify_verdict(inv_spec: dict, computed_verdict_status: str | None) -> dict:
    """Derived: EXPIRED when the authored verdict diverges from the computed one, using
    the engine's normalized divergence (``executive.computed_acceptance.
    diverges_from_authored`` if present, else strip+lower compare like
    ``investigation_status``)."""
    ex = inv_spec.get("executive") or {}
    authored = ex.get("verdict_status")
    acc = ex.get("computed_acceptance")
    if isinstance(acc, dict) and acc.get("diverges_from_authored") is not None:
        diverges = bool(acc["diverges_from_authored"])
    else:
        diverges = bool(_norm(authored) and _norm(computed_verdict_status)
                        and _norm(authored) != _norm(computed_verdict_status))
    if diverges:
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
    tabs = {
        "verdict": classify_verdict(inv_spec, computed_verdict_status),
        "decisions": classify_decisions(inv_spec),
        "narrative": classify_narrative(inv_spec, studies),
    }
    return {**tabs, "any_expired": any(c["state"] == EXPIRED for c in tabs.values())}


def _now() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")


def stamp_narrative(inv_spec: dict, studies: dict | None, *, rationale: str | None,
                    by: str | None) -> dict:
    """Accept-gated refresh of the Narrative tab: record the current basis. Requires a
    non-blank rationale (a human re-read the prose) and real studies. Mirrors
    viz_freshness.stamp_meta, but is NEVER called at persist time."""
    if not (rationale or "").strip():
        raise ValueError("stamp_narrative requires a non-empty rationale")
    if not _studies_map(studies):
        raise ValueError("stamp_narrative requires member studies (refusing an empty basis)")
    ex = inv_spec.setdefault("executive", {})
    basis = narrative_basis(studies)
    ex["narrative_basis"] = basis
    ex.setdefault("narrative_history", []).append(
        {"basis": basis, "rationale": rationale.strip(), "by": by or "human", "at": _now()})
    return inv_spec


def renew_executive(on_disk: dict, studies: dict | None = None, *,
                    computed_verdict_status: str | None = None,
                    accept_verdict_rationale: str | None = None,
                    by: str | None = "accept") -> dict:
    """Persist-time renew: the mechanical decisions reconciliation (delegates to
    ``_renew_executive_decisions``) plus an OPTIONAL rationale-gated verdict acceptance.
    It never stamps the narrative (see ``stamp_narrative``); ``studies`` is accepted for
    interface symmetry and may be None."""
    from .discover import _renew_executive_decisions  # lazy: discover imports this module

    ex = on_disk.get("executive")
    if not isinstance(ex, dict):
        return on_disk
    _renew_executive_decisions(on_disk)
    rationale = (accept_verdict_rationale or "").strip()
    if rationale and computed_verdict_status:
        ex.setdefault("verdict_history", []).append(
            {"from": ex.get("verdict_status"), "to": computed_verdict_status,
             "rationale": rationale, "by": by or "accept", "at": _now()})
        ex["verdict_status"] = computed_verdict_status
    return on_disk
