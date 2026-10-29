"""Scientific evaluation and integration — Discovery Engine 2.0, Wave 3/4 (Cycle N+1 Phase F).

Promote or reject claims under one multi-axis assessment, and record the per-cycle
scientific change as a ``DiscoveryDelta``. The axes are kept separate on purpose:
a hypothesis's *support* (does the evidence back it?) is distinct from its evidence
*integrity* (is the evidence a registered run?). A claim backed only by PROVISIONAL
evidence is **provisionally supported**, never promoted to accepted — the Milestone 3
evidence-to-claim gate. Model *disagreement* (two arms, two answers) is surfaced as a
scientific question, not averaged away. "Never treat two simulations of related
assumptions as independent empirical confirmation."

Derive-first: every function here is a pure projection over the investigation spec,
the study specs, and (optionally) the Phase-A evidence state.
"""
from __future__ import annotations

from typing import Any

from .hypotheses import (
    _as_list, _RETIRED_STATUS, _to_float, anomalies, discriminating_hypotheses,
    fidelity_flags, rollup_support, support_summary,
)
from .evidence_integrity import VERIFIED

# Claim verdicts (the scientific axis — separate from the evidence-integrity axis).
SUPPORTED = "supported"
PROVISIONAL_SUPPORT = "provisionally-supported"
CONTESTED = "contested"
UNRESOLVED = "unresolved"
GATED = "gated"


def _study_hyps(inv_spec: dict, slug: str) -> list[dict]:
    return [h for h in _as_list(inv_spec.get("hypotheses"))
            if isinstance(h, dict) and h.get("study") == slug
            and str(h.get("status", "open")).strip().lower() not in _RETIRED_STATUS]


def evaluate_claim_support(summary: dict, evidence_status: str | None) -> str:
    """Map a hypothesis's support tally + its study's evidence integrity to a claim
    verdict. Net support needs VERIFIED evidence to be ``supported``; otherwise it is
    ``provisionally-supported``. Net-negative is ``contested``; zero is ``unresolved``."""
    net = (summary or {}).get("net", 0)
    if net > 0:
        return SUPPORTED if evidence_status == VERIFIED else PROVISIONAL_SUPPORT
    if net < 0:
        return CONTESTED
    return UNRESOLVED


def compare_hypotheses(inv_spec: dict, studies: dict, evidence_state: dict | None = None) -> dict:
    """Per study, the competing hypotheses with their support tally and claim verdict,
    plus the study's favored hypothesis (or that it is gated/unresolved). Reuses the
    rollup; folds in the Phase-A evidence status and the stage-4/6 fidelity caveats."""
    specs = list(studies.values()) if isinstance(studies, dict) else list(studies or [])
    rolled = rollup_support(inv_spec or {}, specs)
    disc = discriminating_hypotheses(inv_spec or {})
    flags = fidelity_flags(inv_spec or {}, specs)
    gated = {f["study"] for f in flags if f["kind"] in ("superseded_evidence", "gated_evidence")}
    ev_by_study = (evidence_state or {}).get("by_study", {})

    out: dict[str, dict] = {}
    for slug in {h.get("study") for h in _as_list(inv_spec.get("hypotheses")) if isinstance(h, dict)}:
        if slug is None:
            continue
        ev_status = (ev_by_study.get(slug) or {}).get("status")
        hyps = []
        for h in rolled.get("hypotheses", []):
            if h.get("study") != slug:
                continue
            s = support_summary(h.get("support_log") or [])
            hyps.append({"id": h.get("id"), "support": s,
                         "verdict": evaluate_claim_support(s, ev_status)})
        favored = max((h for h in hyps if h["support"]["net"] > 0),
                      key=lambda h: h["support"]["net"], default=None)
        note = None
        if slug in gated:
            note = "model-fidelity gated — a superseded or multi-arm result; see fidelity caveats"
        out[slug] = {
            "observable": (disc.get(slug) or {}).get("observable"),
            "hypotheses": hyps,
            "favored": favored["id"] if favored else None,
            "status": GATED if slug in gated else (favored["verdict"] if favored else UNRESOLVED),
            "note": note, "evidence_status": ev_status,
        }
    return out


def _arm_values(finding: dict) -> dict:
    """Pull the per-arm values a finding records across models/regimes/topologies."""
    ev = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    arms: dict[str, float] = {}
    for key in ("by_topology", "by_arm", "by_regime", "by_scenario", "values"):
        v = ev.get(key)
        if isinstance(v, dict):
            for name, val in v.items():
                f = _to_float(val)
                if f is not None:
                    arms[str(name)] = f
    sup = ev.get("superseded")
    if isinstance(sup, dict) and _to_float(sup.get("observed")) is not None:
        arms[f"superseded:{sup.get('source', 'prior')}"[:40]] = _to_float(sup["observed"])
    return arms


