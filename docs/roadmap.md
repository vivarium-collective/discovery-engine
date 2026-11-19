# discovery-engine roadmap — the eight-stage scientific cycle

DE2.0 shipped Phases A–F + N+2, and the 2026-11-11 generative-cycle work (v0.4.0)
closed the loop from proposal to discovery and back to a human — seed → run →
model-build, first-class Discovery records, model-error diagnosis, and a
policy-gated `reopen` (see the two Shipped sections below and `architecture.md`).
This tracks the remaining gap between the engine and the full eight-stage
scientific discovery cycle (the *Scientific Discovery Cycle Technical
Specification & Requirements*, 2026-10-28). Each item carries its FR/AC id, a
status, and the sub-package it will land in.

As of 2026-11-19 the **Benchmark-1 evaluation substrate** has shipped (the
*Next Development & Scientific Benchmarks* plan, 2026-11-18): the measurement +
governance layer plus the first passing benchmark. See the Shipped section below
and [`cycle-map.html`](cycle-map.html) / [`discovery-engine-cycle-map.pdf`](discovery-engine-cycle-map.pdf)
§10 for the map. This layer ships as **0.7.0** (the EIG active-design increment
and the free-text steering goal landed on top of it). The open frontier is real
LLM-backed reasoning at the generation seam — see **Next build steps** at the end.

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
- Transition composition (Cycle N+2) was a domain analysis; moved out of the framework to `docs/extracted/transition_model.py` for the workspace to adopt.
- ✅ **Growth loop** (`study_seeding` / `study_refinement`, `discover seed-study` / `refine-study`) — propose→seed→run→promote→refine, with a `human_in_the_loop` vs `autonomous` policy. Covers FR-03.04 (designs → run-ready study.yaml) and part of FR-04.01 (needs_build → build routing).

## Shipped 2026-11-11 — the generative cycle closes (PRs #49/#53/#51/#52/#54)

The five-PR generative-cycle plan is merged, closing the loop from proposal to
discovery and back to a human. Landed:

