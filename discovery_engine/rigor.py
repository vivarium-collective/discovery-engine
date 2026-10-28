"""Evidence & rigor scorecard — deterministic feedback on how well a study /
investigation defends its claims against a skeptical reader.

Motivation
----------
A skeptical reviewer of a simulation-based investigation repeatedly asks the
same questions: *Did you replicate across seeds? Where are the negative
controls? Have you separated observation from interpretation and excluded
alternative explanations? Are the acceptance criteria falsifiable, or tailored
to succeed? Is there an adversarial study that tries to break the framework?*

The framework already has rich structure (behavior_tests, gates, findings,
acceptance_criteria, simulation_set.seeds) but never *computes feedback* on
these dimensions, so authors omit them and reviewers can't see the gaps. This
module turns each rigor dimension into a deterministic signal computed from
declared (optional) fields. A missing field is not an error — it produces a
``gap`` signal, which IS the feedback that prompts the next investigation to do
better.

Everything here is pure and deterministic (no LLM/AI) so the AI-free dashboard
can call it directly. Mirrors :mod:`discovery_engine.study_verdict` in spirit:
pure ``spec -> dict`` functions.

Schema (all OPTIONAL, back-compatible)
--------------------------------------
study.yaml::

    kind: adversarial                 # or study_kind: adversarial (default: standard)
    robustness:                       # else derived from simulation_set.seeds / runs
      n_replicates: 3
      seeds: [0, 1, 2]
      parameter_sweep: true
    controls:                         # negative / discriminating / calibration controls
      - name: externally-maintained-membrane
        kind: negative                # negative | adversarial | positive | borderline
        hypothesis: "If the membrane is supplied externally, closure should FAIL."
        expected: fail-closure
        observed: fail-closure        # optional, after running
        result: PASS                  # PASS = control discriminated as expected
      # A positive/borderline control calibrates the metric across its range (C4).
    limitations: "What this result does NOT show: e.g. only one membrane function
      (a geometric boundary) is modelled, not transport/signalling/energetics."
      # or does_not_show: [...]
    alternative_hypotheses:           # competing explanations + how excluded
      - claim: "Survival gain is plain movement-to-resources, not sense-making."
        discriminated_by: "non-sensing motile control"
        status: not-excluded          # excluded | not-excluded | untested
    findings:                         # existing shape, extended with two opt fields
      - statement: "..."
        tier: interpretation          # observation | mechanism | interpretation
        mechanism_origin: engineered  # engineered | emergent (for tier=interpretation)
        evidence: {from_test: agency-advantage}
    falsifiability: "Closure would fail if the membrane were externally supplied."

investigation.yaml::

    acceptance_criteria:
      - study: ...
        behavior: ...
        could_fail_if: "..."          # falsifiability note
        independent: false            # derived from theory-under-test vs independent perspective
    competing_frameworks:             # compared interpretive lenses (C13)
      - name: active inference
        relation: "predicts the same survival gain; distinguished by ..."
"""
from __future__ import annotations

from typing import Any

# Canonical band detector — see band_provenance.has_numeric_band's docstring
# for the pass_if.value reconciliation rule (numeric value = band regardless
# of comparator op; non-numeric value, e.g. a config-selector, is not).
from .band_provenance import has_numeric_band as _has_numeric_band

# Severity vocabulary, ordered worst→best for roll-ups.
GAP = "gap"
WARN = "warn"
OK = "ok"
_SEVERITY_RANK = {GAP: 0, WARN: 1, OK: 2}

# A fourth, non-scoring severity (mode-awareness, below): a dimension that does
# not APPLY to this study's kind. It is neither a gap nor credit — it is excluded
# from the gap/warn/ok score and the addressed/total roll-up entirely. Used for
# the hypothesis-test dimensions on a descriptive / informational study.
NA = "not_applicable"

# Dimensions that presuppose a HYPOTHESIS UNDER TEST. On a descriptive /
# informational study (a catalog / inventory / reference with no pass-fail claim
# to defend) these are category-inappropriate — asking a units catalog for
# "replication across seeds" or "negative controls" is noise that makes finished
# reference work read as deficient. For such studies they are relabelled
# :data:`NA` ("not applicable — descriptive reference") instead of counting as
# gaps. The dimensions left applicable (limitations / completeness, next steps,
# run persistence) ARE meaningful for a reference deliverable.
_HYPOTHESIS_TEST_DIMS = frozenset({
    "replication",
    "negative_control",
    "alternatives",
    "claim_discipline",
    "falsifiability",
    "mechanism_origin",
    "preregistration",
    "threshold_provenance",
    "metric_calibration",
    "generality",
    # Review-integration dims (conditionally appended — see G2/G3 below); they
    # presuppose a claim under test, so a descriptive study relabels them NA.
    "statistical_power",
    "held_out_generalization",
})

# Authored verdict values that mark a study as descriptive (no hypothesis test).
_DESCRIPTIVE_VERDICTS = ("informational", "descriptive")

# Above this coefficient of variation, per-measure spread across seeds is
# treated as cross-seed disagreement (item 14 — replication scores AGREEMENT,
# not merely count). Deterministic threshold; tolerant of missing sub-fields.
_HIGH_CV = 0.5

# study_type vocabulary (critique #10). A study's intent governs how its
# passing tests are read: an ``exploratory`` study OBSERVES (its passes are not
# falsification credit); an ``adversarial`` study tries to BREAK the framework
# (stronger credit); a ``confirmatory`` study tests pre-declared criteria (its
# pass is downgraded when the criteria were not pre-registered — see #18);
# ``diagnostic`` studies probe a prior failure; ``standard`` is the default.
STUDY_TYPES = (
    "exploratory", "confirmatory", "diagnostic", "adversarial", "standard",
)

# claim_scope vocabulary (critique #21). The claim CLASS a finding makes —
# distinct from ``tier`` (observation/mechanism/interpretation) and from
# ``lifecycle_state`` (maturity). A theoretical / generality scope demands
# robustness or generality evidence; a single-instance result claiming it earns
# a WARN (see ``claim_discipline``).
CLAIM_SCOPES = (
    "local-implementation", "mechanism", "behavioral", "theoretical", "generality",
)

# generality vocabulary (critique #22). ``axes_tested`` enumerates the
# independent dimensions a finding's robustness was probed along; ``level`` is
# the breadth of the resulting claim.
GENERALITY_AXES = (
    "parameter_regime", "initial_conditions", "discretization", "geometry",
    "alt_implementation", "independent_authoring",
)
GENERALITY_LEVELS = ("instance_specific", "mechanism", "framework")

# threshold-provenance vocabulary (critique #9). Where an acceptance threshold
# came from — distinct from ``cites`` (a literature source link).
THRESHOLD_PROVENANCE_KINDS = (
    "theory", "calibration", "literature", "expert", "exploratory", "post_hoc",
)

# Emitter kinds a run record may declare to evidence that its trajectory was
# PERSISTED (not just summarised). A run that emits via one of these — or that
# carries a run-db reference (db_path / run_db / …) — is reproducible from disk.
EMITTER_KINDS = ("sqlite", "parquet", "xarray")

# Keys on a run record that reference a persisted run database / output file.
_RUN_DB_REF_KEYS = ("db_path", "run_db", "runs_db", "db_file", "db", "run_db_path")


def run_is_emitter_backed(run: Any) -> bool:
    """True when a single ``runs[]`` record evidences emitter-backed persistence.

    A run is "persisted via an emitter" when its record either:

    * carries an ``emitter`` whose value (a string, or a dict's ``type`` /
      ``kind``) is one of :data:`EMITTER_KINDS` (sqlite / parquet / xarray), or
    * carries a non-empty run-db / output reference under any of
      :data:`_RUN_DB_REF_KEYS` (``db_path`` / ``run_db`` / ``runs_db`` / …).

    Pure and tolerant: a non-dict / malformed record returns ``False``.
    """
    if not isinstance(run, dict):
        return False
    em = run.get("emitter")
    if isinstance(em, str) and em.strip().lower() in EMITTER_KINDS:
        return True
    if isinstance(em, dict):
        kind = str(em.get("type") or em.get("kind") or em.get("emitter") or "").strip().lower()
        if kind in EMITTER_KINDS:
            return True
    for k in _RUN_DB_REF_KEYS:
        if _nonempty(run.get(k)):
            return True
    return False


def _study_type(spec: dict) -> str:
    """Return the study's intent type (critique #10), generalizing the old
    ``_is_adversarial`` helper.

    Reads the explicit ``study_type`` field, falling back to the legacy
    ``kind`` / ``study_kind`` aliases (so existing ``kind: adversarial`` studies
    keep working). An unknown / unset value degrades to ``"standard"``.
    """
    spec = spec or {}
    raw = str(
        spec.get("study_type")
        or spec.get("kind")
        or spec.get("study_kind")
        or ""
    ).strip().lower()
    return raw if raw in STUDY_TYPES else "standard"


def _as_list(v: Any) -> list:
    return v if isinstance(v, list) else ([] if v is None else [v])


def _nonempty(v: Any) -> bool:
    """True if a str/list field carries actual content."""
    if isinstance(v, str):
        return bool(v.strip())
    if isinstance(v, list):
        return any(str(x).strip() for x in v)
    return bool(v)


def _findings(spec: dict) -> list[dict]:
    out = []
    for f in _as_list(spec.get("findings")):
        if isinstance(f, dict):
            out.append(f)
        elif isinstance(f, str):
            out.append({"statement": f})
    return out


