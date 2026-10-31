"""Experiment design — Discovery Engine 2.0, Wave 1 (Cycle N+1 Phase C).

Turn a set of competing hypotheses into a *discriminating* study design — a typed
ExperimentDesign with the hypothesis contrast, the intervention arms, the controls,
a registered Prediction per hypothesis, and a committed decision rule — all
specified BEFORE the run. "Tests and outcome interpretations must be specified
before running."

Canonical contracts (compact core, per the upgrade plan):

- ``Prediction``       {id, hypothesis_id, intervention, observable, expected, falsifier}
- ``ExperimentDesign`` {id, study, hypothesis_contrast, interventions, controls,
                        measurements, predictions, decision_criterion, analysis_plan}

Derive-first: ``design_discriminating_study`` is a pure projection over the
investigation spec; it registers predictions and a decision rule but runs nothing.
"""
from __future__ import annotations


from .hypotheses import _as_list, discriminating_hypotheses, _RETIRED_STATUS

# A hypothesis whose *id* names a null / negative / failure arm — its intervention
# is a control, not a positive condition under test. Matched against the id only:
# a positive hypothesis may *mention* a control regime in its prose (e.g. "distant")
# without being one.
_CONTROL_MARKERS = ("null", "no-", "no_", "control", "catastrophe", "collapse",
                    "non-heritable", "non_heritable")


def _is_control_hyp(h: dict) -> bool:
    return any(m in str(h.get("id", "")).lower() for m in _CONTROL_MARKERS)


def register_prediction(hypothesis: dict, observable: str | None = None) -> list[dict]:
    """Register the falsifiable Prediction(s) a hypothesis makes: one per
    ``predictions[]`` entry, carrying the intervention (``when``) it is made under,
    the observable, and the expected band. Pure; the ``model_version`` is left
    explicit-None until a model is bound (Phase D)."""
    hid = hypothesis.get("id")
    out: list[dict] = []
    for i, p in enumerate(_as_list(hypothesis.get("predictions"))):
        if not isinstance(p, dict) or not p.get("observable"):
            continue
        if observable and p["observable"] != observable:
            continue
        out.append({
            "id": f"pred:{hid}:{i}",
            "hypothesis_id": hid,
            "intervention": p.get("when") or {},
            "observable": p["observable"],
            "expected": p.get("expected"),
            "model_version": None,
            "falsifier": f"{p['observable']} outside {p.get('expected')}"
                         + (f" under {p['when']}" if p.get("when") else ""),
        })
    return out


def commit_decision_rule(hypotheses: list[dict], observable: str) -> dict:
    """The committed interpretation, fixed before the run: how a measured value maps
    to a hypothesis verdict, and what counts as an anomaly."""
    arms = []
    for h in hypotheses:
        for p in _as_list(h.get("predictions")):
            if isinstance(p, dict) and p.get("observable") == observable:
                arms.append({"hypothesis": h.get("id"), "intervention": p.get("when") or {},
                             "supports_if": p.get("expected")})
    return {
        "observable": observable,
        "arms": arms,
        "rule": ("Under each intervention, the hypothesis whose expected band the measured "
                 f"{observable} satisfies is supported for that regime; a competing hypothesis "
                 "whose band is violated is weakened. A value outside EVERY predicted band is an "
                 "anomaly — neither hypothesis anticipated it; generate a new hypothesis."),
        "precommitted": True,
    }


def validate_controls(design: dict, required_controls: list[str] | None = None) -> list[str]:
    """Check a design carries genuine controls. Returns a list of issue strings
    (empty = ok). A discriminating design needs at least one control arm; named
    ``required_controls`` (e.g. 'no-exchange', 'passive-force') must each appear in
    an intervention or control label."""
    issues: list[str] = []
    if not design.get("controls"):
        issues.append("no control arm — a discriminating study needs a null / negative control")
    if len(design.get("interventions", [])) < 2:
        issues.append("fewer than two intervention arms — nothing to contrast")
    labels = " ".join(str(c) for c in design.get("controls", [])
                      + design.get("interventions", [])).lower()
    for req in required_controls or []:
        if req.lower().replace("-", "") not in labels.replace("-", "").replace("_", ""):
            issues.append(f"missing required control: {req}")
    return issues


