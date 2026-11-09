"""Per-study rigor scorecard (:func:`study_rigor`) and its dimension helpers.

Covers the calibration-ladder (critique #20), generality (critique #22),
descriptive-mode awareness, and the conditionally-appended review-integration
dimensions (G2 statistical power / G3 held-out generalization / G6 conservation
ledger), all feeding the mode-aware ``study_rigor`` roll-up.
"""
from __future__ import annotations

from typing import Any

from discovery_engine.reporting.rigor.bands import (
    _numeric_band_tests,
    _test_threshold_sourced,
)
from discovery_engine.reporting.rigor.common import (
    GAP,
    NA,
    OK,
    WARN,
    _SEVERITY_RANK,
    _as_list,
    _dim,
    _findings,
    _nonempty,
    _replicate_count,
    _replication_agreement,
    _study_type,
    run_is_emitter_backed,
)

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
            from discovery_engine.study.verdict import preregistration_status, roll_up_verdict
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