def _replicate_count(spec: dict) -> tuple[int, bool]:
    """Return (n_replicates, parameter_sweep) from declared fields.

    Prefers an explicit ``robustness`` block; else counts ``simulation_set``
    seeds; else counts recorded ``runs``.
    """
    rob = spec.get("robustness") or {}
    if isinstance(rob, dict):
        n = rob.get("n_replicates")
        seeds = rob.get("seeds")
        sweep = bool(rob.get("parameter_sweep"))
        if isinstance(n, int):
            return n, sweep
        if isinstance(seeds, list):
            return len(seeds), sweep
    # Derive from simulation_set seeds.
    seeds_total = 0
    for sim in _as_list(spec.get("simulation_set")):
        if isinstance(sim, dict):
            seeds_total += len(_as_list(sim.get("seeds")))
    if seeds_total:
        return seeds_total, False
    # Fall back to count of recorded runs.
    return len(_as_list(spec.get("runs"))), False


def _replication_agreement(spec: dict, n_rep: int) -> tuple[bool, str]:
    """Inspect ``robustness`` for cross-seed AGREEMENT (item 14).

    Returns ``(disagrees, reason)``. Two deterministic signals, both optional:

    * ``robustness.per_measure[*]`` — an explicit ``cv`` (coefficient of
      variation), else derived as ``|std / mean|``; a value above
      :data:`_HIGH_CV` means the measure is not stable across seeds.
    * ``robustness.seeds_with_advantage`` — count (int) or list of seeds, or a
      fraction in ``(0, 1]`` (float); no majority (``<= 0.5`` of replicates)
      means the seeds don't agree on the effect.

    When no agreement evidence is present, returns ``(False, "")`` — the
    replicate count alone stands. Tolerant of malformed sub-fields.
    """
    rob = spec.get("robustness")
    if not isinstance(rob, dict):
        return False, ""
    reasons: list[str] = []

    # 1. Per-measure coefficient of variation.
    for m in _as_list(rob.get("per_measure")):
        if not isinstance(m, dict):
            continue
        cv = m.get("cv")
        if cv is None:
            std = m.get("std")
            mean = m.get("mean")
            try:
                if std is not None and mean not in (None, 0):
                    cv = abs(float(std) / float(mean))
            except (TypeError, ValueError, ZeroDivisionError):
                cv = None
        try:
            if cv is not None and float(cv) > _HIGH_CV:
                reasons.append(f"{m.get('name') or 'a measure'} CoV={float(cv):.2f}")
        except (TypeError, ValueError):
            pass

    # 2. Majority agreement among seeds.
    swa = rob.get("seeds_with_advantage")
    frac = None
    if isinstance(swa, bool):
        frac = None
    elif isinstance(swa, list):
        frac = (len(swa) / n_rep) if n_rep else None
    elif isinstance(swa, int):
        frac = (swa / n_rep) if n_rep else None
    elif isinstance(swa, float):
        frac = swa if 0 < swa <= 1 else ((swa / n_rep) if n_rep else None)
    if frac is not None and frac <= 0.5:
        reasons.append("no majority of seeds shows the advantage")

    return (bool(reasons), "; ".join(reasons))


def _dim(id_: str, label: str, severity: str, detail: str, comments: list[str]) -> dict:
    return {"id": id_, "label": label, "severity": severity,
            "detail": detail, "comments": comments}


# ---------------------------------------------------------------------------
# Threshold provenance + sensitivity (critique #9)
# ---------------------------------------------------------------------------

def _study_test_entries(spec: dict) -> list[dict]:
    """Every behavior_tests[] / tests[] entry (dicts only)."""
    out: list[dict] = []
    for section in ("behavior_tests", "tests"):
        for t in _as_list((spec or {}).get(section)):
            if isinstance(t, dict):
                out.append(t)
    return out


# _has_numeric_band: see the top-of-file import — was a local reimplementation
# here that disagreed with band_provenance.py / report_linter.py.


def _numeric_band_tests(spec: dict) -> list[dict]:
    return [t for t in _study_test_entries(spec) if _has_numeric_band(t)]


def _test_threshold_sourced(test: dict) -> bool:
    """A numeric-band test is "sourced" when it links a literature source
    (``cites`` on the test or its ``calibration_anchor``) OR declares an honest
    ``pass_if.provenance.kind`` (critique #9)."""
    if not isinstance(test, dict):
        return False
    if test.get("cites"):
        return True
    anch = test.get("calibration_anchor")
    if isinstance(anch, dict) and (anch.get("cites") or anch.get("literature_target") is not None):
        return True
    pass_if = test.get("pass_if")
    if isinstance(pass_if, dict):
        prov = pass_if.get("provenance")
        if isinstance(prov, dict) and str(prov.get("kind") or "").strip():
            return True
    return False


def threshold_sensitivity(spec: dict, test_name: str,
                          deltas: tuple[float, ...] = (-0.2, -0.1, 0.1, 0.2)) -> list[dict]:
    """Re-evaluate a test's pass predicate against its RECORDED observed value
    across cutoffs scaled by ``deltas`` (critique #9).

    Pure ``spec -> list[{delta, cutoff, result}]``. Finds the named test, reads
    its numeric band (via :func:`band_provenance._band_from_pass_if`), reads the
    canonical run's recorded ``outcomes[test_name].observed`` (falling back to
    ``measured_value``), then for each delta scales every numeric band bound by
    ``(1 + delta)`` and re-checks whether the observed value still satisfies the
    band. Shows how brittle a pass/fail is to the exact cutoff.

    Returns ``[]`` (the guard) when the test is not found, has no numeric band,
    or has no recorded observed value — there is nothing to re-evaluate.
    ``cutoff`` is the scaled band dict; ``result`` is ``"PASS"`` / ``"FAIL"``.
    """
    spec = spec or {}
    test = None
    for t in _study_test_entries(spec):
        if t.get("name") == test_name:
            test = t
            break
    if test is None:
        return []

    try:
        from .band_provenance import _band_from_pass_if
    except Exception:  # noqa: BLE001 — defensive
        _band_from_pass_if = None  # type: ignore
    band = _band_from_pass_if(test.get("pass_if")) if _band_from_pass_if else None
    pass_if = test.get("pass_if") if isinstance(test.get("pass_if"), dict) else {}
    op = pass_if.get("op")
    # Fallback for the {op, value} pass_if shape (which _band_from_pass_if, keyed
    # on low/high/threshold, does not recognise): treat ``value`` as the cutoff.
    if not band:
        val = pass_if.get("value")
        if isinstance(val, (int, float)) and not isinstance(val, bool):
            band = {"threshold": val}
    if not band:
        return []

    # Recorded observed value from the canonical run's outcomes.
    try:
        from .study_outcomes import canonical_outcomes
        outcomes = canonical_outcomes(spec)
    except Exception:  # noqa: BLE001 — defensive
        outcomes = {}
    out = outcomes.get(test_name) if isinstance(outcomes, dict) else None
    observed = None
    if isinstance(out, dict):
        observed = out.get("observed")
        if observed is None:
            observed = out.get("measured_value")
    try:
        observed = float(observed)
    except (TypeError, ValueError):
        return []

    results: list[dict] = []
    for d in deltas:
        scaled = {k: (v * (1 + d)) for k, v in band.items()
                  if isinstance(v, (int, float)) and not isinstance(v, bool)}
        results.append({
            "delta": d,
            "cutoff": scaled,
            "result": "PASS" if _observed_satisfies(observed, scaled, op) else "FAIL",
        })
    return results


def _observed_satisfies(observed: float, band: dict, op: str | None) -> bool:
    """Pure predicate: does a scalar ``observed`` satisfy a numeric band?

    Handles range bands (low/high), explicit comparator ``op`` against a
    threshold/value, and a bare threshold (defaults to ``>=``).
    """
    if "low" in band and "high" in band:
        return band["low"] <= observed <= band["high"]
    ref = band.get("threshold", band.get("value"))
    o = (op or "").strip()
    if o in ("<=", "lte"):
        return ref is not None and observed <= ref
    if o in (">=", "gte"):
        return ref is not None and observed >= ref
    if o == "<":
        return ref is not None and observed < ref
    if o == ">":
        return ref is not None and observed > ref
    if o in ("==", "eq"):
        return ref is not None and observed == ref
    if o in ("!=", "ne"):
        return ref is not None and observed != ref
    if "threshold" in band:
        return observed >= band["threshold"]
    if "value" in band:
        return observed == band["value"]
    return False


# ---------------------------------------------------------------------------
# Metric calibration ladder (critique #20)
# ---------------------------------------------------------------------------

_LADDER_RUNGS = ("known_fail", "known_pass", "borderline", "stress")


def _calibration_ladders(spec: dict) -> list[dict]:
    """Every declared ``calibration_ladder`` (a single dict or a list)."""
    raw = (spec or {}).get("calibration_ladder")
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    return []


def _ladder_severity(ladder: dict, control_names: set[str]) -> tuple[str, str]:
    """Classify one calibration ladder → (severity, detail).

    A rung is "filled" when its value is a non-null control reference that
    resolves to a ``controls[].name``. GAP ≤1 rung, WARN when known_fail +
    known_pass are filled but no borderline, OK at ≥3 rungs.
    """
    metric = ladder.get("metric") or "metric"
    filled = {
        rung for rung in _LADDER_RUNGS
        if isinstance(ladder.get(rung), str) and ladder.get(rung) in control_names
    }
    n = len(filled)
    if n >= 3:
        return OK, f"{metric}: {n}/4 rungs calibrated ({sorted(filled)})"
    if "known_fail" in filled and "known_pass" in filled and "borderline" not in filled:
        return WARN, (f"{metric}: known_fail + known_pass but no borderline — add a borderline "
                      "case so the metric is calibrated near the cutoff, not just at the extremes")
    return GAP, (f"{metric}: only {n} rung(s) resolve to a control "
                 "(need known_fail + known_pass + borderline)")