def design_discriminating_study(inv_spec: dict, study_slug: str,
                                required_controls: list[str] | None = None) -> dict | None:
    """Produce an ExperimentDesign that discriminates the competing hypotheses on
    ``study_slug``. Returns ``None`` when the study carries no discriminable
    competing hypotheses. Pure; registers predictions + a committed decision rule."""
    inv_spec = inv_spec or {}
    disc = discriminating_hypotheses(inv_spec).get(study_slug)
    if not disc or not disc.get("discriminable"):
        return None
    observable = disc["observable"]
    hyps = [h for h in _as_list(inv_spec.get("hypotheses"))
            if isinstance(h, dict) and h.get("study") == study_slug
            and str(h.get("status", "open")).strip().lower() not in _RETIRED_STATUS]

    interventions, controls, seen = [], [], set()
    for h in hyps:
        for p in _as_list(h.get("predictions")):
            if not isinstance(p, dict) or p.get("observable") != observable:
                continue
            arm = {"hypothesis": h.get("id"), "when": p.get("when") or {}}
            key = (h.get("id"), tuple(sorted((p.get("when") or {}).items())))
            if key in seen:
                continue
            seen.add(key)
            interventions.append(arm)
            if _is_control_hyp(h):
                controls.append(arm)

    predictions: list[dict] = []
    for h in hyps:
        predictions.extend(register_prediction(h, observable))

    design = {
        "id": f"design:{study_slug}",
        "study": study_slug,
        "hypothesis_contrast": [h.get("id") for h in hyps],
        "interventions": interventions,
        "controls": controls,
        "measurements": [observable],
        "predictions": predictions,
        "decision_criterion": commit_decision_rule(hyps, observable),
        "analysis_plan": (f"Run each intervention arm (incl. controls) with replicate seeds; measure "
                          f"settled {observable}; apply the committed decision rule; report uncertainty "
                          "and preserve controls and negative results."),
    }
    design["control_issues"] = validate_controls(design, required_controls)
    return design


def materialize_design(design: dict, parent_spec: dict, inv_spec: dict,
                       model_version: dict | None = None) -> dict:
    """Turn a discriminating ``design`` into a child study spec. Pure (no I/O, inputs
    untouched); the caller writes it (``_write_seeded_study``).

    Baseline comes from ``build_seeded_study``; ``model_version`` (``{composite, params}``,
    e.g. from ``model_architect.model_version``) becomes its ``model_change``. One
    ``variants[]`` arm per intervention: when every ``when`` key is a parameter already on the
    parent's baseline, the conditions become ``params`` overrides; otherwise the arm is marked
    ``needs_author`` (a non-parameter condition such as a structure needs hand authoring).
    Control interventions are flagged ``control: true``. Child ``hypotheses[]`` carry the
    design's predictions and ``origin: {kind: design, ref: design id}``."""
    import copy
    from .study_seeding import build_seeded_study

    design, parent_spec, inv_spec = (copy.deepcopy(x) for x in (design, parent_spec, inv_spec))
    new_slug = f"{design.get('study') or 'study'}-discriminating"
    mc = None
    if model_version:
        mc = {k: model_version[k] for k in ("composite", "params") if model_version.get(k)}
    study = build_seeded_study(parent_spec, inv_spec, new_slug=new_slug, model_change=mc or None,
                               title=f"Discriminating design for {design.get('study')}")
    base = next((a for a in study.get("baseline") or [] if isinstance(a, dict)), {})
    base_params = base.get("params") or {}

    controls = [(c.get("hypothesis"), tuple(sorted((c.get("when") or {}).items())))
                for c in design.get("controls") or []]
    variants = []
    for i, iv in enumerate(design.get("interventions") or []):
        when = iv.get("when") or {}
        key = (iv.get("hypothesis"), tuple(sorted(when.items())))
        arm: dict = {"name": f"arm-{i + 1}-{iv.get('hypothesis')}", "base": "baseline",
                     "hypothesis": iv.get("hypothesis"), "when": dict(when)}
        if when and all(k in base_params for k in when):
            arm["params"] = dict(when)
        else:
            arm["needs_author"] = True
        if key in controls:
            arm["control"] = True
        variants.append(arm)
    study["variants"] = variants

    by_hyp: dict = {}
    for p in design.get("predictions") or []:
        by_hyp.setdefault(p.get("hypothesis_id"), []).append(p)
    study["hypotheses"] = [
        {"id": f"{hid}@{new_slug}", "study": new_slug, "status": "open", "predictions": preds,
         "origin": {"kind": "design", "ref": design.get("id")}}
        for hid, preds in by_hyp.items()]
    study["design"] = {"id": design.get("id"), "decision_criterion": design.get("decision_criterion")}
    return study