def model_disagreement_map(inv_spec: dict, studies: dict) -> list[dict]:
    """Where models/arms disagree on the same observable within a study — surfaced as
    a scientific question to investigate, not averaged. Returns one entry per study
    whose recorded arms span a non-trivial range."""
    specs = studies.values() if isinstance(studies, dict) else (studies or [])
    out: list[dict] = []
    for spec in specs:
        if not isinstance(spec, dict):
            continue
        slug = spec.get("name") or spec.get("slug")
        for f in _as_list(spec.get("findings")):
            if not isinstance(f, dict):
                continue
            arms = _arm_values(f)
            if len(arms) < 2:
                continue
            vals = list(arms.values())
            spread = max(vals) - min(vals)
            ev = f.get("evidence") or {}
            out.append({
                "study": slug, "observable": ev.get("measure") or ev.get("observable"),
                "arms": arms, "spread": round(spread, 4),
                "disagreement": spread >= 0.2,
                "note": ("arms disagree — investigate the model disagreement, do not average"
                         if spread >= 0.2 else "arms broadly agree"),
            })
    return out


def invalidate_dependents(changed: dict, inv_spec: dict, studies: dict) -> list[dict]:
    """Reverse dependency index: on an upstream change (a hypothesis, model, or a
    study's evidence), mark the downstream artifacts REVIEW_REQUIRED rather than
    silently overwriting. ``changed`` = ``{kind, id}`` (kind: study | hypothesis | model)."""
    kind, cid = changed.get("kind"), changed.get("id")
    affected: list[dict] = []
    if kind in ("study", "model") and cid:
        for h in _study_hyps(inv_spec, cid):
            affected.append({"artifact": f"hypothesis:{h.get('id')}", "status": "REVIEW_REQUIRED",
                             "reason": f"depends on {kind} {cid}, which changed"})
        affected.append({"artifact": f"claim:{cid}", "status": "REVIEW_REQUIRED",
                         "reason": f"the {kind} {cid} changed — re-evaluate the claim and narrative"})
        affected.append({"artifact": "narrative", "status": "STALE",
                         "reason": f"a downstream claim for {cid} is under review"})
    elif kind == "hypothesis" and cid:
        for h in _as_list(inv_spec.get("hypotheses")):
            if isinstance(h, dict) and h.get("id") == cid:
                affected.append({"artifact": f"claim:{h.get('study')}", "status": "REVIEW_REQUIRED",
                                 "reason": f"hypothesis {cid} changed"})
                affected.append({"artifact": "narrative", "status": "STALE",
                                 "reason": f"hypothesis {cid} changed"})
    return affected


def discovery_delta(inv_spec: dict, studies: dict, evidence_state: dict | None = None) -> dict:
    """The per-cycle DiscoveryDelta: what this cycle established, qualified, gated, or
    left anomalous, relative to a start-of-cycle state in which nothing was registered.
    Records added/weakened claims, model disagreements, anomalies and scientific
    regressions (a superseded over-claim is progress, recorded explicitly)."""
    specs = list(studies.values()) if isinstance(studies, dict) else list(studies or [])
    cmp = compare_hypotheses(inv_spec, studies, evidence_state)
    anoms = anomalies(inv_spec or {}, specs)
    flags = fidelity_flags(inv_spec or {}, specs)
    disagreements = [d for d in model_disagreement_map(inv_spec, studies) if d["disagreement"]]

    added, gated_claims, regressions = [], [], []
    for slug, c in cmp.items():
        if c["status"] == PROVISIONAL_SUPPORT and c["favored"]:
            added.append({"study": slug, "claim": c["favored"], "strength": PROVISIONAL_SUPPORT,
                          "scope": c.get("evidence_status")})
        elif c["status"] == SUPPORTED and c["favored"]:
            added.append({"study": slug, "claim": c["favored"], "strength": SUPPORTED})
        elif c["status"] == GATED:
            gated_claims.append({"study": slug, "reason": c.get("note")})
    for f in flags:
        if f["kind"] == "superseded_evidence":
            regressions.append({"study": f["study"],
                                "note": "a prior over-claim was superseded by a higher-fidelity arm — "
                                        "recorded as progress, not hidden: " + f["detail"]})

    import hashlib
    import json
    fp = hashlib.sha256(json.dumps(
        {k: cmp[k]["status"] for k in sorted(cmp)} | {"anoms": len(anoms)}, sort_keys=True
    ).encode()).hexdigest()[:16]
    return {
        "added_or_qualified": added,
        "gated": gated_claims,
        "model_disagreements": [{"study": d["study"], "observable": d["observable"],
                                 "arms": d["arms"], "spread": d["spread"]} for d in disagreements],
        "anomalies": [{"study": a["study"], "observable": a["observable"], "observed": a["observed"]}
                      for a in anoms],
        "regressions": regressions,
        "fingerprint": fp,
    }