# ---------------------------------------------------------------------------
# Generality (critique #22)
# ---------------------------------------------------------------------------

def _study_swept(spec: dict) -> bool:
    """True when the study declares a parameter sweep (``robustness.swept_param``
    name or the legacy ``robustness.parameter_sweep`` flag)."""
    rob = (spec or {}).get("robustness")
    if not isinstance(rob, dict):
        return False
    if str(rob.get("swept_param") or "").strip():
        return True
    return bool(rob.get("parameter_sweep"))


def _finding_generality_axes(finding: dict) -> set[str]:
    """Valid ``generality.axes_tested`` enum values declared on a finding."""
    gen = (finding or {}).get("generality")
    if not isinstance(gen, dict):
        return set()
    return {str(a).strip() for a in _as_list(gen.get("axes_tested"))
            if str(a).strip() in GENERALITY_AXES}


def _study_generality_axes(spec: dict) -> set[str]:
    """The union of generality axes evidenced across a study's findings, plus
    ``parameter_regime`` when a robustness sweep is declared."""
    axes: set[str] = set()
    for f in _findings(spec):
        axes |= _finding_generality_axes(f)
    if _study_swept(spec):
        axes.add("parameter_regime")
    return axes


def _has_generality_signal(spec: dict, finding: dict) -> bool:
    """True when a finding has ANY generality/robustness evidence: its own
    ``generality.axes_tested`` OR a study-level robustness sweep / ≥3 replicates."""
    if _finding_generality_axes(finding):
        return True
    if _study_swept(spec):
        return True
    n_rep, sweep = _replicate_count(spec)
    return sweep or n_rep >= 3


def _authored_verdict(spec: dict) -> str:
    """The study's authored verdict, searched across its canonical locations:
    top-level ``verdict``, ``report.verdict``, then any
    ``conclusion_verdicts[].verdict``. Lower-cased; ``""`` when none authored.
    """
    spec = spec or {}
    v = str(spec.get("verdict") or "").strip().lower()
    if v:
        return v
    rep = spec.get("report")
    if isinstance(rep, dict):
        v = str(rep.get("verdict") or "").strip().lower()
        if v:
            return v
    for cv in _as_list(spec.get("conclusion_verdicts")):
        if isinstance(cv, dict):
            v = str(cv.get("verdict") or "").strip().lower()
            if v:
                return v
    return ""


def _has_acceptance(spec: dict) -> bool:
    """True when the study declares ANY acceptance bar — a non-empty
    ``behavior_tests`` / ``tests`` / ``acceptance_criteria``. An empty ``[]``
    (the explicit "no tests" of a reference study) does not count."""
    spec = spec or {}
    for key in ("behavior_tests", "tests", "acceptance_criteria"):
        if _nonempty(spec.get(key)):
            return True
    return False


def is_descriptive_study(spec: dict) -> bool:
    """True when a study is DESCRIPTIVE / informational — a catalog, inventory or
    reference with no hypothesis to test — so the hypothesis-test rigor
    dimensions don't apply (mode-awareness).

    Two deterministic signals, either sufficient:

    * an authored ``verdict`` of ``informational`` / ``descriptive`` (read from
      top-level ``verdict`` / ``report.verdict`` / ``conclusion_verdicts``), OR
    * an authored ``gate_status`` of ``not_applicable`` combined with no
      acceptance bar (empty / absent ``tests`` / ``behavior_tests`` /
      ``acceptance_criteria``).

    Pure and tolerant of a malformed / minimal spec.
    """
    spec = spec or {}
    if _authored_verdict(spec) in _DESCRIPTIVE_VERDICTS:
        return True
    gate = str(spec.get("gate_status") or "").strip().lower()
    if gate == "not_applicable" and not _has_acceptance(spec):
        return True
    return False


# ---------------------------------------------------------------------------
# Review-integration dims (G2 / G3 / G6) — conditionally-appended dimensions
# that only bite the claim classes they police (like the confirmatory-only
# preregistration dim), so every other study's dimension count is unchanged
# (back-compat: study_rigor({}) still scores 12 dimensions).
#
# Schema (all OPTIONAL, back-compatible)::
#
#     findings:
#       - claim_type: causal            # causal | directional  (G2)
#         # or: substitutability | equivalence | surrogate     (G3)
#     arms: [treatment, control]        # or contrast: ... / statistics.arms (G2)
#     statistics:                       # the declared test for a causal contrast
#       test: mann-whitney-u
#       p_value: 0.003
#       effect_size: 0.61
#       n_per_arm: 24
#       gate_on: ensemble               # ensemble | flagship_seed (or spec.gate_on)
#     held_out:                         # G3 — train vs test condition
#       train: [tuned-condition]        # aliases: generalization / train_test
#       test: [held-out-condition]
#     degrees_of_freedom:               # G3 — free params vs matched observables
#       free_parameters: 3
#       matched_observables: 7
#     representation_conversion:        # G6 — a conserved quantity crosses reps
#       from: lattice_pixels
#       to: particles
#       quantity: mass
#       ledger: {test: mass_ledger, result: PASS}
# ---------------------------------------------------------------------------

# claim_type vocabulary for the conditional dims — distinct from ``tier`` and
# ``claim_scope``: the KIND of claim, which decides which extra bar applies.
_CAUSAL_CLAIM_TYPES = ("causal", "directional")
_EQUIVALENCE_CLAIM_TYPES = (
    "substitutability", "equivalence", "surrogate", "interface_equivalence",
    "same_interface",
)
# Conservative text signals for an equivalence/substitutability claim when no
# claim_type is declared (G3 detection: "if none, don't flag").
_EQUIVALENCE_TEXT_SIGNALS = (
    "substitut", "equivalen", "same interface", "surrogate", "interchangeab",
    "drop-in",
)
# Text tokens marking a drift-null / no-effect control arm (G2).
_DRIFT_NULL_TOKENS = ("drift", "null", "no-effect", "no_effect")
_DRIFT_NULL_KINDS = ("null", "drift", "drift-null", "drift_null", "no-effect", "no_effect")
# gate_on values (G2): the pass/fail gate reads the ensemble statistic, not a
# single flagship seed.
_ENSEMBLE_GATE_VALUES = ("ensemble", "ensemble_statistic", "ensemble-statistic")
# Minimum replicates per arm for a causal claim from a stochastic contrast (G2)
# — the review's power bar, deliberately above the ≥3-seed replication floor.
_CAUSAL_MIN_N_PER_ARM = 20
# Conserved-quantity tokens for the conservative G6 text signal.
_CONSERVED_QUANTITY_TOKENS = (
    "mass", "pixel", "particle", "volume", "flux", "molecule", "count", "conserv",
)


def _finding_claim_type(finding: dict) -> str:
    return str((finding or {}).get("claim_type") or "").strip().lower()


def _study_statistics(spec: dict) -> dict:
    """The declared ``statistics`` block — study-level preferred, else the first
    finding-level one (mirrors the alternatives dim's source-preference style)."""
    stats = (spec or {}).get("statistics")
    if isinstance(stats, dict):
        return stats
    for f in _findings(spec or {}):
        s = f.get("statistics")
        if isinstance(s, dict):
            return s
    return {}


def _declares_arm_contrast(spec: dict) -> bool:
    """True when the study declares a between-arm contrast (``arms`` /
    ``contrast(s)`` at the study level, ``statistics.arms``, or a finding's
    ``evidence.arms`` / ``evidence.contrast``)."""
    spec = spec or {}
    for key in ("arms", "contrast", "contrasts"):
        if _nonempty(spec.get(key)):
            return True
    if _nonempty(_study_statistics(spec).get("arms")):
        return True
    for f in _findings(spec):
        ev = f.get("evidence")
        if isinstance(ev, dict) and (_nonempty(ev.get("arms")) or _nonempty(ev.get("contrast"))):
            return True
    return False


def _causal_stochastic_claim(spec: dict) -> bool:
    """G2 applicability: a causal/directional finding derived from a STOCHASTIC
    contrast between arms.

    Conservative (does not bite deterministic or non-causal studies):

    * the study must be stochastic — ``stochastic: true`` or ≥2 declared
      replicates (seeds), AND
    * a finding declares ``claim_type: causal|directional`` (always in scope), or
      a ``tier: interpretation`` finding coincides with a DECLARED arm contrast
      (``arms`` / ``contrast`` / ``statistics.arms`` / ``evidence.arms``).
    """
    spec = spec or {}
    n_rep, _ = _replicate_count(spec)
    if not (bool(spec.get("stochastic")) or n_rep >= 2):
        return False
    findings = _findings(spec)
    if any(_finding_claim_type(f) in _CAUSAL_CLAIM_TYPES for f in findings):
        return True
    has_interp = any((f.get("tier") or "").lower() == "interpretation" for f in findings)
    return has_interp and _declares_arm_contrast(spec)


def _has_drift_null_arm(spec: dict, stats: dict) -> bool:
    """A declared drift-null / no-effect control arm: ``statistics.null_arm`` /
    ``statistics.drift_null``, or a control whose kind / name / hypothesis marks
    it as the no-effect arm."""
    if _nonempty((stats or {}).get("null_arm")) or _nonempty((stats or {}).get("drift_null")):
        return True
    for c in _as_list((spec or {}).get("controls")):
        if not isinstance(c, dict):
            continue
        if (c.get("kind") or "").strip().lower() in _DRIFT_NULL_KINDS:
            return True
        hay = " ".join(str(c.get(k) or "") for k in ("name", "hypothesis", "expected")).lower()
        if any(t in hay for t in _DRIFT_NULL_TOKENS):
            return True
    return False


