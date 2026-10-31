"""Model-revision proposals: a measured disagreement / anomaly / contested result
becomes a typed, versioned ``ModelRevisionProposal`` (decision spec §3.4, §4).

Pure: proposes, never acts, never mutates its inputs, no I/O. The engine fills only
what it can derive (``param`` sweeps, higher-fidelity ``composite`` swaps); a
``mechanism`` change is always ``{kind, needs_author: true, question}`` and
``statement``/``scientific_judgment`` are never written.

Triggers (§4): ``model_disagreement``, ``anomaly``, ``contested``. A ``contradicted``
finding is an evidence problem (REPAIR), so it yields NO proposal.
"""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .evaluation import _arm_values, _keyed_specs, model_disagreement_map
from .hypotheses import _fidelity_rank, _finding_measure_observed, anomalies
from .study_refinement import _default_sweep

_TERMINAL = ("rejected", "applied", "evaluated")
# Cap ordering (1 proposal/study/cycle): explicit kind priority first (a measured model
# disagreement > an unanticipated anomaly > a contested tally), then magnitude WITHIN the
# kind (spread for disagreement, max |net| for contested; anomalies tie at 0, id breaks it).
# Raw scores are never compared across kinds - their scales are incomparable.
_KIND_ORDER = {"model_disagreement": 0, "anomaly": 1, "contested": 2}


def _as_list(v: Any) -> list:
    return v if isinstance(v, list) else ([] if v is None else [v])


def _findings(spec: dict) -> list[dict]:
    return [f for f in _as_list((spec or {}).get("findings")) if isinstance(f, dict)]


def _fid(f: dict) -> Any:
    return f.get("id") or f.get("name")


def _ev(f: dict) -> dict:
    return f.get("evidence") if isinstance(f.get("evidence"), dict) else {}


def _contradicted_studies(evidence_state: dict | None, evaluation: dict) -> set:
    """Studies with a contradicted result (evidence problem -> REPAIR, never a model revision):
    via a contradicted ledger finding, by_study status, or the evaluation status."""
    es = evidence_state or {}
    bad = {r.get("study") for r in es.get("ledger", [])
           if isinstance(r, dict) and r.get("status") == "contradicted"}
    bad |= {k for k, v in (es.get("by_study") or {}).items()
            if isinstance(v, dict) and v.get("status") == "contradicted"}
    bad |= {k for k, v in evaluation.items() if isinstance(v, dict) and v.get("status") == "contradicted"}
    return bad


def _affected_mv(study: str, evaluation: dict, model_versions: list) -> str | None:
    """The mv under question: the evaluation's favored mv, else the study's baseline-bound mv."""
    mv = (evaluation.get(study) or {}).get("model_version")
    if mv:
        return mv
    bound = []
    for m in model_versions or []:
        for b in _as_list(m.get("bound_to")):
            if isinstance(b, dict) and b.get("study") == study:
                bound.append((0 if str(b.get("arm", "")).startswith("baseline") else 1, m["id"]))
    return min(bound)[1] if bound else None


def _mv_composite(mv_id: str | None, model_versions: list) -> str | None:
    return next((m.get("composite") for m in model_versions or [] if m.get("id") == mv_id), None)


def _cat_rank(entry: dict) -> int:
    return max([_fidelity_rank(f) for f in _as_list(entry.get("fidelities")) + [entry.get("fidelity")]] or [0])


def _composite_candidate(composite: str | None, observable: str | None, catalog: list) -> dict | None:
    """A resolving catalog entry of strictly higher fidelity serving the same capability."""
    cur = next((c for c in catalog or [] if c.get("composite") == composite), None)
    cur_rank = _cat_rank(cur) if cur else 0
    cur_cap = set(cur.get("capability", [])) if cur else set()
    best = None
    for c in catalog or []:
        if c.get("composite") == composite or not c.get("resolves"):
            continue
        if _cat_rank(c) <= cur_rank:
            continue
        cap = set(c.get("capability", []))
        same = (observable in _as_list(c.get("observables"))) or (
            cur_cap and len(cur_cap & cap) / len(cur_cap) >= 0.5)
        if same and (best is None or (_cat_rank(c), str(c.get("id"))) > (_cat_rank(best), str(best.get("id")))):
            best = c
    return ({"kind": "composite", "replace_with": best["composite"],
             "derived_by": "catalog higher fidelity_rank"} if best else None)


def _param_candidate(spec: dict) -> dict | None:
    name, values = _default_sweep(spec or {})
    if not name or not values:
        return None
    base = next((a for a in _as_list(spec.get("baseline")) if isinstance(a, dict)), {})
    return {"kind": "param", "name": name, "from": (base.get("params") or {}).get(name),
            "to": list(values), "derived_by": "refine_study._default_sweep"}


def _test_for(spec: dict, finding: dict | None, observable: str | None) -> dict | None:
    name = _ev(finding).get("from_test") if finding else None
    if not name:
        for f in _findings(spec):
            if _finding_measure_observed(f)[0] == observable and _ev(f).get("from_test"):
                name = _ev(f)["from_test"]
                break
    if not name:
        return None
    t = next((t for t in _as_list(spec.get("behavior_tests"))
              if isinstance(t, dict) and t.get("name") == name), None)
    return {"behavior_test": name, "pass_if": copy.deepcopy((t or {}).get("pass_if"))}


