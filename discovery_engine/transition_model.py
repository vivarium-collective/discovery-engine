"""Transition composition — Discovery Engine 2.0, Milestone 4 (Cycle N+2).

Compose the Cycle N+1 sub-models into a versioned Proto-Endosymbiosis Composite and
estimate a **conditional transition phase diagram**: which regime of the conditioning
parameters (spatial proximity, pump efficiency, heredity fidelity) yields which
transition outcome. The diagram is *conditional* because an upstream disagreement
(the s3 enclosure topology Phase F surfaced) is unresolved — the engine states the
condition rather than papering over it.

Derive-first: composition reuses the Phase-D ``model_architect`` catalog routing; the
phase diagram is a pure sweep of a declared, inspectable decision rule over the axes.
"""
from __future__ import annotations

import itertools
from typing import Any

from . import model_architect as _ma
from .hypotheses import _as_list

# Transition outcomes.
NO_TRANSITION = "no-transition"
TRANSIENT = "transient-association"
STABLE = "stable-endosymbiosis"
COLLAPSE = "collapse"

# The decision rule, derived from the Cycle N+1 claims and evaluated in order: the
# first rule whose `when` is a subset of the parameter point wins. Each rule names the
# member study whose claim justifies it, so the phase diagram is auditable.
TRANSITION_RULES: list[dict] = [
    {"when": {"proximity": "distant"}, "outcome": NO_TRANSITION,
     "because": "s2: exchange collapses with distance — no spatial contact, no partnership"},
    {"when": {"pump": "weak"}, "outcome": COLLAPSE,
     "because": "s4: a weak pump cannot sustain the host — energetic collapse"},
    {"when": {"heredity": "low"}, "outcome": TRANSIENT,
     "because": "s5: below the fidelity threshold the association is not transmitted — transient"},
    {"when": {}, "outcome": STABLE,
     "because": "s2+s4+s5: proximate, energetically positive, and heritable — the transition stabilizes"},
]

_DEFAULT_AXES = {
    "proximity": ["proximate", "distant"],
    "pump": ["efficient", "weak"],
    "heredity": ["high", "low"],
}

# The campaign mechanisms the composite must couple.
_TRANSITION_MECHANISMS = ["exchange", "diffusion", "membrane", "energy", "heredity"]


def compose_transition_model(studies: dict, parent_versions: list[str] | None = None) -> dict:
    """Compose the Proto-Endosymbiosis Composite: route every transition mechanism
    through the Phase-D catalog (reuse/wrap/build) and record an immutable ModelVersion
    whose ``parent_ids`` are the Cycle N+1 per-study model versions it is built from."""
    catalog = _ma.build_catalog(studies)
    req = _ma.ModelBuildRequest(question_id="N+2:proto-endosymbiosis",
                                required_mechanisms=_TRANSITION_MECHANISMS,
                                required_observables=["transition_outcome"])
    routes = _ma.choose_acquisition_route(req, catalog)
    mv = _ma.model_version(req, routes, parent_ids=parent_versions or [])
    mv["coupling_issues"] = _ma.validate_coupling(routes, catalog)
    mv["routes"] = routes
    return mv


def classify_transition_outcome(point: dict, rules: list[dict] | None = None) -> dict:
    """Classify one parameter point into a transition outcome by the ordered decision
    rule. Returns ``{outcome, because, rule}``; an unmatched point (impossible with the
    catch-all rule) is ``no-transition`` with a note."""
    for i, rule in enumerate(rules or TRANSITION_RULES):
        when = rule.get("when") or {}
        if all(point.get(k) == v for k, v in when.items()):
            return {"outcome": rule["outcome"], "because": rule.get("because"), "rule": i}
    return {"outcome": NO_TRANSITION, "because": "no rule matched", "rule": None}


def estimate_outcome_surface(axes: dict | None = None, rules: list[dict] | None = None) -> list[dict]:
    """Sweep the Cartesian product of the conditioning axes → a phase diagram: one
    ``{point, outcome, because}`` per cell. Pure; no simulation is run here (the cells
    are the classification of a would-be run, to be confirmed by executing the composite)."""
    axes = axes or _DEFAULT_AXES
    keys = list(axes)
    surface: list[dict] = []
    for combo in itertools.product(*(axes[k] for k in keys)):
        point = dict(zip(keys, combo))
        verdict = classify_transition_outcome(point, rules)
        surface.append({"point": point, "outcome": verdict["outcome"], "because": verdict["because"]})
    return surface


def conditioning_caveats(inv_spec: dict, studies: dict) -> list[str]:
    """Why the phase diagram is *conditional*: the unresolved upstream disagreements /
    gated studies whose resolution could move region boundaries. Derived from the Phase-F
    model-disagreement map, so the caveat tracks the evidence, not a hand-written note."""
    from .evaluation import model_disagreement_map
    caveats: list[str] = []
    for d in model_disagreement_map(inv_spec, studies):
        if d["disagreement"]:
            caveats.append(f"conditional on resolving {d['study']} / {d['observable']} "
                           f"(arms span {d['spread']}) — the enclosure region may shift")
    return caveats


def transition_phase_report(inv_spec: dict, studies: dict,
                            parent_versions: list[str] | None = None) -> dict:
    """The Cycle N+2 deliverable: the composed model version, the phase diagram, an
    outcome tally, and the conditioning caveats."""
    surface = estimate_outcome_surface()
    tally: dict[str, int] = {}
    for cell in surface:
        tally[cell["outcome"]] = tally.get(cell["outcome"], 0) + 1
    return {
        "model_version": compose_transition_model(studies, parent_versions),
        "surface": surface,
        "tally": tally,
        "caveats": conditioning_caveats(inv_spec, studies),
    }