def _gate_target(spec: dict, stats: dict) -> str:
    """The declared gate target (``gate_on`` at study level or in statistics)."""
    return str((spec or {}).get("gate_on") or (stats or {}).get("gate_on") or "").strip().lower()


def _equivalence_claim_present(spec: dict) -> bool:
    """G3 applicability: a substitutability / equivalence / surrogate claim —
    a declared ``claim_type`` (study- or finding-level) or a clear text signal
    in a finding statement. No claim → don't flag."""
    spec = spec or {}
    if str(spec.get("claim_type") or "").strip().lower() in _EQUIVALENCE_CLAIM_TYPES:
        return True
    for f in _findings(spec):
        if _finding_claim_type(f) in _EQUIVALENCE_CLAIM_TYPES:
            return True
        text = str(f.get("statement") or "").lower()
        if any(sig in text for sig in _EQUIVALENCE_TEXT_SIGNALS):
            return True
    return False


def _held_out_block(spec: dict) -> dict:
    """The declared train-vs-test block (``held_out`` / ``generalization`` /
    ``train_test``) — the first dict wins."""
    for key in ("held_out", "generalization", "train_test"):
        b = (spec or {}).get(key)
        if isinstance(b, dict):
            return b
    return {}


def _held_out_conditions(block: dict) -> tuple[set[str], set[str]]:
    """(train, test) condition-name sets from a held-out block (alias-tolerant)."""
    block = block or {}
    train = (block.get("train") if block.get("train") is not None
             else block.get("train_conditions") if block.get("train_conditions") is not None
             else block.get("tuned_on"))
    test = (block.get("test") if block.get("test") is not None
            else block.get("test_conditions") if block.get("test_conditions") is not None
            else block.get("held_out") if block.get("held_out") is not None
            else block.get("evaluated_on"))
    to_set = lambda v: {str(x).strip().lower() for x in _as_list(v) if str(x).strip()}  # noqa: E731
    return to_set(train), to_set(test)


def _dof_statement(spec: dict) -> bool:
    """A degrees-of-freedom-vs-constraints statement: ``degrees_of_freedom`` /
    ``dof_vs_constraints`` (a non-empty string, or a dict declaring both
    free parameters and matched observables/constraints)."""
    spec = spec or {}
    for key in ("degrees_of_freedom", "dof_vs_constraints"):
        v = spec.get(key)
        if isinstance(v, str) and v.strip():
            return True
        if isinstance(v, dict):
            free = v.get("free_parameters", v.get("free_params"))
            matched = v.get("matched_observables", v.get("constraints"))
            if free is not None and matched is not None:
                return True
    return False


def _representation_conversions(spec: dict) -> list[dict]:
    """Declared representation conversions (``representation_conversion`` — a
    single dict or a list — or the plural alias)."""
    raw = ((spec or {}).get("representation_conversion")
           or (spec or {}).get("representation_conversions"))
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    return []


def _conversion_text_signal(spec: dict) -> bool:
    """Conservative G6 text signal: a finding statement that talks about
    CONVERTING a conserved-quantity-bearing representation (must contain a
    convert-word AND a conserved-quantity token)."""
    for f in _findings(spec or {}):
        text = str(f.get("statement") or "").lower()
        if ("convert" in text or "conversion" in text) and any(
                q in text for q in _CONSERVED_QUANTITY_TOKENS):
            return True
    return False


def _conversion_ledger(conv: dict, spec: dict) -> Any:
    """The ledger declaration covering one conversion — on the entry
    (``ledger`` / ``conservation_check`` / ``ledger_test``) or the study-level
    ``conservation_ledger`` fallback."""
    for key in ("ledger", "conservation_check", "ledger_test"):
        v = (conv or {}).get(key)
        if v is not None:
            return v
    return (spec or {}).get("conservation_ledger")


def _ledger_named_and_verified(ledger: Any) -> tuple[bool, bool]:
    """(named, verified) for a ledger declaration. A non-empty string names a
    check; a dict names one via test/check/name and is verified when its
    result/status reads pass/passed/verified/ok (or ``asserted: true``)."""
    if isinstance(ledger, str):
        return bool(ledger.strip()), False
    if isinstance(ledger, dict):
        named = any(_nonempty(ledger.get(k)) for k in ("test", "check", "name"))
        result = str(ledger.get("result") or ledger.get("status") or "").strip().lower()
        verified = result in ("pass", "passed", "verified", "ok") or ledger.get("asserted") is True
        return named or verified, named and verified
    return False, False


