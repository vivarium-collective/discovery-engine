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
can call it directly. Mirrors :mod:`discovery_engine.study.verdict` in spirit:
pure ``spec -> dict`` functions.

Package layout
--------------
This was a single ``rigor.py`` module; it is now a cohesive sub-package. The
entire former public surface is re-exported here, so
``from discovery_engine.reporting.rigor import X`` keeps working for every ``X``
(including the underscore-prefixed helpers). Submodules by concern:

* :mod:`~.common` — severity / study-type / emitter vocabulary and the generic
  pure helpers reused everywhere (``_as_list`` / ``_findings`` / ``_dim`` /
  ``_replicate_count`` / ``run_is_emitter_backed`` / …).
* :mod:`~.bands` — threshold provenance + sensitivity (numeric acceptance bands).
* :mod:`~.study_rigor` — the per-study scorecard and its dimension helpers
  (calibration ladder, generality, descriptive mode, G2/G3/G6 review dims).
* :mod:`~.evidence_weight` — per-finding evidential weight.
* :mod:`~.investigation_rigor` — the investigation-level roll-up.
* :mod:`~.framework_metrics` — framework-self aggregate metrics.

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

from discovery_engine.reporting.rigor.bands import (
    THRESHOLD_PROVENANCE_KINDS,
    _has_numeric_band,
    _numeric_band_tests,
    _observed_satisfies,
    _study_test_entries,
    _test_threshold_sourced,
    threshold_sensitivity,
)
from discovery_engine.reporting.rigor.common import (
    EMITTER_KINDS,
    GAP,
    NA,
    OK,
    STUDY_TYPES,
    WARN,
    _HIGH_CV,
    _RUN_DB_REF_KEYS,
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
from discovery_engine.reporting.rigor.evidence_weight import (
    _alt_matches,
    _control_matches,
    _excluded_alternatives,
    _finding_divergence_present,
    _finding_match_tokens,
    finding_evidential_weight,
)
from discovery_engine.reporting.rigor.framework_metrics import (
    _frac,
    _has_discriminating_control,
    _study_exposes_falsification,
    _study_passed,
    framework_metrics,
)
from discovery_engine.reporting.rigor.investigation_rigor import investigation_rigor
from discovery_engine.reporting.rigor.study_rigor import (
    CLAIM_SCOPES,
    GENERALITY_AXES,
    GENERALITY_LEVELS,
    _CAUSAL_CLAIM_TYPES,
    _CAUSAL_MIN_N_PER_ARM,
    _CONSERVED_QUANTITY_TOKENS,
    _DESCRIPTIVE_VERDICTS,
    _DRIFT_NULL_KINDS,
    _DRIFT_NULL_TOKENS,
    _ENSEMBLE_GATE_VALUES,
    _EQUIVALENCE_CLAIM_TYPES,
    _EQUIVALENCE_TEXT_SIGNALS,
    _HYPOTHESIS_TEST_DIMS,
    _LADDER_RUNGS,
    _authored_verdict,
    _calibration_ladders,
    _causal_stochastic_claim,
    _conversion_ledger,
    _conversion_text_signal,
    _declares_arm_contrast,
    _dof_statement,
    _equivalence_claim_present,
    _finding_claim_type,
    _finding_generality_axes,
    _gate_target,
    _has_acceptance,
    _has_drift_null_arm,
    _has_generality_signal,
    _held_out_block,
    _held_out_conditions,
    _ladder_severity,
    _ledger_named_and_verified,
    _representation_conversions,
    _study_generality_axes,
    _study_statistics,
    _study_swept,
    is_descriptive_study,
    study_rigor,
)

__all__ = [
    # vocabulary / constants
    "GAP", "WARN", "OK", "NA", "STUDY_TYPES", "CLAIM_SCOPES", "GENERALITY_AXES",
    "GENERALITY_LEVELS", "THRESHOLD_PROVENANCE_KINDS", "EMITTER_KINDS",
    # public functions
    "run_is_emitter_backed", "threshold_sensitivity", "is_descriptive_study",
    "study_rigor", "finding_evidential_weight", "investigation_rigor",
    "framework_metrics",
]
