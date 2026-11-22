"""R3 — experimental design from ranking to invention (§3).

Today the engine RANKS authored prediction regimes (``design_discriminating_study`` +
``rank_study_experiments``) and infers control status from hypothesis/arm NAMES
(``experiment_design._is_control_hyp`` matches id markers like ``null`` / ``control``). §3
upgrades both:

- **Explicit, typed arm roles** — ``treatment`` / ``negative_control`` / ``positive_control`` /
  ``sham`` / ``matched_baseline``, declared on the arm. Control status is *never* inferred from an
  arm's name ("do not infer control status from arm names").
- **An ``ExperimentSearchSpace``** (controllable interventions + ranges, observables, controls,
  replicates, model versions, costs, constraints) and a **perturbation-search** interface that
  *generates* candidate perturbations over it — the invention step that feeds the existing EIG
  scorer, instead of only ranking regimes a human already wrote.

Pure: everything here is deterministic structure + enumeration; no run is triggered.
"""
from __future__ import annotations

import dataclasses
import itertools

ARM_ROLES = ("treatment", "negative_control", "positive_control", "sham", "matched_baseline")
CONTROL_ROLES = ("negative_control", "positive_control", "sham", "matched_baseline")


def arm_role(arm) -> str | None:
    """The arm's DECLARED role (``arm['role']``), or None if undeclared or invalid. Never inferred
    from the arm's name — a name like ``no-exchange`` does not make an arm a control."""
    if not isinstance(arm, dict):
        return None
    r = arm.get("role")
    return r if r in ARM_ROLES else None


def is_control(arm) -> bool:
    """True only when the arm DECLARES a control role."""
    return arm_role(arm) in CONTROL_ROLES


def validate_arm_roles(arms, *, required_roles=None) -> list[str]:
    """Issues (empty = ok): every arm must declare a valid role; the design must carry at least one
    explicit control; and any ``required_roles`` must be present. Arm names are never consulted."""
    issues: list[str] = []
    for i, a in enumerate(arms or []):
        if arm_role(a) is None:
            label = a.get("name", i) if isinstance(a, dict) else i
            issues.append(f"arm {label!r} declares no valid role (one of {ARM_ROLES}); "
                          "control status is never inferred from the name")
    present = {arm_role(a) for a in (arms or []) if arm_role(a)}
    if not (present & set(CONTROL_ROLES)):
        issues.append("no arm declares a control role — a discriminating study needs an explicit control")
    for req in required_roles or []:
        if req not in present:
            issues.append(f"missing required arm role: {req}")
    return issues


@dataclasses.dataclass
class ExperimentSearchSpace:
    """The controllable space a perturbation search explores (§3): what can be varied, over what
    ranges, under what controls/constraints, at what cost."""
    interventions: list                                         # [{parameter, values:[...]} | {parameter, range:[lo,hi], steps:n}]
    observables: list = dataclasses.field(default_factory=list)
    controls: list = dataclasses.field(default_factory=list)    # [{role, ...}]
    model_versions: list = dataclasses.field(default_factory=list)
    replicates: int = 1
    constraints: list = dataclasses.field(default_factory=list)  # [{parameter, min?, max?, forbid:[...]}]
    cost: dict | None = None

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _axis_values(iv: dict) -> list:
    """The concrete values an intervention axis takes: an explicit ``values`` list, or a linear
    grid of ``steps`` points across a ``[lo, hi]`` ``range``."""
    if not isinstance(iv, dict):
        return []
    if iv.get("values") is not None:
        return list(iv["values"])
    rng, steps = iv.get("range"), iv.get("steps", 2)
    if isinstance(rng, (list, tuple)) and len(rng) == 2 and isinstance(steps, int) and steps >= 1:
        lo, hi = rng
        if steps == 1:
            return [lo]
        return [lo + (hi - lo) * k / (steps - 1) for k in range(steps)]
    return []


def _satisfies(point: dict, constraints: list) -> bool:
    for c in constraints or []:
        p = c.get("parameter")
        if p not in point:
            continue
        v = point[p]
        if "min" in c and v < c["min"]:
            return False
        if "max" in c and v > c["max"]:
            return False
        if v in (c.get("forbid") or []):
            return False
    return True


def enumerate_perturbations(space, *, limit: int | None = None) -> list[dict]:
    """Generate candidate perturbation points over the search space's interventions — the invention
    step the EIG scorer ranks, rather than only ranking authored regimes. Each point is a
    ``{parameter: value, …}`` dict that satisfies the constraints. Pure + deterministic (a grid over
    the declared values/ranges). An empty or degenerate space yields ``[]``."""
    if isinstance(space, ExperimentSearchSpace):
        ivs, constraints = space.interventions, space.constraints
    else:
        ivs = (space or {}).get("interventions") or []
        constraints = (space or {}).get("constraints") or []
    axes = [(iv["parameter"], _axis_values(iv)) for iv in ivs
            if isinstance(iv, dict) and iv.get("parameter") and _axis_values(iv)]
    if not axes:
        return []
    points: list[dict] = []
    for combo in itertools.product(*[vals for _, vals in axes]):
        point = {name: v for (name, _), v in zip(axes, combo)}
        if _satisfies(point, constraints):
            points.append(point)
            if limit is not None and len(points) >= limit:
                break
    return points