def study_rigor(spec: dict) -> dict:
    """Compute the per-study rigor scorecard.

    Returns ``{mode, descriptive, dimensions: [...],
    score: {gap,warn,ok,na,total}, summary: str}``. Pure; tolerant of a minimal
    spec (every absent field yields a ``gap``).

    MODE-AWARE: a descriptive / informational study (see
    :func:`is_descriptive_study`) has no hypothesis to defend, so the
    hypothesis-test dimensions (replication, controls, alternatives,
    falsifiability, …) are relabelled :data:`NA` ("not applicable — descriptive
    reference") and excluded from the gap/warn/ok score, rather than reported as
    a pile of category-inappropriate gaps.
    """
    spec = spec or {}
    findings = _findings(spec)
    interp = [f for f in findings if (f.get("tier") or "").lower() == "interpretation"]
    tiered = [f for f in findings if f.get("tier")]
    dims: list[dict] = []

    # 1. Replication [C4] — score AGREEMENT, not just count (item 14): with
    #    enough replicates, also check that the seeds actually agree.
    n_rep, sweep = _replicate_count(spec)
    if sweep or n_rep >= 3:
        base = f"{n_rep} replicate(s)" + (" + parameter sweep" if sweep else "")
        disagrees, why = _replication_agreement(spec, n_rep)
        if disagrees:
            dims.append(_dim("replication", "Replication", WARN,
                             base + f" but seeds disagree ({why}) — "
                             "result is not robust across seeds", ["C4"]))
        else:
            dims.append(_dim("replication", "Replication", OK, base, ["C4"]))
    elif n_rep == 2:
        dims.append(_dim("replication", "Replication", WARN,
                         "only 2 replicates — add seeds for a robustness claim", ["C4"]))
    else:
        dims.append(_dim("replication", "Replication", GAP,
                         "single run — no replication across seeds declared "
                         "(add robustness.seeds or simulation_set.seeds)", ["C4"]))

    # 2. Controls & calibration [C1, C2, C4] — a system that SHOULD fail
    #    (discriminative power) AND a clearly-passing / borderline case so the
    #    metric is calibrated across its range, not merely asserted.
    controls = [c for c in _as_list(spec.get("controls")) if isinstance(c, dict)]
    negs = [c for c in controls if (c.get("kind") or "").lower() in ("negative", "adversarial")]
    pos = [c for c in controls if (c.get("kind") or "").lower() in ("positive", "borderline")]
    # A control only discriminates if it actually ran (non-empty `observed`)
    # AND recorded a PASS — a PASS with no observation earns no credit (item 15).
    discriminating = [c for c in negs
                      if str(c.get("result", "")).upper() == "PASS" and _nonempty(c.get("observed"))]
    if not controls:
        dims.append(_dim("negative_control", "Controls & calibration", GAP,
                         "no controls — declare a system that SHOULD fail the criteria "
                         "(externally-maintained / -supplied) plus a clearly-passing / borderline "
                         "case so the metric is calibrated, not just asserted", ["C1", "C2", "C4"]))
    elif not negs:
        dims.append(_dim("negative_control", "Controls & calibration", WARN,
                         "controls declared but none negative/adversarial — add a system that SHOULD fail", ["C1", "C2"]))
    elif discriminating and pos:
        dims.append(_dim("negative_control", "Controls & calibration", OK,
                         f"{len(discriminating)} discriminating control(s) + a passing/borderline case "
                         "calibrate the metric across its range", ["C1", "C2", "C4"]))
    elif discriminating:
        dims.append(_dim("negative_control", "Controls & calibration", WARN,
                         "negative control discriminates, but no clearly-passing / borderline case to "
                         "calibrate the metric across its range", ["C2", "C4"]))
    else:
        dims.append(_dim("negative_control", "Controls & calibration", WARN,
                         f"{len(negs)} control(s) declared but none recorded a discriminating result", ["C1", "C2"]))

    # 3. Alternative hypotheses [C3, C6, C8] — also credit the Decide-phase
    #    synthesis (discovery_implications.alternate_hypotheses).
    # [C5] Single alternatives source: prefer the Decide-phase synthesis
    # (discovery_implications.alternate_hypotheses), fall back to the top-level
    # alternative_hypotheses so authored prose anywhere still counts.
    _di = spec.get("discovery_implications") or {}
    alts = []
    if isinstance(_di, dict):
        alts = [a for a in _as_list(_di.get("alternate_hypotheses")) if isinstance(a, dict)]
    if not alts:
        alts = [a for a in _as_list(spec.get("alternative_hypotheses")) if isinstance(a, dict)]
    excluded = [a for a in alts if (a.get("status") or "").lower() == "excluded"]
    if excluded:
        dims.append(_dim("alternatives", "Alternative hypotheses", OK,
                         f"{len(excluded)} of {len(alts)} competing explanation(s) excluded by evidence", ["C3", "C6", "C8"]))
    elif alts:
        dims.append(_dim("alternatives", "Alternative hypotheses", WARN,
                         f"{len(alts)} alternative(s) listed but none excluded yet", ["C3", "C6", "C8"]))
    elif interp:
        dims.append(_dim("alternatives", "Alternative hypotheses", GAP,
                         "interpretation-tier finding(s) present but no competing explanations "
                         "considered (add alternative_hypotheses + how the evidence discriminates)", ["C3", "C6", "C8"]))
    else:
        dims.append(_dim("alternatives", "Alternative hypotheses", GAP,
                         "no alternative hypotheses declared", ["C6"]))

    # 4. Claim discipline — observation vs mechanism vs interpretation [C3].
    #    Extended (critique #21): a finding whose claim_scope over-reaches
    #    (theoretical / generality) without robustness or generality evidence is
    #    a single-instance claim dressed as a general one — downgrade to WARN.
    if not findings:
        cd = _dim("claim_discipline", "Claim discipline", GAP,
                  "no findings recorded", ["C3"])
    elif not tiered:
        cd = _dim("claim_discipline", "Claim discipline", WARN,
                  "findings not tiered — label each observation / mechanism / interpretation", ["C3"])
    else:
        interp_no_evidence = [f for f in interp if not f.get("evidence")]
        if interp_no_evidence:
            cd = _dim("claim_discipline", "Claim discipline", GAP,
                      f"{len(interp_no_evidence)} interpretation finding(s) not linked to evidence", ["C3"])
        else:
            cd = _dim("claim_discipline", "Claim discipline", OK,
                      "findings tiered; interpretation claims carry evidence", ["C3"])
    overreaching = [
        f for f in findings
        if (f.get("claim_scope") or "").strip().lower() in ("theoretical", "generality")
        and not _has_generality_signal(spec, f)
    ]
    if overreaching and cd["severity"] == OK:
        cd = _dim("claim_discipline", "Claim discipline", WARN,
                  f"{len(overreaching)} finding(s) claim_scope=theoretical/generality but the "
                  "result is single-instance (no robustness sweep or generality axes) — narrow "
                  "the scope or add generality evidence", ["C3", "C21"])
    elif overreaching and cd["severity"] != OK:
        cd["comments"] = list(cd["comments"]) + ["C21"]
    dims.append(cd)

    # 5. Falsifiability of the bar [C5, C1] — the one authored field is
    #    study.falsifiability (no producer ever writes a per-test could_fail_if).
    has_fals = bool(str(spec.get("falsifiability") or "").strip())
    dims.append(_dim("falsifiability", "Falsifiability", OK if has_fals else GAP,
                     "a 'how this could fail' note is declared" if has_fals
                     else "criteria read as tailored-to-succeed — add a falsifiability note "
                          "(study.falsifiability)", ["C5", "C1"]))

    # 6. Engineered vs emergent [C7]
    interp_no_origin = [f for f in interp if not (f.get("mechanism_origin"))]
    if not interp:
        dims.append(_dim("mechanism_origin", "Engineered vs emergent", OK,
                         "no interpretation-tier claim that requires the distinction", ["C7"]))
    elif interp_no_origin:
        dims.append(_dim("mechanism_origin", "Engineered vs emergent", WARN,
                         f"{len(interp_no_origin)} interpretation claim(s) don't state whether the "
                         "mechanism is engineered or emergent", ["C7"]))
    else:
        dims.append(_dim("mechanism_origin", "Engineered vs emergent", OK,
                         "interpretation claims declare engineered vs emergent", ["C7"]))

    # 7. Limitations / "what this does not show" [C8, C11] — also credit the
    #    Decide-phase remaining_uncertainties (the same "what's still open").
    _di_lim = spec.get("discovery_implications") or {}
    has_lim = (_nonempty(spec.get("limitations")) or _nonempty(spec.get("does_not_show"))
               or (isinstance(_di_lim, dict) and _nonempty(_di_lim.get("remaining_uncertainties"))))
    dims.append(_dim("limitations", "Limitations stated", OK if has_lim else GAP,
                     "states what the result does not show" if has_lim
                     else "no limitations / 'what this does not show' — add a short bound on the claim "
                          "(scope/fidelity of the model, what is NOT demonstrated)", ["C8", "C11"]))

    # 8. Next steps / discovery implications [Decide-phase completeness]
    di = spec.get("discovery_implications")
    if isinstance(di, dict):
        has_di = any(_nonempty(v) for v in di.values())
    else:
        has_di = _nonempty(di)
    has_next = has_di or _nonempty(spec.get("follow_up_studies"))
    dims.append(_dim("next_steps", "Next steps", OK if has_next else GAP,
                     "declares discovery implications / follow-up studies" if has_next
                     else "no discovery_implications or follow_up_studies — state what this study "
                          "changes and what to investigate next (the Decide phase)", ["next-steps"]))

    # 9. Pre-registration [C1; confirmatory studies only] (critique #18) — a
    #    confirmatory study that passed on criteria registered only AFTER the run
    #    reads as post-hoc; only added for confirmatory studies so the dimension
    #    count for every other study type is unchanged (back-compat).
    study_type = _study_type(spec)
    if study_type == "confirmatory":
        try:
            from .study_verdict import preregistration_status, roll_up_verdict
            prereg = preregistration_status(spec)
            verdict = roll_up_verdict(spec)
        except Exception:  # noqa: BLE001 — never let the import sink the scorecard
            prereg, verdict = {}, {}
        passed = str((verdict or {}).get("result") or "").lower() == "passed"
        if not prereg.get("preregistered"):
            dims.append(_dim("preregistration", "Pre-registration", WARN,
                             "confirmatory study has no preregistered block — the criteria "
                             "can't be shown to predate the run (add a preregistered: block "
                             "with registered_at + thresholds)", ["C1"]))
        elif passed and prereg.get("registered_before_run") is not True:
            dims.append(_dim("preregistration", "Pre-registration", WARN,
                             "confirmatory pass on post-hoc criteria — registered_before_run "
                             "is not established (pre-register criteria before running)", ["C1"]))
        else:
            dims.append(_dim("preregistration", "Pre-registration", OK,
                             "confirmatory criteria pre-registered before the run", ["C1"]))

    # 10. Threshold provenance (critique #9) — a numeric acceptance band should
    #     say WHERE its cutoff came from: a literature ``cites`` link OR an
    #     honest ``pass_if.provenance.kind``. An unsourced band reads as
    #     tailored-to-succeed.
    band_tests = _numeric_band_tests(spec)
    if not band_tests:
        dims.append(_dim("threshold_provenance", "Threshold provenance", OK,
                         "no numeric acceptance bands requiring provenance", ["C9", "C5"]))
    else:
        unsourced = [t for t in band_tests if not _test_threshold_sourced(t)]
        if unsourced:
            dims.append(_dim("threshold_provenance", "Threshold provenance", GAP,
                             f"{len(unsourced)} of {len(band_tests)} numeric band(s) declare neither "
                             "cites nor pass_if.provenance.kind — state where the cutoff came from "
                             "(theory/calibration/literature/expert/exploratory/post_hoc)", ["C9", "C5"]))
        else:
            dims.append(_dim("threshold_provenance", "Threshold provenance", OK,
                             f"all {len(band_tests)} numeric band(s) carry a source (cites or "
                             "pass_if.provenance.kind)", ["C9", "C5"]))

    # 11. Metric calibration ladder (critique #20) — a metric is calibrated when
    #     known-fail / known-pass / borderline / stress rungs map to controls
    #     across its range, not merely a single asserted cutoff.
    ladders = _calibration_ladders(spec)
    if not ladders:
        dims.append(_dim("metric_calibration", "Metric calibration ladder", GAP,
                         "no calibration_ladder declared — index controls[] by known_fail / "
                         "known_pass / borderline / stress rungs so the metric is calibrated across "
                         "its range, not just asserted", ["C4", "C2", "C20"]))
    else:
        control_names = {str(c.get("name")) for c in
                         _as_list(spec.get("controls")) if isinstance(c, dict) and c.get("name")}
        # Severity = the best (most-filled) ladder, so a single well-calibrated
        # metric is credited; detail enumerates each.
        best_sev = GAP
        details: list[str] = []
        for lad in ladders:
            sev, det = _ladder_severity(lad, control_names)
            details.append(det)
            if _SEVERITY_RANK[sev] > _SEVERITY_RANK[best_sev]:
                best_sev = sev
        dims.append(_dim("metric_calibration", "Metric calibration ladder", best_sev,
                         "; ".join(details), ["C4", "C2", "C20"]))

    # 12. Generality (critique #22) — how many INDEPENDENT axes the finding's
    #     robustness was probed along (parameter regime, initial conditions,
    #     discretization, geometry, alt implementation, independent authoring).
    axes = _study_generality_axes(spec)
    if len(axes) >= 2:
        dims.append(_dim("generality", "Generality", OK,
                         f"{len(axes)} independent generality axis(es) tested: {sorted(axes)}",
                         ["C22"]))
    elif len(axes) == 1:
        dims.append(_dim("generality", "Generality", WARN,
                         f"only one generality axis tested ({sorted(axes)[0]}) — a single sweep is not "
                         "generality; vary initial conditions / discretization / implementation too",
                         ["C22"]))
    else:
        dims.append(_dim("generality", "Generality", GAP,
                         "no generality axes tested — add findings[].generality.axes_tested or a "
                         "robustness parameter sweep so the claim's breadth is evidenced", ["C22"]))

    # 13. Run persistence / emitter coverage — a run is reproducible only if its
    #     trajectory was PERSISTED, not merely summarised. A run record evidences
    #     this by carrying an ``emitter`` (sqlite/parquet/xarray) or a run-db
    #     reference (see :func:`run_is_emitter_backed`). Not-applicable (OK) when
    #     the study records no runs; GAP when it has runs but none are
    #     emitter-backed.
    runs = [r for r in _as_list(spec.get("runs")) if isinstance(r, dict)]
    persisted = [r for r in runs if run_is_emitter_backed(r)]
    if not runs:
        dims.append(_dim("run_persistence", "Run persistence", OK,
                         "no runs recorded — nothing to persist via an emitter", ["persistence"]))
    elif persisted:
        dims.append(_dim("run_persistence", "Run persistence", OK,
                         f"{len(persisted)}/{len(runs)} run(s) persisted via an emitter "
                         "(sqlite/parquet/xarray or a run-db reference)", ["persistence"]))
    else:
        dims.append(_dim("run_persistence", "Run persistence", GAP,
                         f"{len(runs)} run(s) recorded but none carry an emitter "
                         "(sqlite/parquet/xarray) or a run-db reference — the trajectories are "
                         "not persisted; runs should emit via the workspace emitter", ["persistence"]))

    # 14. G2 — Statistical power for causal stochastic claims. Conditionally
    #     appended (like the confirmatory-only preregistration dim) so it only
    #     bites causal/directional claims derived from a stochastic contrast
    #     between arms — deterministic and non-causal studies are untouched.
    #     Sharpened bar above the ≥3-seed replication floor: n≥20 per arm, a
    #     declared rank/nonparametric test with a p-value, a drift-null control
    #     arm, and a gate on the ENSEMBLE statistic (not one flagship seed).
    if _causal_stochastic_claim(spec):
        stats = _study_statistics(spec)
        n_arm = stats.get("n_per_arm")
        if not isinstance(n_arm, int) or isinstance(n_arm, bool):
            n_arm = n_rep
        missing: list[str] = []
        if n_arm < _CAUSAL_MIN_N_PER_ARM:
            missing.append(f"n>={_CAUSAL_MIN_N_PER_ARM} replicates per arm "
                           f"(found {n_arm} — the >=3 replication floor is not "
                           "statistical power)")
        has_test = bool(str(stats.get("test") or "").strip())
        p_val = stats.get("p_value")
        has_p = isinstance(p_val, (int, float)) and not isinstance(p_val, bool)
        if not (has_test and has_p):
            missing.append("a declared rank/nonparametric test with a p-value "
                           "(statistics: {test, p_value, effect_size})")
        if not _has_drift_null_arm(spec, stats):
            missing.append("a drift-null / no-effect control arm")
        gate = _gate_target(spec, stats)
        single_seed_gate = bool(gate) and gate not in _ENSEMBLE_GATE_VALUES and (
            "seed" in gate or "flagship" in gate)
        if gate not in _ENSEMBLE_GATE_VALUES:
            missing.append("gate on the ensemble statistic (gate_on: ensemble), "
                           "not a single flagship seed")
        if not missing:
            dims.append(_dim("statistical_power", "Statistical power (causal stochastic claim)",
                             OK,
                             f"causal contrast powered: n>={_CAUSAL_MIN_N_PER_ARM} per arm, "
                             f"{stats.get('test')} test with p-value, drift-null arm, "
                             "gate on the ensemble statistic", ["G2", "C4"]))
        elif single_seed_gate:
            detail = (f"causal claim gated on a single seed (gate_on: {gate}) — gate the "
                      "pass/fail on the ENSEMBLE statistic")
            other = [m for m in missing if "ensemble" not in m]
            if other:
                detail += "; also missing: " + "; ".join(other)
            dims.append(_dim("statistical_power", "Statistical power (causal stochastic claim)",
                             GAP, detail, ["G2", "C4"]))
        elif not (has_test and has_p):
            dims.append(_dim("statistical_power", "Statistical power (causal stochastic claim)",
                             GAP,
                             "causal claim from a stochastic contrast with no declared statistical "
                             "test — missing: " + "; ".join(missing), ["G2", "C4"]))
        else:
            dims.append(_dim("statistical_power", "Statistical power (causal stochastic claim)",
                             WARN,
                             "statistical test declared but the power bar is not met — missing: "
                             + "; ".join(missing), ["G2", "C4"]))

    # 15. G3 — Held-out generalization. Conditionally appended for
    #     substitutability / equivalence / surrogate claims only (detected from a
    #     declared claim_type or a clear finding-text signal; no claim → silent).
    #     A surrogate that only agrees on the condition it was tuned on is
    #     calibration, not mechanism-independence: require a declared train vs
    #     HELD-OUT test condition plus a degrees-of-freedom-vs-constraints
    #     statement (free params vs matched observables).
    if _equivalence_claim_present(spec):
        ho = _held_out_block(spec)
        train, test = _held_out_conditions(ho)
        has_dof = _dof_statement(spec)
        if not test:
            dims.append(_dim("held_out_generalization", "Held-out generalization", GAP,
                             "substitutability/equivalence claim with no held-out condition "
                             "declared — agreement on the tuned condition alone is surrogate "
                             "calibration, not yet mechanism-independence (declare "
                             "held_out: {train, test} with a test condition the surrogate was "
                             "NOT tuned on, plus degrees_of_freedom: free params vs matched "
                             "observables)", ["G3"]))
        elif train and test <= train:
            dims.append(_dim("held_out_generalization", "Held-out generalization", WARN,
                             "held-out test condition(s) are the tuned (train) condition(s) — "
                             "surrogate calibration, not yet mechanism-independence; evaluate on "
                             "a condition the surrogate was NOT tuned on", ["G3"]))
        elif not has_dof:
            dims.append(_dim("held_out_generalization", "Held-out generalization", WARN,
                             "held-out condition declared but no degrees-of-freedom-vs-constraints "
                             "statement — declare degrees_of_freedom: {free_parameters, "
                             "matched_observables} so readers can judge how constrained the "
                             "agreement is", ["G3"]))
        else:
            dims.append(_dim("held_out_generalization", "Held-out generalization", OK,
                             f"held-out test condition(s) {sorted(test)} distinct from tuned "
                             f"{sorted(train) if train else '(undeclared)'} + a declared "
                             "degrees-of-freedom-vs-constraints statement", ["G3"]))

    # 16. G6 — Conservation ledger across representation conversions.
    #     Conditionally appended, and detection stays conservative: only a study
    #     that DECLARES a conversion of a conserved quantity between
    #     representations (representation_conversion field, or a clear
    #     finding-text signal) is flagged — everything else is silent. When a
    #     conversion is declared, require a ledger check asserting the quantity
    #     is conserved (not manufactured or lost) across the conversion.
    conversions = _representation_conversions(spec)
    if conversions or _conversion_text_signal(spec):
        if not conversions:
            dims.append(_dim("conservation_ledger", "Conservation ledger", GAP,
                             "findings describe a representation conversion but no "
                             "representation_conversion is declared — declare {from, to, "
                             "quantity, ledger: {test, result}} so the conserved quantity is "
                             "checked, not manufactured, across the conversion", ["G6"]))
        else:
            unledgered = []
            unverified = []
            for conv in conversions:
                named, verified = _ledger_named_and_verified(_conversion_ledger(conv, spec))
                label = (f"{conv.get('from') or '?'}->{conv.get('to') or '?'} "
                         f"({conv.get('quantity') or 'quantity?'})")
                if not named:
                    unledgered.append(label)
                elif not verified:
                    unverified.append(label)
            if unledgered:
                dims.append(_dim("conservation_ledger", "Conservation ledger", GAP,
                                 f"{len(unledgered)} of {len(conversions)} representation "
                                 f"conversion(s) declare no conservation-ledger check "
                                 f"({'; '.join(unledgered)}) — add a ledger test asserting the "
                                 "quantity is conserved across the conversion", ["G6"]))
            elif unverified:
                dims.append(_dim("conservation_ledger", "Conservation ledger", WARN,
                                 f"ledger check named but no recorded passing result for "
                                 f"{'; '.join(unverified)} — run the ledger and record "
                                 "result: PASS", ["G6"]))
            else:
                dims.append(_dim("conservation_ledger", "Conservation ledger", OK,
                                 f"all {len(conversions)} representation conversion(s) carry a "
                                 "verified conservation-ledger check", ["G6"]))

    # Mode-awareness: for a descriptive / informational study the hypothesis-test
    # dimensions don't apply — relabel them NA so they neither score as gaps nor
    # inflate the "addressed" count. The dimensions that ARE meaningful for a
    # reference deliverable (limitations / completeness, next steps, run
    # persistence) keep their computed severity.
    descriptive = is_descriptive_study(spec)
    if descriptive:
        for d in dims:
            if d["id"] in _HYPOTHESIS_TEST_DIMS:
                d["severity"] = NA
                d["detail"] = ("not applicable — descriptive reference (no hypothesis "
                               "under test); hypothesis-test rigor is not scored")

    score = {GAP: 0, WARN: 0, OK: 0}
    na = 0
    for d in dims:
        if d["severity"] == NA:
            na += 1
            continue
        score[d["severity"]] = score.get(d["severity"], 0) + 1
    addressed = score[OK]
    total = len(dims) - na  # applicable (scored) dimensions only
    if descriptive:
        summary = "descriptive reference — hypothesis-test rigor not applicable"
        if total:
            summary += f"; {addressed}/{total} reference dimension(s) addressed"
        if score[GAP]:
            summary += f" · {score[GAP]} gap(s)"
    else:
        summary = (f"{addressed}/{total} rigor dimensions addressed"
                   + (f" · {score[GAP]} gap(s)" if score[GAP] else ""))
    return {
        "study_type": study_type,
        "mode": "descriptive" if descriptive else "hypothesis",
        "descriptive": descriptive,
        "dimensions": dims,
        "score": {"gap": score[GAP], "warn": score[WARN], "ok": score[OK],
                  "na": na, "total": total},
        "summary": summary,
    }