- ✅ **One AutonomyPolicy for every consequential op** (#49) — a single
  `AutonomyPolicy.allows()` gate for every mutating advance move (was per-op).
- ✅ **Seed op + authoring write-back** (#53) — a proposal promotes to a run-ready
  `study.yaml` on disk, closing propose→seed as an advance op.
- ✅ **Chain promotion → run** (#51) — promoting a ready proposal chains straight
  into its run; all ready proposals promote in one advance.
- ✅ **Real model-build into autonomous advance** (#52) — `needs_build` resolves
  into an actual `ModelVersion` build during `advance` (completes FR-04.01).
- ✅ **FR-08.02 first-class Discovery records** (`discovery_record`, #54) — a claim
  verdict flipping to a settled outcome on *verified* evidence writes a `Discovery`
  (scope / novelty / counterevidence); passing a gate is not a discovery.
- ✅ **FR-07.04 model-error diagnosis** (`model_error`, #54) — a refutation emits a
  typed `EvidenceUpdate` (`revise-model` vs `revise-hypothesis`) that steers which
  child seed `suggest_studies` proposes.
- ✅ **`reopen` op** (#54) — staged scientist feedback (`pending_feedback[]`) is a
  first-class frontier item + advance move (`reconcile_and_reopen`), folded out of
  the side `ingest-feedback` CLI into the policy-gated loop.

## Shipped 2026-11-19 — the Benchmark-1 evaluation substrate (PRs #102–#110)

The measurement + governance layer that turns "the engine got better" into "it
moved a number on a held-out oracle under a fixed budget", plus the first passing
vertical slice (**Benchmark 1 — hidden metabolic regulator**). Pure, additive; the
cycle stages are untouched.

- ✅ **Typed Discrepancy** (`phases/discrepancy.py` + schema, #102) — the sourced
  anomaly / model-disagreement trigger, derived into `cycle_result.discrepancies`
  and attached to each `hypothesize` request's context (#109). The FR-02 input contract.
- ✅ **Evidence-authority guard** (`phases/evidence_authority.py`, #102) — an applied
  artifact can never overwrite a finding's `observed` or a run's `computed_outcomes`
  (interpretation ≠ evidence), enforced in `apply_result`.
- ✅ **Held-out quantitative EvidenceRecord** (`study/evaluator/evidence_record.py`,
  `benchmark/holdout.py`, #103) — predicted-vs-observed + uncertainty + residual,
  scored on *unseen* conditions (`evaluate_holdout`). Delivers FR-07.01.
- ✅ **Bounded autonomy** (`discover/budget.py`, `_policy.py` stops,
  `actions.record_decision`, #104) — a `Budget` (simulation_runs / model_builds /
  generation_calls), typed stop reasons (budget_exhausted / no_informative_experiment /
  insufficient_evidence / seek_human_data), and `investigation.yaml::decision_log[]`.
- ✅ **Declarative benchmark** (`benchmark/{manifest,rubric,gates,matrix,baselines}.py`
  + schema + `scenarios/metabolic_regulator.py`, #105/#106/#107) — a benchmark is
  *data* (fixture, allowed-skills, budget, required-artifacts, metrics, stops); a
  weighted rubric scores a run, hard gates fail it regardless of score, and a CI
  matrix compares `full` vs `deterministic_only` / `no_critique` / `no_build`.
- ✅ **`discover investigate`** (`discover/investigate.py`, #108) — one bounded loop
  over the existing verbs (project → emit → fill → apply under the budget, logging
  each decision), halting on closure / budget / no-informative-move. 40 `discover`
  verbs total; no new phase logic.
- ✅ **Skill-level invariants** (#110) — truth-preservation, calibration/abstention,
  contrast sensitivity, pinned by tests.
- **Benchmark 1 passes end to end**: the full engine recovers the concealed mechanism
  AND beats a deterministic-only baseline on held-out prediction, under a fixed budget,
  with a clean decision log and zero gate violations
  (`test_full_beats_deterministic_on_mechanism_and_holdout`).

> **The oracle caveat.** These pass with the benchmark's **oracle filler** standing in
> for the LLM at the generation seam (by design — the real filler swaps in at the same
> seam). Real `research`/`design`/`analyze`/`synthesize` reasoning, and the full A–E
> baseline matrix (which needs non-deterministic fillers for meaningful CIs), are the
> open frontier.

## Still missing — new data entities
Target package: `discovery_engine/contracts/`
- ◑ **Hypothesis** — a `hypotheses` module + discrimination exist, and the typed **Discrepancy** trigger now feeds each `hypothesize` request's context (#102/#109); the fully generative entity (real alternative generation) still needs an LLM filler (FR-02.01).
- ◑ **Figure** (review_status + scientific critique) — only a file-exists check.
- ✅ **Discovery** (scope, novelty, counterevidence) — first-class `Discovery` records; passing a gate ≠ a discovery (FR-08.02, `discovery_record`, #54).
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
- ⬜ FR-03.02 expected information gain over calibrated predictive distributions (else heuristic + declared missing assumptions) — **now unblocked** by the held-out EvidenceRecord (FR-07.01); the recommended next capability build
- ⬜ FR-03.03 identifiability / confounding / feasibility / downstream model impact
- ⬜ FR-03.04 translate designs → study.yaml behavior tests + observable requirements + build requests
- AC-03.01 discriminating intervention ranks above non-discriminating; AC-03.02 no EIG unless likelihood assumptions declared

### Stage 4 — Build, compose, calibrate, revise models · `discovery_engine/model_build/`
- ◑ `run_readiness` *reports* `needs_build`; the builder is new:
- ✅ FR-04.01 resolve needs_build into concrete build tasks (and build them) — real `ModelVersion` build wired into autonomous `advance` (#52)
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
- ✅ FR-07.01 quantitative predicted-vs-observed comparison incl. uncertainty + untested regimes — held-out **EvidenceRecord** + `evaluate_holdout` (#103)
- ⬜ FR-07.03 Bayesian / likelihood belief updates where defensible, qualitative otherwise
- ✅ FR-07.04 model-error diagnosis — distinguish mechanism refutation from model/implementation limitation (typed `EvidenceUpdate`, `model_error`, #54)

### Stage 8 — Record discoveries, renew
- ✅ FR-08.01 **DiscoveryDelta** — semantic change between cycle states (`evaluation.discovery_delta`)
- ✅ FR-08.02 first-class Discovery records (scope / novelty / counterevidence); passing a gate ≠ a discovery (`discovery_record`, #54)
- ◑ FR-08.03 follow-ups from anomalies / sensitivity boundaries (divergence/open-question driven, now also refutation-steered via `model_error`)
- ◑ FR-08.04 closure along **four** axes (operational / empirical / explanatory / publication) — ~3 today

## Missing — cross-cutting infrastructure
- ⬜ **Immutable versioned dependencies** for models/datasets/runs/analyses/figures + invalidation (only validator-snapshot drift today)
- ◑ **Uncertainty + assumptions + counterevidence + qualification** fields on claims/conclusions (provenance check only)
- ◑ **Autonomy policy + budget/cost-ceiling + tool allowlist + per-decision log** `{policy_version, actor, input_hashes, rationale, consequence, result}` — one `AutonomyPolicy` gates every consequential op (#49); a `Budget` meter (generation/sim/build caps) + typed stops + the persisted `decision_log[]` now exist (#104). Cost-ceiling and tool-allowlist are still to come.
- ◑ **Scientific quality metrics** — the weighted **rubric** (mechanism-id, held-out error, …), the held-out residual, and the **hard gates** now score a run (#106); information gain / figure adequacy / cycle efficiency still to come.
- ◑ **Typed feedback transitions** (result→model, viz→analysis, interpretation→experiment, discovery→hypotheses) — result→model (`model_error` EvidenceUpdate) and the human reopen (`reopen` op) now typed; viz→analysis + interpretation→experiment still via execute_action/advance routing.

## Implementation waves (from the spec)
0. Core contracts — entity schemas, cycle-state fingerprints, typed feedback actions
1. Inquiry planning — hypotheses, predictions, experiment proposals, discrimination scoring
2. Model-action loop — needs_build → builder → readiness → execution
3. Scientific figure critic — provenance, QA, semantic critique, regenerate/re-run actions
4. Evidence learning — evidence update, alternatives, revision + narrative diff
5. Continuous discovery — DiscoveryDelta, branching, multidimensional closure

## Benchmark suite (Next Development plan)

A ladder of 12 synthetic→real benchmarks (T0–T5). **1 of 12** is built:
- ✅ **Benchmark 1 — hidden metabolic regulator** — manifest + scenario + acceptance
  test; passes vs the deterministic-only baseline (with the oracle filler).
- ⬜ **Benchmarks 2–12** — DnaA initiation, antibiotic heteroresistance, cross-feeding,
  tissue inflammation, emergent multicellularity, model-composition failure injection,
  equifinality/abstention, study-value optimization, published-data replication,
  literature-grounded novelty, autonomous multiscale construction.

## Next build steps (2026-11-19)

**Deterministic, buildable now:**
1. ✅ *Refresh this roadmap* + cut the **0.7.0** release marking the benchmark
   substrate, the EIG active-design increment, and the free-text steering goal.
2. ✅ **EIG / active design** (Stage 3, FR-03.02) — a model-agnostic experiment-utility
   scorer (ranks experiments by discrimination, or abstains with a NO-EXPERIMENT stop,
   #118) wired into `design_discriminating_study` as an expected-information fallback
   that picks the most-separating regime when no pair of bands is fully disjoint (#119).
   Next: feed it a study's perturbation space + the workspace model to `predict`.
3. ⬜ **Benchmark 2 (DnaA initiation)** — a second manifest + scenario reusing the
   substrate; proves the harness generalizes beyond Benchmark 1.
4. ⬜ **Persist EvidenceRecord in-grade** (currently derived on read) + round out
   governance (cost-ceiling / tool-allowlist beyond the generation-call budget).

**The frontier (needs a real LLM filler behind `$VIVA_FILLER_CMD`):**
   The drive is now steerable end to end — `discover investigate <inv> --goal "<text>"`
   (#120) threads a free-text goal into every request, and viva-superpowers'
   `/viva-drive-engine` + `/viva-fill` wrap it. What remains is the reasoning *quality*
   behind the seam:
5. ⬜ Run `discover investigate` against Benchmark 1 with real `hypothesize`/`critique`
   reasoning, replacing the oracle — then the full A–E baseline matrix becomes real.
6. ⬜ The remaining plan skills: `research` (literature), `design` (EIG), `analyze`
   (stats/uncertainty), `synthesize` (narrative graph); then Benchmarks 3–12.
