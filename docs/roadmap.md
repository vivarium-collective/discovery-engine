# discovery-engine roadmap — the eight-stage scientific cycle

DE2.0 shipped Phases A–F + N+2 (see the Shipped section below and `architecture.md`).
This tracks the remaining gap between the engine and the full eight-stage
scientific discovery cycle (the *Scientific Discovery Cycle Technical
Specification & Requirements*, 2026-10-28). Each item carries its FR/AC id, a
status, and the sub-package it will land in.

Status: ✅ present · ◑ partial · ⬜ missing

## What exists today (the extracted foundation)
Stages **1 assess**, **5 execute**, **8 persist/renew**, and the separation
invariants are real:
- ✅ `run_cycle` → knowledge / argument / narrative / audit / gates / validators / suggestions
- ✅ `frontier` / `closure` / `next_action` / `diagnose` (6 bottleneck flags) / `opportunities` (value-ranked) / `execute_plan`
- ✅ `run_readiness` (composite-resolves, viz-rendered, measures-resolvable) + publishable axis
- ✅ `execute_action` (consequence-gated, autonomous, AI decider) / `advance_cycle`
- ✅ `persist_cycle` / `resolutions` / `suggest_studies` / `build_completion_map`
- ✅ invariants: progress⊥verdict, closure⊥frontier, epistemic separation, findings-as-partial-evidence (AC-01.02), null-control-as-success (AC-07.01), narrative-gap audit (AC-07.02), idempotent re-crank (AC-08.03)

## Shipped since this roadmap was written — DE2.0 (Phases A–F + N+2)

The generative half this roadmap called "missing" now exists as shipped modules +
CLI verbs. See [`architecture.md`](architecture.md). Landed:

- ✅ **Evidence integrity** (`evidence_integrity`, `discover reconcile-evidence`) —
  verified/provisional/contradicted/unsupported per finding against registered runs.
- ✅ **CandidateAction planner** (`candidate_action`, `discover plan`) — REPAIR/RESOLVE/EXTEND, ranked + dependency-ordered.
- ✅ **Prediction + ExperimentDesign** (`experiment_design`, `discover design`) — contrast, controls, registered predictions, committed decision rule (specify-before-run). Satisfies AC-03.01 (discriminating ranks above non-discriminating).
- ✅ **ModelVersion** (`model_architect`, `discover build-model`) — immutable, content-addressed, parent_ids; reuse/wrap/build acquisition + coupling checks. Satisfies FR-04.04.
- ✅ **DiscoveryDelta** (`evaluation`, `discover delta`) — semantic change per cycle. Satisfies FR-08.01.
- ✅ **Transition composition** (`transition_model`, `discover transition`) — composed model + conditional phase diagram (Cycle N+2).
- ✅ **Growth loop** (`study_seeding` / `study_refinement`, `discover seed-study` / `refine-study`) — propose→seed→run→promote→refine, with a `human_in_the_loop` vs `autonomous` policy. Covers FR-03.04 (designs → run-ready study.yaml) and part of FR-04.01 (needs_build → build routing).

## Still missing — new data entities
Target package: `discovery_engine/contracts/`
- ◑ **Hypothesis** — a `hypotheses` module + discrimination exist; not yet the fully generative entity (FR-02.01 credible-alternative generation).
- ◑ **Figure** (review_status + scientific critique) — only a file-exists check.
- ⬜ **Discovery** (novelty_basis, alternatives_considered, limitations) — passing a gate ≠ a discovery (FR-08.02).
- ◑ **Cycle state machine** DRAFT→…→INTEGRATED — the phases exist as functions; the explicit per-action state record + transitions are not yet first-class.

## Missing — stage capabilities

### Stage 2 — Generate hypotheses & predictions · `discovery_engine/hypotheses/`
- ⬜ FR-02.01 generate ≥1 credible alternative when evidence does not discriminate
- ⬜ FR-02.02 predictions conditioned on model/experimental settings, linked to observables
- ⬜ FR-02.03 separate biological-mechanism from implementation hypotheses
- ⬜ FR-02.04 novelty / prior-support / model-dependence; AI content starts *proposed*
- AC-02.01..03