# ---------------------------------------------------------------------------
# Per-finding evidential weight (item 8) — a strong/moderate/weak chip per
# finding, computed by REUSING the study-level rigor predicates restricted to
# the one finding (degrading to study-level signals when it can't be matched).
# ---------------------------------------------------------------------------

def _finding_match_tokens(finding: dict) -> set[str]:
    """Lower-cased identifiers a finding can be matched on — keyed primarily by
    ``evidence.from_test`` (per the contract), plus the finding's measure/name."""
    toks: set[str] = set()
    ev = finding.get("evidence")
    if isinstance(ev, dict):
        for k in ("from_test", "measure", "observable"):
            v = ev.get(k)
            if isinstance(v, str) and v.strip():
                toks.add(v.strip().lower())
    for k in ("measure", "id", "name", "test"):
        v = finding.get(k)
        if isinstance(v, str) and v.strip():
            toks.add(v.strip().lower())
    return toks


def _finding_divergence_present(finding: dict) -> bool:
    """Effect-size signal: ``calibration_anchor.divergence_factor`` (or the
    mirrored ``evidence.divergence_factor``) is present and meaningfully > 0.
    No recompute — reads the value the finding-observations pass already wrote."""
    for src in (finding.get("calibration_anchor"), finding.get("evidence")):
        if isinstance(src, dict):
            df = src.get("divergence_factor")
            try:
                if df is not None and abs(float(df)) > 0:
                    return True
            except (TypeError, ValueError):
                pass
    return False


