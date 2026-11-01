"""feedback_compiler — compile a *discovery_request* (+ its reopening-feedback YAML)
into engine-native authored artifacts that the ordinary discovery cycle then drives.

A **discovery_request** (``discovery_request.yaml``) is the execution envelope: which
investigation to reopen, which reopening-feedback file carries the scientific content,
and the budgets / approval gates / stop conditions. A **reopening-feedback** file is a
scientist's instruction to reopen and redirect the investigation.

This module promotes what used to be a workspace-local prototype script into a first-class,
*reusable* engine capability. Two hard rules distinguish it from a prose importer:

1. **Strict schema.** The discovery_request and the executable subtrees of the feedback
   are validated against bundled JSON Schemas with ``additionalProperties: false`` — an
   unknown field is an error, never silently ignored.
2. **Never infer executable actions from prose.** Numeric discriminating bands,
   controls, and mechanisms are read from STRUCTURED fields the scientist authored
   (``competing_hypotheses[].predictions``, ``nonnegotiable_controls``,
   ``new_composite_model.modules``). Where a study has no structured ≥2-way disjoint
   prediction coverage, the compiler emits a typed ``author_predictions`` action rather
   than fabricating a band from the prose ``decision_rule``.

Public API:
    compile_feedback(discovery_request, feedback=None, *, base_dir=None) -> CompiledRequest
    reconcile_and_reopen(compiled, inv_spec, studies) -> dict
    ingest_feedback(request_path, ws_root, *, write=False) -> dict   # CLI orchestration

``compile_feedback`` is a pure projection (validate → typed actions); ``reconcile_and_reopen``
applies those actions to a deep-copied investigation spec, preserving the prior closure /
verdict in ``lifecycle_history`` (never silently dropped) and staying idempotent.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA_DIR = Path(__file__).parent / "schemas"
_OPS = {">", ">=", "<", "<=", "==", "range"}


class FeedbackValidationError(ValueError):
    """A discovery_request / reopening-feedback file violated the strict schema."""


# ── small helpers ────────────────────────────────────────────────────────────
def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _slug(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")


def _as_list(v: Any) -> list:
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def _schema(name: str) -> dict:
    return json.loads((_SCHEMA_DIR / name).read_text(encoding="utf-8"))


def _jsonschema_validate(doc: dict, schema: dict, what: str) -> None:
    import jsonschema  # bundled dep (same one discover._validate_extensions uses)
    try:
        jsonschema.validate(json.loads(json.dumps(doc, default=str)), schema)
    except jsonschema.ValidationError as e:  # pragma: no cover - message formatting
        loc = "/".join(str(p) for p in e.absolute_path) or "(root)"
        raise FeedbackValidationError(f"{what}: {e.message} (at {loc})") from None


# ── prediction bands: parse, canonicalize, disjointness ──────────────────────
def _band_from_prediction(pred: dict, where: str) -> dict:
    """A feedback prediction -> a canonical band dict: ``{op, value}`` or ``{low, high}``.

    Strict: exactly one coherent form. ``op: range`` needs ``low``/``high``; any other op
    needs ``value``; mixing them or omitting the operand is an error (not a prose guess)."""
    op = pred.get("op")
    has_val = isinstance(pred.get("value"), (int, float)) and not isinstance(pred.get("value"), bool)
    has_lh = all(isinstance(pred.get(k), (int, float)) and not isinstance(pred.get(k), bool)
                 for k in ("low", "high"))
    if op == "range" or (op is None and has_lh and not has_val):
        if not has_lh:
            raise FeedbackValidationError(f"{where}: a range prediction needs numeric low and high")
        return {"low": float(pred["low"]), "high": float(pred["high"])}
    if op in _OPS and op != "range":
        if not has_val:
            raise FeedbackValidationError(f"{where}: prediction op {op!r} needs a numeric value")
        return {"op": op, "value": float(pred["value"])}
    raise FeedbackValidationError(
        f"{where}: prediction must carry either (op in >,>=,<,<=,== with value) or "
        f"(op: range with low/high); got op={op!r}")


def _interval(band: dict) -> tuple[float, bool, float, bool]:
    """(lo, lo_inclusive, hi, hi_inclusive). ±inf for open ends."""
    if "low" in band:
        return (band["low"], True, band["high"], True)
    op, v = band["op"], band["value"]
    return {
        ">":  (v, False, math.inf, False),
        ">=": (v, True, math.inf, False),
        "<":  (-math.inf, False, v, False),
        "<=": (-math.inf, False, v, True),
        "==": (v, True, v, True),
    }[op]


def _bands_disjoint(b1: dict, b2: dict) -> bool:
    """True iff no real value satisfies both bands (the honest discrimination test)."""
    a_lo, a_lc, a_hi, a_hc = _interval(b1)
    b_lo, b_lc, b_hi, b_hc = _interval(b2)
    left = (a_lo < b_hi) or (a_lo == b_hi and a_lc and b_hc)
    right = (b_lo < a_hi) or (b_lo == a_hi and b_lc and a_hc)
    return not (left and right)


def _to_expected(band: dict) -> dict:
    """Canonical engine ``expected`` (what hypotheses.discriminating_hypotheses reads)."""
    return dict(band)


# ── action builders ──────────────────────────────────────────────────────────
def _hash8(payload: Any) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:8]


def _parent_composite(studies: dict, reuse: list) -> str | None:
    """First-fidelity arm: the baseline composite path of a named reused parent study."""
    for slug in reuse or []:
        spec = studies.get(slug)
        if not isinstance(spec, dict):
            continue
        base = spec.get("baseline")
        if isinstance(base, list) and base and isinstance(base[0], dict):
            if base[0].get("composite"):
                return base[0]["composite"]
        elif isinstance(base, dict) and base.get("composite"):
            return base["composite"]
    return None


def compile_feedback(discovery_request: dict, feedback: dict | None = None, *,
                     base_dir: Any = None) -> dict:
    """Validate a discovery_request (+ its feedback) and project it to typed actions.

    ``discovery_request`` is the envelope dict (``{"discovery_request": {...}}`` or the
    inner mapping). ``feedback`` may be passed directly (pure); if omitted, it is loaded
    from ``discovery_request.feedback_file`` resolved against ``base_dir``. Returns a
    CompiledRequest: ``{discovery_request, feedback, actions, summary}``. Raises
    ``FeedbackValidationError`` on any schema violation (unknown field, bad band, …).
    """
    # accept either the wrapped or inner form, then validate the wrapped form strictly
    wrapped = discovery_request if "discovery_request" in discovery_request else {"discovery_request": discovery_request}
    _jsonschema_validate(wrapped, _schema("discovery-request.schema.json"), "discovery_request")
    dr = wrapped["discovery_request"]

    if feedback is None:
        if base_dir is None:
            raise FeedbackValidationError("compile_feedback: feedback not provided and no base_dir to resolve feedback_file")
        fb_path = Path(base_dir) / dr["feedback_file"]
        if not fb_path.is_file():
            raise FeedbackValidationError(f"feedback_file not found: {fb_path}")
        import yaml
        feedback = yaml.safe_load(fb_path.read_text(encoding="utf-8"))
    if not isinstance(feedback, dict):
        raise FeedbackValidationError("feedback: expected a top-level mapping")
    _jsonschema_validate(feedback, _schema("reopening-feedback.schema.json"), "feedback")

    inv_id = dr["investigation_id"]
    challenge = feedback.get("scientific_challenge") or {}
    next_cycle = feedback.get("next_cycle") or {}
    following = feedback.get("following_cycle") or {}
    instr = feedback.get("cycle_instructions") or {}
    sr = feedback.get("status_reconciliation") or {}
    lifecycle = dr.get("lifecycle") or {}

    actions: list[dict] = []

    # 1. reopen (lifecycle)
    actions.append({
        "type": "reopen",
        "status": sr.get("requested_state", "active"),
        "verdict_status": "open",
        "preserve_history": bool(lifecycle.get("preserve_history", True)),
        "reason": lifecycle.get("reason"),
    })

    # 2. objective / central gap / mandate
    actions.append({
        "type": "register_objective",
        "challenge_id": challenge.get("id"),
        "objective": challenge.get("question"),
        "central_gap": challenge.get("central_gap"),
        "mandate": challenge.get("mandate"),
    })

    # 3. competing hypotheses (global, canonical predictions). Build an observable->bands
    #    index so study seeding can detect ≥2-way disjoint coverage.
    hyps = challenge.get("competing_hypotheses") or []
    hyp_ids = [h.get("id") for h in hyps if isinstance(h, dict) and h.get("id")]
    obs_bands: dict[str, list[tuple[str, dict]]] = {}   # observable -> [(hyp_id, band)]
    for h in hyps:
        hid = h["id"]
        canon_preds = []
        for i, p in enumerate(_as_list(h.get("predictions"))):
            where = f"competing_hypotheses[{hid}].predictions[{i}]"
            band = _band_from_prediction(p, where)
            obs = p["observable"].strip()
            canon = {"observable": obs, "expected": _to_expected(band)}
            if p.get("regime"):
                canon["when"] = {"regime": p["regime"]}
            canon_preds.append(canon)
            obs_bands.setdefault(obs, []).append((hid, band))
        actions.append({
            "type": "register_hypothesis",
            "hypothesis": {
                "id": hid,
                "statement": h.get("mechanism"),
                "competing_with": [o for o in hyp_ids if o != hid],
                "predictions": canon_preds,
                "status": "open",
            },
        })

    # cross-check: a prediction's observable must be measured by some design study
    measured = {m for d in next_cycle.get("design") or [] if isinstance(d, dict)
                for m in _as_list(d.get("measurements"))}
    for obs in obs_bands:
        if obs not in measured:
            raise FeedbackValidationError(
                f"prediction observable {obs!r} is not in any next_cycle.design[].measurements")

    # 4. controls + bridge-composite mechanisms -> required_mechanisms (never hardcoded)
    comp = following.get("new_composite_model") or {}
    controls = [str(c) for c in _as_list(challenge.get("nonnegotiable_controls"))]
    bridge_mechs = [str(m) for m in _as_list(comp.get("modules"))]
    for mech in controls + bridge_mechs:
        actions.append({"type": "require_mechanism", "mechanism": mech})

    # 5. seed discriminating studies (data-driven)
    for design in next_cycle.get("design") or []:
        if not isinstance(design, dict):
            continue
        actions.append(_seed_study_action(design, inv_id, challenge, controls, obs_bands, hyps))

    # 6. reconciliation log
    for d in feedback.get("report_diagnostics") or []:
        if isinstance(d, dict):
            actions.append({"type": "record_reconciliation", "item": {
                "id": d.get("id"), "severity": d.get("severity"),
                "observation": d.get("observation"), "request": d.get("request"),
                "status": "open"}})

    # 7. bridge composite proposal
    if comp:
        actions.append({"type": "propose_composite", "composite": {
            "name": comp.get("name"), "architecture": comp.get("architecture"),
            "modules": bridge_mechs, "coupling_contract": comp.get("coupling_contract"),
            "status": "proposed"}})

    # 8. cycle flags
    actions.append({"type": "set_cycle_flags", "autonomy": instr.get("autonomy", "supervised")})

    summary = _summarize_actions(actions, inv_id, comp)
    return {"discovery_request": dr, "feedback": feedback, "actions": actions, "summary": summary}


def _seed_study_action(design: dict, inv_id: str, challenge: dict, controls: list,
                       obs_bands: dict, hyps: list) -> dict:
    """Build a seed_study action: a run-ready-shaped study spec + its acceptance behaviors.

    Discriminating behavior tests come ONLY from structured ≥2-way disjoint prediction
    coverage; a measurement without it becomes a preregistered secondary measure (no
    fabricated pass_if), and the study is flagged for ``author_predictions``.
    """
    slug = design["id"]
    measurements = [str(m) for m in _as_list(design.get("measurements"))]
    extend = [str(m) for m in _as_list(design.get("extend_with"))]
    reuse = [str(s) for s in _as_list(design.get("reuse"))]
    primary_hyp = design.get("primary_hypothesis")

    btests: list[dict] = []
    acceptance: list[str] = []
    discriminating_obs: list[str] = []
    for m in measurements:
        bands = obs_bands.get(m, [])
        disjoint = any(_bands_disjoint(bands[i][1], bands[j][1])
                       for i in range(len(bands)) for j in range(i + 1, len(bands)))
        if len(bands) >= 2 and disjoint:
            discriminating_obs.append(m)
            # pass_if = the band of the study's primary hypothesis (if it predicts m),
            # else the first competing hypothesis (author order) that predicts m.
            band = next((b for hid, b in bands if hid == primary_hyp), None) or bands[0][1]
            name = f"{slug}-discriminates-{_slug(m)}"
            btests.append({"name": name, "classification": "primary",
                           "description": design.get("decision_rule") or "",
                           "measure": m, "pass_if": dict(band)})
            acceptance.append(name)
        else:
            btests.append({"name": f"{slug}-{_slug(m)}", "classification": "secondary",
                           "description": f"Preregistered measurement {m!r} (bands to be authored).",
                           "measure": m})

    spec = {
        "schema_version": 4,
        "name": slug,
        "title": design.get("question") or slug,
        "investigation": inv_id,
        "question": design.get("question"),
        "claim": None,  # hypothesis-neutral until evidence decides
        "concept": challenge.get("id") or "",
        "topic": inv_id,
        "status": "planned",
        "design_status": "designed",
        "simulation_status": "not_started",
        "evaluation_status": "not_started",
        "gate_status": "not_started",
        "created": _now(),
        "parent_studies": [{"study": s, "relation": "builds-on"} for s in reuse],
        "required_mechanisms": extend + controls,
        "variants": [],
        "observables": measurements,
        "behavior_tests": btests,
        "visualizations": [],
        "findings": [],
        "runs": [],
        "tags": ["bridge", "discriminating", "reopened-by-feedback"],
    }
    return {"type": "seed_study", "slug": slug, "spec": spec,
            "acceptance": acceptance, "reuse": reuse,
            "author_predictions": [m for m in measurements if m not in discriminating_obs]
            if not discriminating_obs else []}


def _summarize_actions(actions: list, inv_id: str, comp: dict) -> dict:
    by: dict[str, int] = {}
    for a in actions:
        by[a["type"]] = by.get(a["type"], 0) + 1
    author = [a for a in actions if a["type"] == "seed_study" and a.get("author_predictions")]
    return {
        "investigation": inv_id,
        "actions": by,
        "new_studies": [a["slug"] for a in actions if a["type"] == "seed_study"],
        "hypotheses": [a["hypothesis"]["id"] for a in actions if a["type"] == "register_hypothesis"],
        "required_mechanisms": sorted({a["mechanism"] for a in actions if a["type"] == "require_mechanism"}),
        "bridge_composite": comp.get("name"),
        "author_predictions_needed": {a["slug"]: a["author_predictions"] for a in author},
    }


def reconcile_and_reopen(compiled: dict, inv_spec: dict, studies: dict) -> dict:
    """Apply a CompiledRequest's typed actions to a deep copy of ``inv_spec``.

    Preserves the prior closure / verdict status in ``lifecycle_history`` (unless the
    request set ``preserve_history: false``). Idempotent: hypotheses, studies, mechanisms,
    acceptance criteria and reconciliation items are keyed and never duplicated. Returns
    ``{inv_spec, new_studies, audit, summary, actions}``.
    """
    actions = compiled["actions"]
    feedback = compiled.get("feedback") or {}
    inv = copy.deepcopy(inv_spec)

    # parent-composite resolution needs the (pre-existing) studies
    merged_studies = dict(studies or {})

    audit = None
    new_studies: dict[str, dict] = {}
    for a in actions:
        t = a["type"]
        if t == "reopen":
            prior_status = inv.get("status")
            prior_verdict = (inv.get("executive") or {}).get("verdict_status")
            if a.get("preserve_history", True):
                audit = {
                    "at": _now(), "event": "reopened_by_feedback",
                    "feedback_intent": (feedback.get("target") or {}).get("intent"),
                    "challenge_id": (feedback.get("scientific_challenge") or {}).get("id"),
                    "reason": a.get("reason"),
                    "prior_status": prior_status, "prior_verdict_status": prior_verdict,
                    "rationale": str(feedback.get("human_feedback_to_engine") or "").strip()[:400],
                }
                hist = inv.setdefault("lifecycle_history", [])
                key = (audit["event"], audit["challenge_id"])
                if not any((h.get("event"), h.get("challenge_id")) == key for h in hist if isinstance(h, dict)):
                    hist.append(audit)
            inv["status"] = a["status"]
            ex = dict(inv.get("executive") or {})
            ex["verdict_status"] = a["verdict_status"]
            inv["executive"] = ex
        elif t == "register_objective":
            ex = dict(inv.get("executive") or {})
            if a.get("objective") is not None:
                ex["objective"] = a["objective"]
            if a.get("central_gap") is not None:
                ex["central_gap"] = a["central_gap"]
            if a.get("mandate") is not None:
                ex["mandate"] = a["mandate"]
            inv["executive"] = ex
        elif t == "register_hypothesis":
            hyps = list(inv.get("hypotheses") or [])
            ids = {h.get("id") for h in hyps if isinstance(h, dict)}
            if a["hypothesis"]["id"] not in ids:
                hyps.append(a["hypothesis"])
            inv["hypotheses"] = hyps
        elif t == "require_mechanism":
            mechs = list(inv.get("required_mechanisms") or [])
            if a["mechanism"] not in mechs:
                mechs.append(a["mechanism"])
            inv["required_mechanisms"] = mechs
        elif t == "seed_study":
            slug = a["slug"]
            spec = copy.deepcopy(a["spec"])
            # bind first-fidelity baseline from a reused parent composite, if any
            parent_comp = _parent_composite(merged_studies, a.get("reuse") or [])
            spec["baseline"] = ([{
                "name": "baseline", "composite": parent_comp, "params": {},
                "note": "First-fidelity arm reusing the parent composite named in the feedback's "
                        "`reuse`; the coupled bridge composite is the next-fidelity arm "
                        "(a capability gap to build)."}] if parent_comp else [])
            if slug not in merged_studies:
                new_studies[slug] = spec
                merged_studies[slug] = spec
            sl = list(inv.get("studies") or [])
            if slug not in sl:
                sl.append(slug)
            inv["studies"] = sl
            acs = list(inv.get("acceptance_criteria") or [])
            keys = {(c.get("study"), c.get("behavior")) for c in acs if isinstance(c, dict)}
            for beh in a.get("acceptance") or []:
                if (slug, beh) not in keys:
                    acs.append({"study": slug, "behavior": beh})
                    keys.add((slug, beh))
            inv["acceptance_criteria"] = acs
        elif t == "record_reconciliation":
            log = list(inv.get("reconciliation_log") or [])
            ids = {r.get("id") for r in log if isinstance(r, dict)}
            if a["item"].get("id") not in ids:
                log.append(a["item"])
            inv["reconciliation_log"] = log
        elif t == "propose_composite":
            inv["bridge_composite"] = a["composite"]
        elif t == "set_cycle_flags":
            cf = dict(inv.get("cycle_flags") or {})
            cf["autonomy"] = a["autonomy"]
            cf["reopened"] = True
            inv["cycle_flags"] = cf

    summary = dict(compiled["summary"])
    summary["reopened"] = True
    summary["new_studies"] = list(new_studies)
    return {"inv_spec": inv, "new_studies": new_studies, "audit": audit,
            "summary": summary, "actions": actions}


def ingest_feedback(request_path: Any, ws_root: Any, *, write: bool = False) -> dict:
    """CLI orchestration: load a discovery_request, compile it (+ its feedback), reconcile
    against the on-disk investigation, and optionally write the mutations.

    ``request_path`` points at a ``discovery_request.yaml``; its ``feedback_file`` is
    resolved relative to that file's directory. Returns the reconcile result plus a
    ``wrote`` flag. Never merges/commits — writes YAML in place when ``write`` is set.
    """
    import yaml
    from . import discover as _disc

    request_path = Path(request_path)
    raw = yaml.safe_load(request_path.read_text(encoding="utf-8")) or {}
    compiled = compile_feedback(raw, base_dir=request_path.parent)
    dr = compiled["discovery_request"]
    inv_id = dr["investigation_id"]

    inv_path, inv_spec, studies = _disc.load_cycle_inputs(str(ws_root), inv_id)
    result = reconcile_and_reopen(compiled, inv_spec, studies)

    result["wrote"] = False
    if write:
        from .study_io import save_yaml_atomic
        save_yaml_atomic(Path(inv_path), result["inv_spec"])
        sdir_base = _disc.studies_dir(Path(ws_root))
        for slug, spec in result["new_studies"].items():
            sdir = sdir_base / slug
            sdir.mkdir(parents=True, exist_ok=True)
            save_yaml_atomic(sdir / "study.yaml", spec)
        result["wrote"] = True
    return result
