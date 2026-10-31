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


from .hypotheses import (
    _as_list, _RETIRED_STATUS, _to_float, _finding_measure_observed, anomalies,
    discriminating_hypotheses, fidelity_flags, rollup_support, support_summary,
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


def _keyed_specs(studies) -> list[dict]:
    """Study specs with their identity resolved to the ``inv_spec["studies"]`` key.

    ``hypothesis.study`` names the investigation's study slug (the key), which can
    differ from the spec's own ``name``/``slug``. Copy each keyed spec with ``name``
    set to its key so every downstream join (support log, fidelity flags, anomalies)
    uses the slug. A plain list keeps each spec's ``name``/``slug`` (fallback). Pure."""
    if isinstance(studies, dict):
        return [({**spec, "name": key} if isinstance(spec, dict) else spec)
                for key, spec in studies.items()]
    return list(studies or [])


def _has_quantitative_support(support_log: list[dict]) -> bool:
    """True iff >=1 ``supports`` entry came from the quantitative ``score_support``
    path (those carry ``discriminating``), not the prose ``alternate_hypotheses`` fold."""
    return any((e or {}).get("delta") == "supports" and (e or {}).get("discriminating")
               for e in support_log or [])


def evaluate_claim_support(summary: dict, evidence_status: str | None, *,
                           has_quantitative: bool) -> str:
    """Map a hypothesis's support tally + its study's evidence integrity to a claim
    verdict. ``supported`` needs net>0 AND VERIFIED evidence AND quantitative
    (run-measured) support; prose-only or unverified net support is
    ``provisionally-supported``. Net-negative is ``contested``; zero is ``unresolved``."""
    net = (summary or {}).get("net", 0)
    if net > 0:
        return (SUPPORTED if evidence_status == VERIFIED and has_quantitative
                else PROVISIONAL_SUPPORT)
    if net < 0:
        return CONTESTED
    return UNRESOLVED


def _favored_model_version(inv_spec: dict, specs: list, slug: str, hyp_id) -> str | None:
    """The model version of the finding backing the favored hypothesis: a finding of
    the study measuring one of the hypothesis's predicted observables, read from
    ``provenance.model_version``. ``None`` when none is resolvable. Pure."""
    hyp = next((h for h in _as_list((inv_spec or {}).get("hypotheses"))
                if isinstance(h, dict) and h.get("id") == hyp_id), None)
    obs = {p.get("observable") for p in _as_list((hyp or {}).get("predictions"))
           if isinstance(p, dict)}
    spec = next((s for s in specs if isinstance(s, dict)
                 and (s.get("name") or s.get("slug")) == slug), None)
    if not obs:
        return None  # prose-only hypothesis: no observable to join a finding on
    mvs: set[str] = set()
    for f in _as_list((spec or {}).get("findings")):
        if not isinstance(f, dict):
            continue
        ev = f.get("evidence") if isinstance(f.get("evidence"), dict) else {}
        prov = f.get("provenance") if isinstance(f.get("provenance"), dict) else {}
        mv = prov.get("model_version")
        if mv and (ev.get("measure") or ev.get("observable")) in obs:
            mvs.add(mv)
    # Exactly one distinct version, or ambiguous (multi-arm study): never guess.
    return next(iter(mvs)) if len(mvs) == 1 else None


def compare_hypotheses(inv_spec: dict, studies: dict, evidence_state: dict | None = None) -> dict:
    """Per study, the competing hypotheses with their support tally and claim verdict,
    plus the study's favored hypothesis (or that it is gated/unresolved). Reuses the
    rollup; folds in the Phase-A evidence status and the stage-4/6 fidelity caveats."""
    specs = _keyed_specs(studies)
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
            hq = _has_quantitative_support(h.get("support_log") or [])
            hyps.append({"id": h.get("id"), "support": s,
                         "verdict": evaluate_claim_support(s, ev_status, has_quantitative=hq),
                         "model_version": None})
        favored = max((h for h in hyps if h["support"]["net"] > 0),
                      key=lambda h: h["support"]["net"], default=None)
        mv = _favored_model_version(inv_spec, specs, slug, favored["id"]) if favored else None
        if favored:
            favored["model_version"] = mv
        note = None
        if slug in gated:
            note = "model-fidelity gated — a superseded or multi-arm result; see fidelity caveats"
        out[slug] = {
            "observable": (disc.get(slug) or {}).get("observable"),
            "hypotheses": hyps,
            "favored": favored["id"] if favored else None,
            "status": GATED if slug in gated else (favored["verdict"] if favored else UNRESOLVED),
            "model_version": mv,
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
    # An adjudicated supersession is resolved history — not a live competing arm.
    if (isinstance(sup, dict) and _to_float(sup.get("observed")) is not None
            and not (sup.get("adjudicated_by") or sup.get("adjudicated"))):
        arms[f"superseded:{sup.get('source', 'prior')}"[:40]] = _to_float(sup["observed"])
    return arms


def _finding_resolved(finding: dict) -> bool:
    """A finding is resolved — no longer a live disagreement — when it has a numeric
    observed AND any supersession has been adjudicated by a higher-fidelity run."""
    ev = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    _, observed = _finding_measure_observed(finding)
    sup = ev.get("superseded")
    sup_open = bool(sup) and not (isinstance(sup, dict) and (sup.get("adjudicated_by") or sup.get("adjudicated")))
    return observed is not None and not sup_open


def model_disagreement_map(inv_spec: dict, studies: dict) -> list[dict]:
    """Where models/arms disagree on the same observable within a study — surfaced as
    a scientific question to investigate, not averaged. Returns one entry per study
    whose recorded arms span a non-trivial range."""
    specs = _keyed_specs(studies)
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
            # A resolved finding (numeric observed + any supersession adjudicated) is
            # no longer a live disagreement, even if its regime arms span a range.
            live = spread >= 0.2 and not _finding_resolved(f)
            out.append({
                "study": slug, "observable": ev.get("measure") or ev.get("observable"),
                "arms": arms, "spread": round(spread, 4),
                "disagreement": live,
                "note": ("arms disagree — investigate the model disagreement, do not average"
                         if live else "resolved — regime-dependence recorded, no live model conflict"),
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


def discovery_delta(inv_spec: dict, studies: dict, evidence_state: dict | None = None,
                    prev: dict | None = None) -> dict:
    """The per-cycle DiscoveryDelta: what this cycle established, qualified, gated, or
    left anomalous, relative to a start-of-cycle state in which nothing was registered.
    Records added/weakened claims, model disagreements, anomalies and scientific
    regressions (a superseded over-claim is progress, recorded explicitly)."""
    specs = _keyed_specs(studies)
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

    fp = _fingerprint(cmp, len(anoms))
    prev = prev if isinstance(prev, dict) else None
    changed: list[dict] = []
    new_mvs: list[str] = []
    if prev is not None:
        pby = prev.get("by_study") or {}
        for slug in sorted(cmp):
            p = pby.get(slug) or {}
            frm = p.get("status") or "unassessed"
            to, mv_to = cmp[slug]["status"], cmp[slug].get("model_version")
            mv_from = p.get("model_version")
            if frm != to or mv_from != mv_to:
                changed.append({"study": slug, "from": frm, "to": to,
                                "model_version_from": mv_from, "model_version_to": mv_to})
        known = set(prev.get("model_versions") or [])
        cur = [c.get("model_version") for c in cmp.values() if c.get("model_version")]
        new_mvs = sorted({m for m in cur if m not in known})
    return {
        "added_or_qualified": added,
        "gated": gated_claims,
        "model_disagreements": [{"study": d["study"], "observable": d["observable"],
                                 "arms": d["arms"], "spread": d["spread"]} for d in disagreements],
        "anomalies": [{"study": a["study"], "observable": a["observable"], "observed": a["observed"]}
                      for a in anoms],
        "regressions": regressions,
        "fingerprint": fp,
        "prev_fingerprint": prev.get("fingerprint") if prev else None,
        "changed": changed,
        "new_model_versions": new_mvs,
        "proposals": {"opened": [], "applied": [], "evaluated": []},
        "unchanged": bool(prev is not None and prev.get("fingerprint") == fp
                          and not changed and not new_mvs),
    }


def _fingerprint(cmp: dict, n_anoms: int) -> str:
    import hashlib
    import json
    return hashlib.sha256(json.dumps(
        {k: cmp[k]["status"] for k in sorted(cmp)} | {"anoms": n_anoms}, sort_keys=True
    ).encode()).hexdigest()[:16]


def snapshot_evaluation(cmp: dict, evidence_state: dict | None, model_versions,
                        *, n_anomalies: int = 0) -> dict:
    """The §3.5 ScientificState snapshot persisted in ``cycle_state.evaluation_snapshot``:
    just what the next cycle needs to compute a change. Pure."""
    ev_by = (evidence_state or {}).get("by_study", {})
    by_study = {slug: {"status": c.get("status"), "favored": c.get("favored"),
                       "evidence": (ev_by.get(slug) or {}).get("status"),
                       "model_version": c.get("model_version")}
                for slug, c in sorted((cmp or {}).items())}
    ids = [m["id"] if isinstance(m, dict) else m for m in (model_versions or [])]
    return {"fingerprint": _fingerprint(cmp or {}, n_anomalies),
            "by_study": by_study, "model_versions": ids,
            "proposals": {"open": [], "applied": []}}