### Stage 3 — Design & prioritize experiments · `discovery_engine/experiment_design/`
- ◑ value-ranking exists (`opportunities`); the following are new:
- ⬜ FR-03.02 expected information gain over calibrated predictive distributions (else heuristic + declared missing assumptions)
- ⬜ FR-03.03 identifiability / confounding / feasibility / downstream model impact
- ⬜ FR-03.04 translate designs → study.yaml behavior tests + observable requirements + build requests
- AC-03.01 discriminating intervention ranks above non-discriminating; AC-03.02 no EIG unless likelihood assumptions declared

### Stage 4 — Build, compose, calibrate, revise models · `discovery_engine/model_build/`
- ◑ `run_readiness` *reports* `needs_build`; the builder is new:
- ⬜ FR-04.01 resolve needs_build into concrete build tasks (and build them)
- ⬜ FR-04.02 compose via explicit process/bigraph interfaces + state-variable mappings; record units/sync
- ⬜ FR-04.03 parameter fit / structural alternative / model replacement as distinct revision kinds
- ✅ FR-04.04 immutable ModelVersion — never overwrite the model that produced prior evidence (`model_architect`)
- ◑ FR-04.05 smoke / conservation / unit checks before readiness (only import + declared checks today)

### Stage 6 — Figure critique · `discovery_engine/figure_critique/`
- ◑ only `viz_rendered` (file exists) today:
- ⬜ FR-06.01 select charts by inferential task
- ⬜ FR-06.03 does the figure show the hypothesis distinction / null control / variance / failure regime?
- ⬜ FR-06.04 machine-checkable structural QA + a separately recorded scientific critique (validator `scope=figure`)
- AC-06.01 file existence necessary but not sufficient; AC-06.02 truncated axis / hidden uncertainty triggers critique

### Stage 7 — Interpret, validate, synthesize evidence · `discovery_engine/evidence/`
- ◑ grades pass/fail today; the following are new:
- ⬜ FR-07.01 quantitative predicted-vs-observed comparison incl. uncertainty + untested regimes
- ⬜ FR-07.03 Bayesian / likelihood belief updates where defensible, qualitative otherwise
- ⬜ FR-07.04 model-error diagnosis — distinguish mechanism refutation from model/implementation limitation (EvidenceUpdate)

### Stage 8 — Record discoveries, renew
- ✅ FR-08.01 **DiscoveryDelta** — semantic change between cycle states (`evaluation.discovery_delta`)
- ⬜ FR-08.02 first-class Discovery records (scope / novelty / counterevidence); passing a gate ≠ a discovery
- ◑ FR-08.03 follow-ups from anomalies / sensitivity boundaries (only divergence/open-question driven today)
- ◑ FR-08.04 closure along **four** axes (operational / empirical / explanatory / publication) — ~3 today

## Missing — cross-cutting infrastructure
- ⬜ **Immutable versioned dependencies** for models/datasets/runs/analyses/figures + invalidation (only validator-snapshot drift today)
- ◑ **Uncertainty + assumptions + counterevidence + qualification** fields on claims/conclusions (provenance check only)
- ⬜ **Autonomy policy + budget/cost-ceiling + tool allowlist + per-decision log** `{policy_version, actor, input_hashes, rationale, consequence, result}`
- ⬜ **Scientific quality metrics** — information gain, discrimination, model correction, evidence quality, figure adequacy, cycle efficiency
- ◑ **Typed feedback transitions** (result→model, viz→analysis, interpretation→experiment, discovery→hypotheses) — partially via execute_action/advance routing

## Implementation waves (from the spec)
0. Core contracts — entity schemas, cycle-state fingerprints, typed feedback actions
1. Inquiry planning — hypotheses, predictions, experiment proposals, discrimination scoring
2. Model-action loop — needs_build → builder → readiness → execution
3. Scientific figure critic — provenance, QA, semantic critique, regenerate/re-run actions
4. Evidence learning — evidence update, alternatives, revision + narrative diff
5. Continuous discovery — DiscoveryDelta, branching, multidimensional closure