def _intervention_params(space) -> list:
    ivs = space.interventions if isinstance(space, ExperimentSearchSpace) \
        else (space or {}).get("interventions") or []
    return [iv["parameter"] for iv in ivs if isinstance(iv, dict) and iv.get("parameter")]


def search_discriminating_perturbation(hypotheses, search_space, *, predict,
                                       min_score: float = 0.5, limit: int | None = None) -> dict:
    """Invent a discriminating experiment (§3): enumerate candidate perturbations over
    ``search_space``, score how well each separates the competing ``hypotheses``' predictions with
    the existing EIG scorer (:func:`experiment_utility.best_experiment`), and return the
    most-separating regime — or a reasoned abstention WITH a concrete data/model requirement when
    none separates them.

    ``predict(hypothesis, perturbation)`` returns that hypothesis's prediction under the
    perturbation (a number, ``{value, uncertainty}``, or ``{low, high}`` band) — the model's job,
    injected so this stays pure. Returns ``best_experiment``'s shape augmented with
    ``method: 'perturbation-search'`` and ``n_candidates``; on abstention it adds a concrete
    ``requirement`` (§3 acceptance 2). An ``experiment`` outcome is the found divergent regime
    (acceptance 1)."""
    from discovery_engine.phases.experiment_utility import best_experiment

    perturbations = enumerate_perturbations(search_space, limit=limit)
    res = dict(best_experiment(hypotheses or [], perturbations, predict=predict, min_score=min_score))
    res["method"] = "perturbation-search"
    res["n_candidates"] = len(perturbations)
    if res.get("outcome") == "experiment":
        return res

    params = sorted(set(_intervention_params(search_space)))
    if not perturbations:
        res["requirement"] = ("the search space enumerated no candidate perturbations — declare "
                              "controllable interventions with explicit values or a [lo, hi] range")
    else:
        res["requirement"] = (
            f"no enumerated perturbation (of {len(perturbations)}) separates the hypotheses beyond "
            f"{min_score}; need an additional observable that distinguishes the mechanisms, a wider "
            f"range on {params or 'the interventions'}, or a model whose predictions respond to the "
            "controllable parameters")
    return res


def _as_list(v):
    if v is None:
        return []
    return list(v) if isinstance(v, (list, tuple)) else [v]


def design_by_invention(inv_spec, study_slug, search_space, *, predict,
                        min_score: float = 0.5, arms=None) -> dict:
    """Produce an ExperimentDesign by INVENTING a discriminating regime (§3) — for a study whose
    authored regimes do not discriminate. Enumerates + scores perturbations
    (:func:`search_discriminating_perturbation`); on an ``experiment`` outcome it builds a design
    under the invented regime with predictions from ``predict`` and controls taken from the
    **declared arm roles** (never name-inferred, unlike ``experiment_design._is_control_hyp``); on
    abstention it returns ``{design: None, abstention: {reason, requirement, n_candidates}}``.

    ``arms``: the study's arms (each ``{name, role, …}``) — their declared control roles populate the
    design's controls. ``predict(hypothesis, perturbation)`` is the injected model seam, so this
    stays pure. Returns ``{design, abstention}``."""
    hyps = [h for h in _as_list((inv_spec or {}).get("hypotheses"))
            if isinstance(h, dict) and h.get("study") == study_slug]
    found = search_discriminating_perturbation(hyps, search_space, predict=predict, min_score=min_score)
    if found.get("outcome") != "experiment":
        return {"design": None,
                "abstention": {"reason": found.get("reason"),
                               "requirement": found.get("requirement"),
                               "n_candidates": found.get("n_candidates")}}

    regime = found["condition"]
    controls = [a for a in (arms or []) if is_control(a)]
    role_issues = validate_arm_roles(arms) if arms else \
        ["no arms supplied — controls cannot be confirmed from declared roles"]
    design = {
        "id": f"design:{study_slug}:invented",
        "study": study_slug,
        "method": "perturbation-search",
        "invented_regime": dict(regime),
        "score": found["score"],
        "n_candidates": found.get("n_candidates"),
        "hypothesis_contrast": [h.get("id") for h in hyps],
        "interventions": [{"hypothesis": h.get("id"), "when": dict(regime)} for h in hyps],
        "controls": [{"name": a.get("name"), "role": arm_role(a)} for a in controls],
        "predictions": [{"hypothesis_id": h.get("id"), "intervention": dict(regime),
                         "predicted": predict(h, regime)} for h in hyps],
        "control_issues": role_issues,
        "analysis_plan": (f"Run every hypothesis arm under the INVENTED regime {regime} (plus the "
                          "declared controls); measure the observable; the regime was chosen to "
                          f"maximize prediction separation (score {found['score']:.3f})."),
    }
    return {"design": design, "abstention": None}
