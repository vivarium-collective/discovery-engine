"""Study refinement — Discovery Engine 2.0.

The symmetric partner to ``study_seeding``: instead of authoring a NEW study, improve
an EXISTING one in place when the engine flags it as thin — add replicate seeds, add
variant arms that sweep the finding's regime, and/or raise model fidelity. Triggered
by the signals the engine already emits (``rigor`` replicate count, ``fidelity_flags``
``low_fidelity_support``), so refinement becomes a first-class, autonomy-gated cycle op
rather than a hand-run hardening pass.

Derive-first: ``refinement_needs`` + ``refine_study`` are pure projections/transforms;
a thin CLI writes the refined study.yaml. Refinement is gated by the same autonomy
policy as seeding (``study_seeding.select_for_launch``).
"""
from __future__ import annotations


from .hypotheses import _as_list, fidelity_flags
from .rigor import _replicate_count

REPLICATE = "replicate"           # add replicate seeds (cross-seed robustness)
SWEEP = "sweep"                   # add variant arms sweeping a parameter
RAISE_FIDELITY = "raise_fidelity"  # swap in a higher-fidelity composite (model improvement)


def refinement_needs(inv_spec: dict, study_spec: dict, *, min_replicates: int = 3) -> list[dict]:
    """What refinements a study needs, derived from the engine's own flags. Returns
    ``[{kind, reason}]`` (empty = adequately rigorous). Pure/read-only."""
    needs: list[dict] = []
    n_rep, sweep = _replicate_count(study_spec)
    if n_rep < min_replicates:
        needs.append({"kind": REPLICATE,
                      "reason": f"only {n_rep} replicate(s) — a stochastic contrast needs "
                                f">= {min_replicates} seeds for a credible claim"})
    if not sweep:
        needs.append({"kind": SWEEP,
                      "reason": "no parameter sweep — the result is a single point, not a "
                                "characterised regime"})
    sslug = study_spec.get("name") or study_spec.get("slug")
    for fl in fidelity_flags(inv_spec or {}, [study_spec]):
        if fl.get("kind") == "low_fidelity_support" and fl.get("study") == sslug:
            needs.append({"kind": RAISE_FIDELITY,
                          "reason": "a supporting finding rests on low-fidelity evidence — "
                                    "re-run on a higher-fidelity model"})
            break
    return needs


def _baseline(study_spec: dict) -> dict:
    return next((a for a in _as_list(study_spec.get("baseline")) if isinstance(a, dict)), {})


# Parameters bounded to the unit interval [0, 1] by their meaning — a 2x sweep of a
# value near 1 would otherwise produce an invalid (>1) arm that fails to run.
_UNIT_INTERVAL_TOKENS = ("fidelity", "probability", "_prob", "prob_", "fraction",
                         "_frac", "frac_", "_rate", "ratio")


def _param_bounds(name: str, explicit: dict | None) -> tuple[float | None, float | None]:
    """Bounds for a sweep parameter: an explicit ``{param: (lo, hi)}`` wins (e.g. from
    the composite's declared schema); else a unit-interval heuristic from the name."""
    if explicit and name in explicit:
        lo, hi = explicit[name]
        return lo, hi
    if any(tok in name.lower() for tok in _UNIT_INTERVAL_TOKENS):
        return 0.0, 1.0
    return None, None


def _clamp(v: float, lo: float | None, hi: float | None) -> float:
    if lo is not None:
        v = max(v, lo)
    if hi is not None:
        v = min(v, hi)
    return v


def _default_sweep(study_spec: dict, param_bounds: dict | None = None) -> tuple[str | None, list]:
    """Pick a sweep parameter + values: the baseline's first numeric param, swept at
    0.5x / 2x around its value — clamped to the parameter's valid range so a value
    near a bound (e.g. a fidelity near 1) does not produce an out-of-range arm.
    Distinct, in-range values only."""
    params = _baseline(study_spec).get("params") or {}
    for k, v in params.items():
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v:
            lo, hi = _param_bounds(k, param_bounds)
            vals = []
            for raw in (v * 0.5, v * 2.0):
                cv = round(_clamp(raw, lo, hi), 12)
                if cv != v and cv not in vals:
                    vals.append(cv)
            return k, vals
    return None, []


def refine_study(study_spec: dict, *, needs: list[dict] | None = None, n_replicates: int = 3,
                 sweep_param: str | None = None, sweep_values: list | None = None,
                 param_bounds: dict | None = None,
                 higher_fidelity_composite: str | None = None) -> tuple[dict, list[dict]]:
    """Return a refined copy of ``study_spec`` + the list of changes applied. Applies
    only the refinements in ``needs`` (default: all of them). Pure — never writes."""
    kinds = {n["kind"] for n in (needs or [{"kind": REPLICATE}, {"kind": SWEEP}, {"kind": RAISE_FIDELITY}])}
    out = dict(study_spec)
    changes: list[dict] = []

    if REPLICATE in kinds:
        rob = dict(out.get("robustness") or {})
        rob["n_replicates"] = max(int(n_replicates), int(rob.get("n_replicates") or 0), 3)
        rob.setdefault("seeds", list(range(1, rob["n_replicates"] + 1)))
        out["robustness"] = rob
        changes.append({"kind": REPLICATE, "detail": f"robustness.n_replicates = {rob['n_replicates']}"})

    if SWEEP in kinds:
        param = sweep_param
        values = sweep_values
        if param is None:
            param, values = _default_sweep(study_spec, param_bounds)
        if param and values:
            base = _baseline(study_spec)
            variants = list(_as_list(out.get("variants")))
            existing = {v.get("name") for v in variants if isinstance(v, dict)}
            for val in values:
                name = f"sweep-{param}-{val}"
                if name in existing:
                    continue
                arm = {"name": name, "base": base.get("name", "baseline"), "params": {param: val}}
                variants.append(arm)
            out["variants"] = variants
            rob = dict(out.get("robustness") or {})
            rob["parameter_sweep"] = True
            out["robustness"] = rob
            changes.append({"kind": SWEEP, "detail": f"swept {param} over {values} (+{len(values)} variant arm(s))"})

    if RAISE_FIDELITY in kinds and higher_fidelity_composite:
        base = _baseline(study_spec)
        new_base = dict(base)
        prior = new_base.get("composite")
        new_base["composite"] = higher_fidelity_composite
        new_base["note"] = (new_base.get("note", "") + f" [refined: raised fidelity from {prior}]").strip()
        out["baseline"] = [new_base] + [a for a in _as_list(out.get("baseline"))
                                        if isinstance(a, dict) and a is not base]
        changes.append({"kind": RAISE_FIDELITY, "detail": f"baseline composite → {higher_fidelity_composite}"})
    elif RAISE_FIDELITY in kinds:
        changes.append({"kind": RAISE_FIDELITY, "detail": "needs a higher-fidelity composite "
                        "(pass --composite or let the model architect pick) — not applied"})

    out["refined_from"] = {"study": study_spec.get("name"), "changes": [c["kind"] for c in changes]}
    return out, changes