def _control_matches(control: dict, tokens: set[str]) -> bool:
    if not tokens:
        return False
    hay = " ".join(str(control.get(k) or "") for k in
                   ("name", "test", "for_test", "discriminates", "hypothesis", "measure")).lower()
    return any(t in hay for t in tokens)


def _excluded_alternatives(spec: dict) -> list[dict]:
    """The excluded competing explanations — same source preference as the
    ``alternatives`` rigor dimension (DI-synthesis preferred, top-level fallback)."""
    _di = spec.get("discovery_implications") or {}
    alts: list[dict] = []
    if isinstance(_di, dict):
        alts = [a for a in _as_list(_di.get("alternate_hypotheses")) if isinstance(a, dict)]
    if not alts:
        alts = [a for a in _as_list(spec.get("alternative_hypotheses")) if isinstance(a, dict)]
    return [a for a in alts if (a.get("status") or "").lower() == "excluded"]


def _alt_matches(alt: dict, tokens: set[str]) -> bool:
    if not tokens:
        return False
    hay = " ".join(str(alt.get(k) or "") for k in
                   ("discriminated_by", "discriminator", "discriminates", "claim")).lower()
    return any(t in hay for t in tokens)


def finding_evidential_weight(spec: dict, finding: dict) -> dict:
    """Per-finding evidential weight (item 8).

    Returns ``{"weight": "strong"|"moderate"|"weak", "dims": {...}, "n_supporting": int}``
    where ``dims`` carries the five booleans ``replication, effect_size,
    control_strength, independence, alternatives``.

    Pure and tolerant. Every dimension REUSES an existing study-level predicate,
    restricted to the finding via a TOLERANT matcher keyed on
    ``finding['evidence']['from_test']``; when a finding can't be matched to
    per-finding inputs it degrades to the study-level signal (never mislabels).
    """
    spec = spec or {}
    finding = finding or {}
    tokens = _finding_match_tokens(finding)

    # replication — _replication_agreement restricted to the finding's measure;
    # degrade to the full study robustness block when no per-measure matches.
    n_rep, sweep = _replicate_count(spec)
    rob = spec.get("robustness")
    rep_spec = spec
    if isinstance(rob, dict) and tokens:
        per = [m for m in _as_list(rob.get("per_measure"))
               if isinstance(m, dict) and str(m.get("name") or "").lower() in tokens]
        if per:
            rep_spec = {**spec, "robustness": {**rob, "per_measure": per}}
    disagrees, _ = _replication_agreement(rep_spec, n_rep)
    replication = (sweep or n_rep >= 3) and not disagrees

    # effect_size — the finding's recorded divergence_factor (no recompute).
    effect_size = _finding_divergence_present(finding)

    # control_strength — reuse the discriminating-control filter (PASS + non-empty
    # observed), matched to this finding's test; degrade to "any discriminating
    # control in the study" when none names this test.
    controls = [c for c in _as_list(spec.get("controls")) if isinstance(c, dict)]
    negs = [c for c in controls if (c.get("kind") or "").lower() in ("negative", "adversarial")]
    discriminating = [c for c in negs
                      if str(c.get("result", "")).upper() == "PASS" and _nonempty(c.get("observed"))]
    matched_controls = [c for c in discriminating if _control_matches(c, tokens)]
    control_strength = bool(matched_controls) if matched_controls else bool(discriminating)

    # independence — emergent (not engineered) interpretation.
    independence = (finding.get("mechanism_origin") or "").lower() == "emergent"

    # alternatives — an excluded competing explanation whose discriminator names
    # this finding's test; degrade to "any excluded alternative in the study".
    excluded = _excluded_alternatives(spec)
    matched_alts = [a for a in excluded if _alt_matches(a, tokens)]
    alternatives = bool(matched_alts) if matched_alts else bool(excluded)

    dims = {
        "replication": bool(replication),
        "effect_size": bool(effect_size),
        "control_strength": bool(control_strength),
        "independence": bool(independence),
        "alternatives": bool(alternatives),
    }
    n_supporting = sum(1 for v in dims.values() if v)
    if n_supporting >= 4:
        weight = "strong"
    elif n_supporting >= 2:
        weight = "moderate"
    else:
        weight = "weak"
    return {"weight": weight, "dims": dims, "n_supporting": n_supporting}


def investigation_rigor(inv_spec: dict, study_specs: list[dict]) -> dict:
    """Roll study rigor up to the investigation, plus investigation-level
    dimensions (adversarial coverage, methodology strength).

    ``study_specs`` is the list of member study specs (any order). Returns
    ``{per_study: {slug: scorecard}, dimensions: [...], score: {...}, summary}``.
    """
    inv_spec = inv_spec or {}
    study_specs = study_specs or []

    per_study: dict[str, dict] = {}
    for s in study_specs:
        slug = (s or {}).get("name") or (s or {}).get("slug") or f"study-{len(per_study)}"
        per_study[slug] = study_rigor(s or {})

    dims: list[dict] = []

    # Adversarial coverage [C10] — generalized through _study_type (critique #10).
    adversarial = [s for s in study_specs if _study_type(s) == "adversarial"]
    if adversarial:
        dims.append(_dim("adversarial_coverage", "Adversarial testing", OK,
                         f"{len(adversarial)} adversarial study(ies) designed to break the framework", ["C10", "C12", "C15"]))
    else:
        dims.append(_dim("adversarial_coverage", "Adversarial testing", GAP,
                         "no adversarial study — add one that tries to BREAK the criteria: "
                         "mimic / parasitic-or-dependent / externally-maintained / random-cyclic "
                         "systems that should NOT qualify", ["C10", "C12", "C15"]))

    # Methodology strength [C9, C2, C14] — informational positive headline; the
    # reusable methodological contribution the reviewers single out.
    has_dag = any("pipeline_gate" in (s or {}) for s in study_specs)
    has_ac = bool(_as_list(inv_spec.get("acceptance_criteria")))
    if has_dag and has_ac:
        dims.append(_dim("methodology", "Traceable methodology", OK,
                         "capability ladder (study DAG) + explicit acceptance criteria + pass/fail "
                         "gates + traceable findings — the reusable methodological contribution", ["C9", "C2", "C14"]))

    # Falsification exposure [C1] — has the framework ever been seen to reject
    # something? All-pass with no failing control reads as confirmation-only.
    def _passed(s):
        pg = (s or {}).get("pipeline_gate") or {}
        ge = (pg.get("gate_evaluator") or {}) if isinstance(pg, dict) else {}
        res = str(ge.get("result") or (s or {}).get("gate_status") or "").lower()
        return res in ("passed", "pass")

    def _has_discriminating_negative(s):
        for c in _as_list((s or {}).get("controls")):
            if (isinstance(c, dict)
                    and (c.get("kind") or "").lower() in ("negative", "adversarial")
                    and str(c.get("result", "")).upper() == "PASS"
                    and _nonempty(c.get("observed"))):  # item 15: must have run
                return True
        return False

    # An exploratory study OBSERVES rather than tests a hypothesis (critique
    # #10): its passing tests are observations, not falsification credit, so it
    # is excluded from the "did everything just pass?" reasoning.
    non_exploratory = [s for s in study_specs if _study_type(s) != "exploratory"]
    all_passed = bool(non_exploratory) and all(_passed(s) for s in non_exploratory)
    visible_failure = (bool(adversarial)
                       or any(_has_discriminating_negative(s) for s in study_specs)
                       or (bool(non_exploratory) and not all_passed))
    if visible_failure:
        dims.append(_dim("falsification_exposure", "Falsification exposure", OK,
                         "the framework has been shown to reject at least one system (a discriminating "
                         "negative control, an adversarial study, or a non-passing result)", ["C1"]))
    else:
        dims.append(_dim("falsification_exposure", "Falsification exposure", GAP,
                         "every study passes and nothing was shown to fail — the framework was not "
                         "visibly exposed to falsification (add a control that fails, an adversarial "
                         "study, or report a non-passing result)", ["C1"]))

    # Competing theoretical frameworks [C13]
    cf = _as_list(inv_spec.get("competing_frameworks"))
    if cf:
        dims.append(_dim("comparative_framing", "Comparative framing", OK,
                         f"{len(cf)} competing theoretical framework(s) compared", ["C13"]))
    else:
        dims.append(_dim("comparative_framing", "Comparative framing", GAP,
                         "no competing theoretical frameworks compared (viability theory, organizational / "
                         "constraint closure, active inference) — show the findings uniquely support this lens", ["C13"]))

    # Hypothesis competition (critique #6 + #16) — did the investigation put
    # forward ≥2 competing hypotheses AND accumulate evidence against each? An
    # investigation with one (or zero) hypotheses is not adjudicating between
    # rival explanations. Uses the deterministic support roll-up (defensive
    # import so a missing module degrades to GAP, not a crash).
    hyps = [h for h in _as_list(inv_spec.get("hypotheses")) if isinstance(h, dict)]
    if not hyps:
        dims.append(_dim("hypothesis_competition", "Hypothesis competition", GAP,
                         "no competing hypotheses[] declared — state ≥2 rival explanations with "
                         "predictions so the evidence can adjudicate between them", ["C6", "C16"]))
    else:
        n_with_support = 0
        try:
            from .hypotheses import compute_support_log
            for h in hyps:
                if compute_support_log(h, study_specs):
                    n_with_support += 1
        except Exception:  # noqa: BLE001 — defensive cross-module import
            n_with_support = 0
        if len(hyps) >= 2 and n_with_support >= 2:
            dims.append(_dim("hypothesis_competition", "Hypothesis competition", OK,
                             f"{len(hyps)} competing hypotheses, {n_with_support} with evidence "
                             "in their support_log", ["C6", "C16"]))
        elif len(hyps) >= 2:
            dims.append(_dim("hypothesis_competition", "Hypothesis competition", WARN,
                             f"{len(hyps)} hypotheses declared but only {n_with_support} carry "
                             "support_log evidence — link study findings / alternate_hypotheses to them",
                             ["C6", "C16"]))
        else:
            dims.append(_dim("hypothesis_competition", "Hypothesis competition", WARN,
                             "only one hypothesis declared — add a competitor so the evidence "
                             "adjudicates between rivals", ["C6", "C16"]))

    # Aggregate the worst per-study gap count as an investigation signal.
    study_gaps = sum(sc["score"]["gap"] for sc in per_study.values())
    if study_gaps:
        dims.append(_dim("study_rigor_gaps", "Per-study rigor gaps", WARN if study_gaps < 4 else GAP,
                         f"{study_gaps} rigor gap(s) across {len(per_study)} member study(ies)", ["C2", "C4", "C6"]))
    elif per_study:
        dims.append(_dim("study_rigor_gaps", "Per-study rigor gaps", OK,
                         "member studies have no rigor gaps", []))

    score = {GAP: 0, WARN: 0, OK: 0}
    for d in dims:
        score[d["severity"]] = score.get(d["severity"], 0) + 1
    return {
        # Critique #1: this scorecard measures the METHOD, not the model. Make
        # that intent explicit and distinct from the per-study model verdicts.
        "intent": "how well the METHOD defends its claims (method-level, "
                  "distinct from the per-study model verdicts)",
        "per_study": per_study,
        "dimensions": dims,
        "score": {"gap": score[GAP], "warn": score[WARN], "ok": score[OK], "total": len(dims)},
        "summary": f"{score[OK]}/{len(dims)} investigation rigor dimensions addressed"
                   + (f" · {score[GAP]} gap(s)" if score[GAP] else ""),
    }