def _hash8(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:8]


def _build(kind, study, finding, observable, mv, inadequacy, values, score, candidates,
           effects, spec, now) -> dict:
    fid = _fid(finding) if finding else None
    h = _hash8({"kind": kind, "study": study, "finding": fid, "observable": observable,
                "model_version": mv, "values": values})
    return {
        "id": f"rp:{study}:{mv}:{h}", "status": "proposed",
        "source": {"kind": kind, "study": study, "finding": fid, "observable": observable},
        "affected_model_version": mv, "inadequacy": inadequacy,
        "candidate_changes": [c for c in candidates if c],
        "predicted_effects": effects,
        "discriminating_test": _test_for(spec, finding, observable),
        "approval": "review",
        "history": [{"at": now, "event": "proposed", "by": "engine"}],
        "evaluated": None,
        "_score": score,
    }


def propose_model_revisions(inv_spec: dict, studies: dict, evaluation: dict,
                            evidence_state: dict | None, model_versions: list,
                            catalog: list, *, now: str | None = None) -> list[dict]:
    """One proposal per trigger, capped at 1 per study per cycle (highest score first).
    ``inv_spec['revision_proposals']`` ids with a terminal status are never re-emitted
    (the id is content-addressed, so they return only if the source values change)."""
    inv_spec = inv_spec or {}
    studies = studies or {}
    evaluation = evaluation or {}
    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    bad = _contradicted_studies(evidence_state, evaluation)
    out: list[dict] = []

    def _find(study, pred):
        return next((f for f in _findings(studies.get(study) or {}) if pred(f)), None)

    for row in model_disagreement_map(inv_spec, studies):
        if not row.get("disagreement"):
            continue
        study, obs = row["study"], row.get("observable")
        spec = studies.get(study) or {}
        f = _find(study, lambda f: _arm_values(f) == row["arms"]
                  and (_finding_measure_observed(f)[0] == obs or obs is None))
        if study in bad:
            continue
        mv = _affected_mv(study, evaluation, model_versions)
        arms = ", ".join(f"{k}={v}" for k, v in sorted(row["arms"].items()))
        out.append(_build(
            "model_disagreement", study, f, obs, mv,
            f"arms {{{arms}}} disagree (spread {row['spread']}) on {obs}", row["arms"], row["spread"],
            [_composite_candidate(_mv_composite(mv, model_versions), obs, catalog), _param_candidate(spec)],
            [{"observable": obs, "direction": "converge",
              "rationale": "if the arm gap is a formalism artefact, raising fidelity collapses the spread"}],
            spec, now))

    for a in anomalies(inv_spec, _keyed_specs(studies)):
        study, obs = a["study"], a["observable"]
        spec = studies.get(study) or {}
        f = _find(study, lambda f: _finding_measure_observed(f)[0] == obs
                  and _finding_measure_observed(f)[1] is not None)
        if study in bad:
            continue
        mv = _affected_mv(study, evaluation, model_versions)
        out.append(_build(
            "anomaly", study, f, obs, mv,
            f"observed {obs} = {a['observed']} lies outside every live hypothesis band",
            {"observed": a["observed"]}, 0.0,
            [_param_candidate(spec),
             {"kind": "mechanism", "add": None, "needs_author": True, "question": a["proposed_question"]}],
            [{"observable": obs, "direction": "enter_predicted_band",
              "rationale": "a parameter change or new mechanism should bring the observable into a predicted band"}],
            spec, now))

    for study, cmp in evaluation.items():
        if not isinstance(cmp, dict) or cmp.get("status") != "contested":
            continue
        spec = studies.get(study) or {}
        obs = cmp.get("observable")
        hyps = cmp.get("hypotheses") or []
        nets = {h.get("id"): (h.get("support") or {}).get("net") for h in hyps}
        f = _find(study, lambda f: _finding_measure_observed(f)[0] == obs) if obs else None
        if study in bad:
            continue
        mv = _affected_mv(study, evaluation, model_versions)
        out.append(_build(
            "contested", study, f, obs, mv,
            f"hypothesis support is contested under {mv} (net by hypothesis: {nets})", nets,
            max([abs(n) for n in nets.values() if isinstance(n, (int, float))] or [0]),
            [_param_candidate(spec)],
            [{"observable": obs, "direction": "discriminate",
              "rationale": "a parameter sweep should separate the competing hypotheses' predictions"}],
            spec, now))

    prior = {p.get("id"): p.get("status") for p in _as_list(inv_spec.get("revision_proposals"))
             if isinstance(p, dict)}
    out = [p for p in out if prior.get(p["id"]) not in _TERMINAL]
    # (see _KIND_ORDER for the cap rule)
    out.sort(key=lambda p: (_KIND_ORDER[p["source"]["kind"]], -p["_score"], p["id"]))
    seen: set = set()
    final = []
    for p in out:
        if p["source"]["study"] in seen:
            continue
        seen.add(p["source"]["study"])
        final.append(p)
    for p in final:
        del p["_score"]
    return final