# ---------------------------------------------------------------------------
# Framework-self metrics (critique #26) — aggregate the EXISTING rigor fields
# across many studies / investigations into a single scorecard. Each metric is
# a ``{fraction, count, total}`` triple (fraction is None when total == 0).
# Pure, deterministic, and tolerant of missing inputs.
# ---------------------------------------------------------------------------


def _frac(count: int, total: int) -> dict:
    return {
        "fraction": (count / total) if total else None,
        "count": count,
        "total": total,
    }


def _has_discriminating_control(spec: dict) -> bool:
    """≥1 negative/adversarial control that ran and recorded a PASS (reuses the
    same ``discriminating`` predicate as :func:`study_rigor`)."""
    for c in _as_list((spec or {}).get("controls")):
        if (isinstance(c, dict)
                and (c.get("kind") or "").lower() in ("negative", "adversarial")
                and str(c.get("result", "")).upper() == "PASS"
                and _nonempty(c.get("observed"))):
            return True
    return False


def _study_passed(spec: dict) -> bool:
    pg = (spec or {}).get("pipeline_gate") or {}
    ge = (pg.get("gate_evaluator") or {}) if isinstance(pg, dict) else {}
    res = str(ge.get("result") or (spec or {}).get("gate_status") or "").lower()
    return res in ("passed", "pass")


def _study_exposes_falsification(spec: dict) -> bool:
    """A study contributes falsification exposure when it is adversarial, carries
    a discriminating negative control, or (being non-exploratory) did NOT pass."""
    st = _study_type(spec)
    if st == "adversarial":
        return True
    if _has_discriminating_control(spec):
        return True
    if st != "exploratory" and not _study_passed(spec):
        return True
    return False


def framework_metrics(study_specs: list[dict], inv_specs: list[dict]) -> dict:
    """Aggregate existing rigor fields across studies/investigations (critique #26).

    ``study_specs`` — member study specs (any order). ``inv_specs`` —
    investigation specs (for AC coverage). Returns a dict of
    ``{fraction, count, total}`` metrics plus ``n_studies`` / ``n_investigations``.
    Pure; tolerant of ``None`` / empty inputs (a metric over an empty set has
    ``fraction: None``). Reuses the existing predicates so it can't drift from
    the per-study / per-investigation scorecards.
    """
    study_specs = [s for s in (study_specs or []) if isinstance(s, dict)]
    inv_specs = [i for i in (inv_specs or []) if isinstance(i, dict)]
    n_studies = len(study_specs)

    # 1. Discriminating negative/adversarial control coverage.
    n_disc = sum(1 for s in study_specs if _has_discriminating_control(s))

    # 2. Interpretation-tier findings: emergent vs missing mechanism_origin.
    interp_total = 0
    emergent = 0
    missing_origin = 0
    for s in study_specs:
        for f in _findings(s):
            if (f.get("tier") or "").lower() != "interpretation":
                continue
            interp_total += 1
            origin = (f.get("mechanism_origin") or "").strip().lower()
            if origin == "emergent":
                emergent += 1
            elif not origin:
                missing_origin += 1

    # 3. Threshold-provenance coverage — behavior_tests / tests carrying a
    #    quantitative band (pass_if or calibration_anchor) that also declare a
    #    source (cites or calibration_anchor).
    band_total = 0
    band_cited = 0
    for s in study_specs:
        for section in ("behavior_tests", "tests"):
            for t in _as_list(s.get(section)):
                if not isinstance(t, dict):
                    continue
                if not (t.get("pass_if") or t.get("calibration_anchor")):
                    continue
                band_total += 1
                # A band is "sourced" by a literature link (cites /
                # calibration_anchor) OR an honest pass_if.provenance.kind (#9).
                if _test_threshold_sourced(t):
                    band_cited += 1

    # 4. Replication coverage — ≥3 replicates (reuse _replicate_count).
    n_replicated = sum(1 for s in study_specs if _replicate_count(s)[0] >= 3)

    # 5. AC coverage — 1 - |gaps|/|criteria| over all investigations' acceptance
    #    criteria (a criterion is a gap when it has no ``study:`` link — the same
    #    predicate linkage_index.ac_gating_matrix uses).
    ac_total = 0
    ac_covered = 0
    for inv in inv_specs:
        for crit in _as_list(inv.get("acceptance_criteria")):
            if not isinstance(crit, dict):
                continue
            ac_total += 1
            if crit.get("study"):
                ac_covered += 1

    # 6. Verdict-divergence rate (reuse study_verdict.diverges_from_authored).
    n_divergent = 0
    try:
        from .study_verdict import diverges_from_authored
        n_divergent = sum(1 for s in study_specs if diverges_from_authored(s))
    except Exception:  # noqa: BLE001 — defensive cross-module import
        pass

    # 7. Falsification-exposure rate.
    n_exposed = sum(1 for s in study_specs if _study_exposes_falsification(s))

    # 8. Alternatives-excluded rate.
    n_alts_excluded = sum(1 for s in study_specs if _excluded_alternatives(s))

    # 9. Emitter coverage — of the studies that record runs, the fraction whose
    #    runs are emitter-backed (≥1 run carries an emitter / run-db reference).
    #    Denominator is studies-with-runs (a study with no runs is not a
    #    persistence candidate), mirroring threshold_provenance's band-scoped rate.
    n_studies_with_runs = 0
    n_emitter_backed = 0
    for s in study_specs:
        runs = [r for r in _as_list(s.get("runs")) if isinstance(r, dict)]
        if not runs:
            continue
        n_studies_with_runs += 1
        if any(run_is_emitter_backed(r) for r in runs):
            n_emitter_backed += 1

    return {
        "n_studies": n_studies,
        "n_investigations": len(inv_specs),
        "discriminating_controls": _frac(n_disc, n_studies),
        "emergent_interpretations": _frac(emergent, interp_total),
        "missing_mechanism_origin": _frac(missing_origin, interp_total),
        "threshold_provenance": _frac(band_cited, band_total),
        "replication_coverage": _frac(n_replicated, n_studies),
        "ac_coverage": _frac(ac_covered, ac_total),
        "verdict_divergence": _frac(n_divergent, n_studies),
        "falsification_exposure": _frac(n_exposed, n_studies),
        "alternatives_excluded": _frac(n_alts_excluded, n_studies),
        "emitter_coverage": _frac(n_emitter_backed, n_studies_with_runs),
    }
