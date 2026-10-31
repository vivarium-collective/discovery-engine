---
title: "Discovery Engine — streamlined closed-loop upgrade decision"
subtitle: "What to build (and not build) so a gap → model → experiment → finding → model revision → next cycle actually runs end to end"
date: "2026-10-30"
inputs:
  - "discovery_engine_logical_capabilities_upgrade.pdf (the DE 2.0 proposal, 9 pp)"
  - "discovery-engine-map.md (architecture map of the current engine, de2 worktree HEAD 4b18fb2)"
  - "~/code/discovery-engine--de2/discovery_engine/*.py (spot-checked), ~/code/discovery-engine (main)"
---

# 0. Executive decision

**Do not build "Discovery Engine 2.0" as a seven-capability orchestration layer with ~40 methods and a Pydantic contract suite.** The current engine already has the right shape — a pure `run_cycle` projection, one frontier/gate engine, explicit write-backs, and (on the `de2` branch) pure Phase A–F modules. What it lacks is not breadth; it is **four specific missing links** that stop the loop from closing:

1. **Evidence is not value-linked.** `classify_finding_evidence` marks a finding VERIFIED on run-id *registration* alone (evidence_integrity.py:103). Yet the substrate already holds the independent value: `compute_outcomes` writes `runs[].computed_outcomes[test].measured_value` (study_evaluator.py:1689-1866) and `populate_finding_observations` copies it into `evidence.observed`. The check that `observed == measured_value` is a ten-line strengthening, not a new "lineage" capability.
2. **Models have no identity.** Nothing binds a study arm's `composite` + `params` to a versioned artifact. `model_version()` exists (model_architect.py:170-188) but digests a *request* (question_id + mechanism routes), is never persisted, and hard-codes `validation.status = "pending"`. Without a `ModelVersion` bound to arms there is nothing for a finding to be conditional *on*, and nothing a revision can be a revision *of*.
3. **There is no feedback object.** Anomalies (`hypotheses.anomalies`), live model disagreements (`evaluation.model_disagreement_map`) and contested hypotheses are computed and then *displayed*. None of them becomes a frontier item, so none becomes an action, so none becomes a new model. The proposal is right that `ModelRevisionProposal` is the missing object; it is wrong about how much machinery it needs around it.
4. **The delta has no memory.** `discovery_delta` diffs against a hard-coded empty baseline (evaluation.py:173-213). The `fingerprint` is computed and thrown away. `persist_cycle` already writes a regenerable `cycle_state` — the prior snapshot belongs there.

**The decision:** close the loop with **six new primitives and five strengthened ones**, all pure except two explicit write-backs, persisted as **three additive blocks in `investigation.yaml`** (`model_versions[]`, `revision_proposals[]`, `cycle_state.evaluation_snapshot`) plus **one code-owned finding slot** (`provenance.model_version`). The executor for a model revision is the **existing** `build_seeded_study` / `refine_study` / `advance_cycle` path, not a new campaign runner. Four waves, not six; the loop is testable end to end in a two-cycle fixture by the end of wave 3.

A fact the proposal does not know: the Phase A–F modules it "keeps" (`evidence_integrity`, `candidate_action`, `experiment_design`, `model_architect`, `evaluation`, `study_seeding`, `study_refinement`, `transition_model`, +423 lines in `discover.py`) exist **only on the `feat/de2-evidence-integrity` worktree branch**, not on `main` (`git diff --stat origin/main...HEAD`: 12 files, +1697). Wave 0 is therefore "land what exists", before anything is strengthened.

\newpage

# 1. The critical path — what is load-bearing

The loop, stated in the engine's own vocabulary, with the current status of each hop:

| # | Hop | Current mechanism | Status | What makes it real |
|---|-----|-------------------|--------|--------------------|
| 1 | Unresolved question → actionable item | `frontier()` → `opportunities()` → `plan_candidate_actions()` | **Real** for criteria/decisions/questions; **blind** to anomalies, disagreements, contested hypotheses | 3 new frontier kinds (`anomaly`, `model_disagreement`, `revision_proposed`), one new op `revise_model` |
| 2 | Item → the model it concerns | none — arms cite composite path strings | **Missing** | `bind_model_versions()` + persisted `model_versions[]`; `resolve_composite()` factored out of `run_readiness` |
| 3 | Model + hypotheses → executable experiment | `design_discriminating_study()` (returns a dict, goes nowhere); `build_seeded_study()` / `refine_study()` (produce study specs) | **Disconnected** — design has no path to a study; seeding has no path from a design | `materialize_design()` = design → `build_seeded_study` + `refine_study(SWEEP)` |
| 4 | Experiment → run → recorded outcome | `advance_cycle` → `execute_action` → `vivarium-workbench run study`; `study_outcomes.sync` → `compute_outcomes` → `populate_finding_observations` | **Real but narrow**: only `test` ops on never-run studies; "has runs → skip" | advance runs `branch`/`revise_model` targets; skip rule becomes "has a run *for this model_version*" |
| 5 | Outcome → defensible finding | `classify_finding_evidence` (registration check) + `compare_hypotheses` | **Weak** — hand-typed number with real run id verifies; prose `evidence_for` can reach `supported` | VERIFIED requires `observed ≈ computed_outcomes[from_test].measured_value`, run `complete`, `evaluated_by: code`; `supported` requires ≥1 quantitative supports entry |
| 6 | Finding/disagreement → model revision proposal | none | **Missing** | `propose_model_revisions()` (pure) + `revision_proposals[]` (append-only) |
| 7 | Proposal → revised model → next cycle inputs | none | **Missing** | `apply_revision()` (write-back) → child study with `model_change`, new `ModelVersion{parent_ids}`, engine-origin hypotheses; `discovery_delta(prev)` shows the effect next cycle |

**The smallest set of NEW primitives that makes 1→7 real:**

| New primitive | Module | Kind | One-line contract |
|---|---|---|---|
| `bind_model_versions(studies) -> {slug: {arm: ModelVersion}}` | model_architect | pure | sha256(composite, canonical params) per arm; parent_ids from `seeded_from`/`refined_from`/`model_change` |
| `resolve_composite(comp, ws_root) -> {resolves, module, spec_path}` | model_architect (factored from discover.run_readiness:733-749) | pure (find_spec only) | the one place "does this model exist" is answered; `build_catalog` and `run_readiness` both call it |
| `propose_model_revisions(inv_spec, studies, evaluation, evidence_state, model_versions) -> [ModelRevisionProposal]` | new `model_revision.py` | pure | anomalies / live disagreements / contested → typed proposals; never invents a mechanism it cannot derive |
| `apply_revision(ws_root, inv_slug, proposal_id) -> {study_path, model_version}` | discover (write-back, CLI `discover revise --apply`) | mutating | proposal → `build_seeded_study(model_change)` or `refine_study(composite)`; registers child, mv, hypotheses |
| `materialize_design(design, parent_spec, inv_spec, mv) -> study dict` | experiment_design | pure | one variant per intervention arm, controls as arms, predictions as `hypotheses[]` entries for the child |
| `snapshot_evaluation(cmp, evidence_state, model_versions) -> dict` + `discovery_delta(..., prev=None)` | evaluation | pure | the prior state lives in `cycle_state.evaluation_snapshot`; delta is `prev → now` |

**Strengthened (existing code, same signatures where possible):**

| Existing | Change |
|---|---|
| `evidence_integrity.classify_finding_evidence` | value-lineage check against the study's `runs[]` (which already carry `computed_outcomes`); run status must be complete |
| `evaluation.compare_hypotheses` / `evaluate_claim_support` | `supported` needs ≥1 quantitative `supports` log entry; prose-only net>0 caps at `provisionally-supported`; match `hypothesis.study` by `inv_spec.studies` key (fix the `name`/`slug` fragility, hypotheses.py:30 / evaluation.py:62) |
| `discovery_engine.frontier` / `_executor_for` | + kinds `anomaly` (prio 48, op `revise_model`), `model_disagreement` (48, `revise_model`), `revision_proposed` (58, `revise_model`); consequence `review` |
| `candidate_action.plan_candidate_actions` | + kind `REVISE` (rank between REPAIR and RESOLVE); consume `execute_plan` not `opportunities` so `consequence` travels with the action (fixes the banner mismatch, map §4) |
| `discover.advance_cycle` | run `test` **and** `revise_model`/`branch` whose target study is seeded + run-ready; skip only if a run exists for the arm's current mv; call `study_outcomes.sync` after a successful run if the runner did not |
| `discover.load_cycle_inputs` | return `missing_members` and surface as a `validation_finding` instead of silently dropping (map §6 INPUTS weak point) |
| `finding_observations._fill_finding` | one more code-owned slot: `provenance.model_version` ← mv of the canonical run's arm |

Everything else in the proposal is either already present under another name, belongs to a different package, or is deferred (§5).

\newpage

# 2. Reuse vs. build — per load-bearing capability

## A — Assess (evidence integrity)

- **Reuse as-is:** `load_cycle_inputs`, `run_cycle`, `_load_runs_by_study`, `reconcile_evidence_state`'s ledger/summary/by_study shape, `trace_claim_to_runs`.
- **Strengthen:** `classify_finding_evidence(finding, study_spec, registered_run_ids)` → add a fourth argument `run_rows: dict[run_id, run_record]` (the `study.yaml` `runs[]` entries keyed by name — they already carry `status`, `computed_outcomes`, `variant`). Decision order becomes:
  1. no numeric `observed` → UNSUPPORTED (unchanged)
  2. unconditioned + violates cited `pass_if` → CONTRADICTED (unchanged)
  3. `from_test` + all `run_ids` registered **and** for the canonical cited run: `status ∈ complete`, `computed_outcomes[from_test].evaluated_by == "code"`, `|observed − measured_value| ≤ rel_tol·|measured_value|` (reuse `hypotheses._SCALAR_REL_TOL = 0.05`; exact for ints) → VERIFIED, reason names the run and the measured value
  4. registered but the value does not match / not code-graded / run not complete → PROVISIONAL with a **specific** reason (`observed 22.1 ≠ computed 3.4 for 'exchange_flux' in run r-…`; `run r-… status=failed`; `test graded by agent, not code`)
  5. else PROVISIONAL (unchanged)
- **Replace:** nothing. The proposal's `verify_observation_lineage` (checksums, transformations, units/tolerance) collapses into step 3 because the transformation *is* `compute_outcomes` and it already runs. Checksums of emitter stores are deferred; `manifest_json.result_fingerprint` is already reserved (run_registry.py:134) for that later.
- **Do not build:** `identify_inferential_gaps`, `assess_model_adequacy` as engine methods. The "selection claim vs exchange-flux measurement" check is semantic. The honest version is an **authored** field `claim_requires: [observable, ...]` on a study and a validator (`claim_scope`) that fails when a required observable is measured by no behavior test. That is a validation.py registry entry, not a capability. Deferred to wave 4 because nothing in the loop depends on it.

## B — Plan

- **Reuse as-is:** `frontier`, `closure`, `opportunities`, `execute_plan`, `select_portfolio`, `suggest_studies`, `select_for_launch` (still unwired; that is fine until wave 4).
- **Strengthen:** `frontier()` gains three sources. They are *derived* items, so they discharge the same way everything else does (a `resolutions` entry of matching kind/target), and they must **not** block closure — they are opportunities, not obligations. `_executor_for("revise_model", target)` → `{"executor": "discover revise", "command": "discover revise <inv> <proposal-id> --apply", "consequence": "review", "auto_safe": False}`. `plan_candidate_actions` adds `REVISE`. That is the whole planner change; the proposal's `generate_scientific_opportunities` and `estimate_action_value` are these two edits.
- **Do not build:** a separate `DiscoveryPlan` type, a budget/authorization DAG, or `resolve_dependencies` beyond what `select_portfolio` already does (`depends_on` + topological emit). The one structural dependency the loop needs — "a RESOLVE on unverified evidence depends on R0" — is already there (candidate_action.py:88).

## C — Design

- **Reuse as-is:** `discriminating_hypotheses`, `register_prediction`, `commit_decision_rule`, `validate_controls`, `design_discriminating_study`.
- **Build:** `materialize_design(design, parent_spec, inv_spec, model_version)` — the bridge the current code lacks. It calls `build_seeded_study(parent, inv, new_slug, model_change=...)` for the baseline and appends one `variants[]` arm per `design.interventions` entry (the `when` conditions become `params` overrides when they are parameter-valued, else the arm is marked `needs_author`), marks control arms from `design.controls`, and emits `hypotheses[]` entries for the child (study=new slug, predictions from `design.predictions`, `origin: design:<id>`). It is pure; `_write_seeded_study` does the I/O.
- **Do not build:** `derive_predictions` (needs a predictive model the substrate does not have — predictions are authored bands), `optimize_experiment_design`, `estimate_replication` from power/variance. Keep `refine_study`'s REPLICATE (≥3 seeds) and SWEEP (0.5×/2× clamped) as the explicit, labelled heuristics they are.

## D — Build (Model Architect)

This is where the proposal over-reaches most and where the real gap is smallest.

- **Reuse:** `build_catalog`'s grouping (one entry per distinct composite path, unioned studies/fidelities/observables). `model_version`'s content-addressing idea.
- **Strengthen:** `build_catalog(studies, ws_root=None)` → each entry gains `resolves: bool`, `module`, `spec_path` from `resolve_composite`. The genesis instance has 5 baseline composites that are `ModuleNotFoundError` (the comment at discover.py:729-731 says so); after this change the catalog says so too instead of listing "6 components".
- **Replace:** `model_version(request, routes)` → `model_version(composite, params, *, parent_ids=(), origin=None)`. Identity = `"mv:" + sha256(json.dumps({composite, params}, sort_keys=True))[:16]`. The current id includes `question_id`, which makes the same model under two questions two models — wrong. `validation.status` is dropped (it was a constant); validation is a property of *evidence under* a model, recorded on findings, not on the mv.
- **Build:** `bind_model_versions(studies)` — derive the mv for every baseline and variant arm (variant `base:` arms inherit the baseline composite and overlay params). `parent_ids` come from `seeded_from.study`'s baseline mv, `refined_from`, or the applying proposal. Persisted into `investigation.yaml: model_versions[]` by `persist_cycle` (regenerable: it is a projection of the arms, plus the `origin` link that is not).
- **Keep as advisory, off the critical path:** `_SYNONYMS`, `search_components`, `choose_acquisition_route`, `validate_coupling`. They are a lexical hint for a human/agent choosing what to reuse. Label the output `heuristic`, keep the CLI verb, do not make anything depend on them.
- **Do not build in this engine:** `select_formalism`, `wrap_simulator`, `construct_process`, `compose_model`, `verify_mathematical_contracts`. Those are the `/viva-expert` skill and process-bigraph's `composite_spec` / `core_introspection` (which the engine already re-exports — composite_spec.py:30-34, core_introspection.py:1-6). The engine's job when a route is BUILD is to emit a `needs_build` item with the mechanism and the observables required — it already does (advance_cycle's `needs_build`, run_readiness gaps). A new composite arrives as a file; the engine then binds a new mv to it. That boundary is the one the proposal states ("Process Bigraph remains the execution/composition substrate") and then crosses.

## E — Execute

- **Reuse as-is:** `execute_action` (consequence gating, injectable `runner`, `executed_actions` log, placeholder refusal), `run_readiness`, `_default_runner`, the workbench runner, `study_outcomes.sync`, `compute_outcomes`, `populate_finding_observations`, `run_registry`, `cycle_trace.sandbox_advance`.
- **Strengthen `advance_cycle`:**
  - eligible ops: `test`, `branch`, `revise_model` — the latter two only when their target resolves to a study that is on disk and `run_readiness(...).ready` (i.e. the proposal has already been *applied*; an unapplied proposal is `review`-tier and stays gated).
  - skip rule: today `if spec.get("runs"): seen.add(...); continue` (discover.py:827-831). New: skip iff some run's arm mv equals the current baseline mv (run `params`/`variant` + composite → mv). A revised study is a new file with a new mv, so this rarely bites, but a `refine_study --write` in place (composite raised) must re-run.
  - post-run: if the runner's study.yaml has no `computed_outcomes` for the new run, call `study_outcomes.sync(study_dir)` (best-effort, as sync already is). *Verify first whether `vivarium-workbench run study` already syncs; if yes, no change.*
- **Do not build:** `compile_campaign`, `validate_campaign_readiness`, `run_ensemble`, `run_parameter_sweep`, `collect_observables`, `register_campaign_results`, `execute_action_graph`. The engine is a dependency *of* the workbench (run_registry.py:82-84; `tests/test_no_workbench_import.py`) and must not grow a runner. Replicates and sweeps are already expressed in the study spec (`robustness.seeds`, `variants[]`) and executed by the workbench. "Campaign" = a study with variants; it already exists.

## F — Evaluate

- **Reuse as-is:** `rollup_support`, `support_summary`, `fidelity_flags`, `anomalies`, `model_disagreement_map`, `invalidate_dependents` (currently unwired — wire it in wave 3 to mark hypotheses/claims REVIEW_REQUIRED when an mv changes; it already returns exactly that shape).
- **Strengthen:** `evaluate_claim_support(summary, evidence_status, *, has_quantitative)` — `supported` requires `net > 0 and evidence_status == VERIFIED and has_quantitative`. `compare_hypotheses` output gains `model_version` (the mv the favored finding's run was conditional on) so "supported" reads as "supported under mv:…". Fix the slug join.
- **Build:** `propose_model_revisions` (§4) and `snapshot_evaluation` / real `discovery_delta(prev)`.
- **Do not build:** `quantify_uncertainty` (four separately propagated uncertainty sources — there are no distributions in the substrate; seeds give a spread, nothing more), `evaluate_model_adequacy` (same as A), a separate `compare_predictions` (that is `score_support`), a separate `derive_findings` (findings are authored prose over code-owned slots and must stay so — the engine never writes `statement`).

## G — Integrate

- **Reuse as-is:** `persist_cycle` (re-reads on disk, writes only regenerable caches), `build_cycle_state`, `snapshot_validations` (the precedent for the snapshot pattern), `_renew_executive_decisions`, `apply_cycle` + git commit in the workbench.
- **Strengthen:** `build_cycle_state` adds `evaluation_snapshot` and `model_versions` ids; `persist_cycle` also writes `model_versions[]`. `discovery_delta(inv, studies, ev, prev=inv_spec.get("cycle_state", {}).get("evaluation_snapshot"))`.
- **Do not build:** `snapshot_scientific_state` as a content-addressed immutable store, `commit_scientific_cycle` as a transaction with event log, `synthesize_narrative`, `critique_figure`, `update_argument_graph` as a separate method (edges are already derived from `relation` tags; a revision writes the tag `refines`/`alternative-to` on the child and the graph follows).

\newpage

# 3. The minimal canonical contracts

**Format decision: plain dicts + JSON Schema at write boundaries, no Pydantic.** The engine is dict-based end to end with tolerant readers (`_as_list`, `_to_float`, field-name fallbacks); introducing Pydantic models at the core would force either a parallel dict API or a rewrite of every consumer, and the workbench reads these YAML files directly. Add `schemas/investigation-extensions.schema.json` (the three new blocks) and validate in `persist_cycle` / `apply_revision` only. Both workbench schemas are `additionalProperties: true` at top level (verified in `viva-template/template/.pbg/schemas/*.schema.json`), so the new `investigation.yaml` blocks save cleanly from the workbench. **Check before wave 1:** `findings[].provenance` in `study.schema.json` — the file has 4 `additionalProperties: false` sub-objects; if `provenance` is one of them, add `model_version` to the viva-template schema first.

### 3.1 ModelVersion (persisted, `investigation.yaml: model_versions[]`)

```yaml
- id: mv:9f3a1c2e7b4d5a60            # sha256({composite, params})[:16]
  composite: viva_genesis.composites.metabolic_exchange.build
  params: {diffusion: 0.1, pump_strength: 0.4}
  parent_ids: [mv:1b2c3d4e5f607182]   # [] for a root
  origin: {kind: authored | seeded | refined | revision, ref: rp:…}   # why it exists
  bound_to: [{study: s2-exchange, arm: baseline}]                     # derived, regenerable
  resolves: true                                                       # resolve_composite at persist time
```

Rules: **identity is content** (composite + canonical params), never a title or slug; **immutable** — a changed param is a new id with the old one in `parent_ids`; `bound_to`/`resolves` are projections and may be regenerated; `origin` is the only non-derivable field and is written once by `apply_revision` / `seed-study --write` / `refine-study --write`.

### 3.2 EvidenceRecord = the Phase-A ledger row (not a new object)

`classify_finding_evidence` already returns `{finding, measure, observed, status, reason, run_ids, from_test, study}`. Add `model_version`, `measured_value`, `evaluated_by`. That row is the evidence record; `reconcile_evidence_state().ledger` is the evidence table. Nothing is persisted — it is re-derived every cycle from `study.yaml` + `runs.db`.

### 3.3 Finding (unchanged shape, one new code-owned slot)

```yaml
findings:
  - id: finding-s2-flux
    statement: "…"                       # authored — engine never writes
    evidence: {from_test: exchange_flux_22x, observed: 22.1, units: …}
    provenance: {run_ids: [r-2026-10-28-…], model_version: mv:9f3a1c2e7b4d5a60}   # both code-owned, fill-absent-only
```

### 3.4 ModelRevisionProposal (persisted, append-only, `investigation.yaml: revision_proposals[]`)

```yaml
- id: rp:s3:mv:4e5f…:a1b2              # sha256({source, affected_model_version, inadequacy})[:8]
  status: proposed | approved | rejected | applied | evaluated
  source: {kind: anomaly | model_disagreement | contested | contradicted, study: s3-enclosure, finding: finding-s3-enclosure-prob, observable: enclosure_probability}
  affected_model_version: mv:4e5f…
  inadequacy: "arms by_topology={2d: 0.12, 3d: 0.71} disagree (spread 0.59) on enclosure_probability"
  candidate_changes:                     # typed; engine fills only what it can derive
    - {kind: param, name: membrane_bending, from: 1.0, to: [0.5, 2.0], derived_by: refine_study._default_sweep}
    - {kind: composite, replace_with: viva_genesis.composites.membrane_3d.build, derived_by: catalog higher fidelity_rank}
    - {kind: mechanism, add: null, needs_author: true}            # never invented
  predicted_effects: [{observable: enclosure_probability, direction: converge, rationale: "if the 2D/3D gap is a formalism artefact, raising fidelity collapses the spread"}]
  discriminating_test: {behavior_test: enclosure_prob_in_band, pass_if: {low: 0.3, high: 0.8}}   # from the existing test the finding cites
  approval: review
  history: [{at: …, event: proposed, by: engine}, {at: …, event: approved, by: human, rationale: "…"}, {at: …, event: applied, child_study: s3b-enclosure-3d, model_version: mv:…}]
  evaluated: {cycle_fingerprint: …, outcome: resolved | persists | inconclusive}   # filled by the next cycle's delta
```

Identity is content-addressed so re-deriving the same proposal in the next cycle is a no-op (dedupe on id); status moves only via `history` events written by `discover revise`.

### 3.5 ScientificState snapshot (minimal; lives in `cycle_state.evaluation_snapshot`)

```yaml
cycle_state:
  evaluation_snapshot:
    fingerprint: 3e9c…                   # the existing discovery_delta fingerprint
    by_study: {s2-exchange: {status: supported, favored: h-s2-mutualism, evidence: verified, model_version: mv:…}, …}
    model_versions: [mv:…, …]
    proposals: {open: [rp:…], applied: [rp:…]}
```

Not a full immutable content-addressed state object. It is what the next cycle needs to compute a change, nothing more; everything else is re-derivable from the YAML + runs.db it was derived from.

### 3.6 DiscoveryDelta (existing output + a real diff)

Keep `added_or_qualified`, `gated`, `model_disagreements`, `anomalies`, `regressions`, `fingerprint`. Add `prev_fingerprint`, `changed: [{study, from, to, model_version_from, model_version_to}]`, `new_model_versions`, `proposals: {opened, applied, evaluated}`, `unchanged: bool`. A no-op cycle yields `unchanged: true` and empty lists — the proposal's acceptance test, met cheaply.

### 3.7 CandidateAction (existing dict, two fields added)

`plan_candidate_actions` rows gain `consequence` (from `execute_plan`) and kind `REVISE`. No status machine, no event store — `executed_actions` + `resolutions` + `revision_proposals[].history` are the three audit trails and they are enough.

\newpage

# 4. The feedback closure — finding → revision → next cycle, concretely

Walk the genesis S3 case (two membrane representations disagree on enclosure probability) through two cycles.

### Cycle N — the proposal appears

1. `load_cycle_inputs` → `run_cycle` → `reconcile_evidence_state` (strengthened). S3's finding carries `evidence.by_topology: {2d: 0.12, 3d: 0.71}`, no single `observed` → `fidelity_flags` raises `gated_evidence`; `model_disagreement_map` returns `disagreement: true, spread 0.59`.
2. `bind_model_versions` → S3 baseline arm → `mv:4e5f…` (2D membrane composite + params); `build_catalog` shows a second composite in the workspace with higher `fidelity_rank` (`membrane_3d`), `resolves: true`.
3. `compare_hypotheses` → S3 `status: gated`. `anomalies` → none for S3 (no scalar). `propose_model_revisions` consumes `(evaluation, model_disagreement_map, anomalies, evidence_state, model_versions, catalog)` and emits **one** proposal `rp:s3:mv:4e5f…:a1b2` (shape in §3.4): source `model_disagreement`, two derivable candidate changes (param sweep of the first numeric baseline param via `_default_sweep`; composite replacement with the higher-fidelity catalog entry), `discriminating_test` = the behavior test the finding cites (`evidence.from_test`), `approval: review`. The proposal is a pure value in the cycle result; `persist_cycle` appends it to `revision_proposals[]` if its id is new.
4. `frontier()` now lists `{kind: revision_proposed, target: rp:s3:…, op: revise_model, priority: 58}`; `execute_plan` attaches `consequence: review`; `plan_candidate_actions` emits a `REVISE` action; the workbench panel shows it as a human-review item (not `human-only`, not auto-runnable). `closure()` is unaffected — the kind does not block.

Proposal-generation rules (what `propose_model_revisions` derives, and refuses to derive):

| Trigger | Condition | Inadequacy text | Candidate changes the engine may fill |
|---|---|---|---|
| `model_disagreement` | `model_disagreement_map[].disagreement` | arms + spread | `composite` → catalog entry with higher `_FIDELITY_RANK` for the same capability tokens, if one `resolves`; `param` → `_default_sweep` |
| `anomaly` | `anomalies()` row | observed outside all live bands | `param` sweep; `mechanism: needs_author` with the `proposed_question` text |
| `contested` | `compare_hypotheses[].status == contested` | favored hypothesis net < 0 under mv | `param` sweep; retire-hypothesis is **not** proposed (that is a human `set-verdict`/`resolve`) |
| `contradicted` | Phase-A `contradicted` finding | observed violates its own gate | **no revision proposal** — routed to REPAIR (evidence problem, not model problem) |

The engine never writes a mechanism. A `mechanism` change is `needs_author: true` plus the derived question; filling it is `/viva-expert` or a human. That keeps the "no AI silently endorses new biological claims" rule structural.

### Between cycles — a human (or an AI decider) approves

`discover revise <inv> rp:s3:… --approve --rationale "…"` appends an `approved` history event. Or, with the existing decider seam: `execute_action(..., decider=…, autonomous=True, target="rp:s3:…")` — extend the `steer` branch at discover.py:610 to also handle `revise_model`, recording `by: discovery-ai`. Rejection appends `rejected` and the frontier item discharges (the proposal id is excluded from `frontier` when status ∈ {rejected, applied, evaluated}).

### Apply — the proposal becomes a model and a study

`discover revise <inv> rp:s3:… --apply [--change 1]` → `apply_revision`:

1. Pick the candidate change (default: the first fully-derived one; `--change` selects). For `composite`: `child = build_seeded_study(parent=S3, inv, new_slug="s3b-enclosure-3d", finding_id=source.finding, model_change={composite: membrane_3d, note: "revision rp:…"})` — this needs a **small extension to `_inherit_arm`** (study_seeding.py:60-72) to honour a `model_change.composite` override alongside `params`. For `param`: `model_change={params: {membrane_bending: 0.5}}` as today, plus `refine_study(child, needs=[SWEEP], sweep_param=…, sweep_values=[0.5, 2.0])` so the child carries the sweep arms.
2. The child inherits S3's `observables` and `behavior_tests` (already how `build_seeded_study` works), so the `discriminating_test` is in place by construction. `parent_studies: [s3-enclosure]` with `pipeline_gate.prerequisites: [{study: s3-enclosure, relation: alternative-to}]` so `run_cycle` derives a `challenge` edge — the disagreement is now *in the argument graph*. (Note: `challenge` edges block closure; this is correct — an open model disagreement with a pending test should block "Closed".)
3. `is_run_ready(child)` must be empty; `run_readiness(child, ws)` must say `ready` (composite resolves — this is where a `BUILD`-route proposal stops: the child is written with `status: needs_build` and `advance` reports it in `needs_build`, never fakes a run).
4. Write: `_write_seeded_study` (registers the slug in `inv.studies`), append engine-origin hypotheses to `inv.hypotheses[]`:
   ```yaml
   - id: h-s3b-formalism-artifact
     study: s3b-enclosure-3d
     statement: "The 2D/3D enclosure gap is a representation artefact; at matched fidelity enclosure_probability lies in the 3D band."
     predictions: [{observable: enclosure_probability, expected: {low: 0.3, high: 0.8}}]
     status: open
     origin: {kind: revision, ref: rp:s3:…}      # engine-authored; distinguishable from human hypotheses
   - id: h-s3b-real-divergence
     study: s3b-enclosure-3d
     predictions: [{observable: enclosure_probability, expected: {op: "<", value: 0.3}}]
     status: open
     origin: {kind: revision, ref: rp:s3:…}
   ```
   Two disjoint-band hypotheses on one observable → `discriminating_hypotheses` fires → Phase C designs it and Phase B ranks it RESOLVE next cycle. The predicted bands come from the proposal's `predicted_effects` + the parent test's `pass_if`; where the engine cannot derive a disjoint pair it writes **one** hypothesis and the child is RESOLVE-ineligible until a human adds the alternative (visible as an `open_question`, as today).
5. Register `mv:new = model_version(membrane_3d, params, parent_ids=[mv:4e5f…], origin={kind: revision, ref: rp:…})` in `model_versions[]`; append `applied` to the proposal history with `child_study` and `model_version`; `invalidate_dependents({kind: model, id: s3-enclosure})` marks S3's hypotheses/claim `REVIEW_REQUIRED` in the cycle result (display only — no authored field changes).

### Execute — the same crank runs it

`discover advance <inv> --autonomous` (or the panel's sandboxed "Run continuously"): the child is a never-run, run-ready study whose frontier item is an `open_question` (`unassessed` claim) → op `test` → `execute_action` runs `vivarium-workbench run study s3b-enclosure-3d`. Post-run `study_outcomes.sync` fills `runs[].computed_outcomes`, `populate_finding_observations` fills `evidence.observed` + `provenance.run_ids` + `provenance.model_version` on any finding whose `from_test` is set. The one authored act left is the finding's `statement` (biology-forward prose — `/viva-biology-forward`), which the engine refuses to write; until a human writes a finding with `from_test`, the study's verdict comes from `roll_up_verdict` over the run outcomes and the hypotheses are scored only if a finding exists. **Design choice:** `apply_revision` pre-seeds one finding stub per discriminating test with `evidence.from_test` set and `statement: null` so the quantitative slots fill automatically and the prose is visibly missing (a `validation_finding`), rather than the finding being absent.

### Cycle N+1 — the loop re-enters

1. `reconcile_evidence_state` → the child's finding is VERIFIED only if `observed ≈ computed_outcomes[from_test].measured_value` on a complete, code-graded run.
2. `compare_hypotheses` → `h-s3b-formalism-artifact` supported-under-`mv:new` (or the alternative; or `anomaly` if neither band is hit — which opens a new proposal, and the cycle continues).
3. `discovery_delta(prev=cycle_state.evaluation_snapshot)` → `changed: [{study: s3b…, from: unassessed, to: supported, model_version_to: mv:new}]`, `new_model_versions: [mv:new]`, `proposals.evaluated: [rp:s3:…]`. `persist_cycle` writes `evaluated: {outcome: resolved}` on the proposal when the source disagreement is gone from `model_disagreement_map` (S3's finding gets `superseded.adjudicated_by: <child run>` — an authored/CLI act, `discover resolve`-style; the engine suggests the exact command in the delta) or `persists` when the new arm reproduces the gap.
4. The frontier no longer lists `rp:s3:…`; it may list the next proposal. **That is one full turn of the loop with every write explicit and every derived value re-derivable.**

Minimum code surface for this section: `model_revision.py` (~250 lines: triggers, candidate derivation, dedupe), `discover.apply_revision` + `discover revise` CLI (~150), `_inherit_arm` composite override (~10), frontier/executor/plan edits (~60), `advance_cycle` eligibility (~30), `finding_observations` mv slot (~15), `evaluation.snapshot/delta(prev)` (~60), `model_architect.bind_model_versions/resolve_composite` (~80). Roughly 650 lines plus tests — versus the proposal's ~40 methods.

\newpage

# 5. Where the proposal over-reaches

Cut, with reasons. "Collapse into" means the need is real and is met by an existing or smaller thing.

| Proposed | Verdict | Why |
|---|---|---|
| `run_discovery_cycle(workspace, id, policy, budget)` seven-stage orchestrator | **Cut** | The orchestration already exists as `cycle_trace.STEPS` in the workbench (data-driven step graph, dependency order, timing) and as separate `discover` verbs. A second owner of "the sequence" is the one-decision-authority violation the proposal itself warns about. Add the new steps to `STEPS`; add `discover cycle --advance` if a one-shot CLI is wanted. |
| `execute_action_graph` with prerequisites, approvals, checkpoints, retries, budgets | **Cut** | `execute_action` + `select_portfolio` + `advance_cycle(max_runs)` + the `seen` set already give gated, dependency-ordered, bounded execution. Retries/checkpoints belong to the runner (workbench), not the engine. |
| Pydantic contract suite; "status changes must be explicit events with reason/actor/timestamp/hashes" | **Cut → JSON Schema at write boundaries; three append-only logs** | See §3. A universal event store for every status transition of every object is bookkeeping with no consumer. |
| `ScientificState` immutable content-addressed snapshot; `snapshot_scientific_state`; `commit_scientific_cycle` | **Collapse into `cycle_state.evaluation_snapshot`** | Everything in the state is re-derivable from YAML + runs.db except the evaluation statuses and mv ids needed for a diff. `persist_cycle` is already the atomic "commit". |
| `verify_observation_lineage` (checksums, transformations, units/tolerance) | **Collapse into `classify_finding_evidence` step 3** | The transformation is `compute_outcomes`, already run and stored. Store checksums → `manifest_json.result_fingerprint` later if ever needed. |
| `identify_inferential_gaps`, `assess_model_adequacy`, `evaluate_model_adequacy` | **Defer → one `claim_scope` validator over an authored `claim_requires[]`** | Semantic adequacy ("selection cannot be verified by flux alone") cannot be derived from the substrate; the honest mechanism is an authored requirement and a mechanical check. Not on the loop's critical path. |
| Capability D: `select_formalism`, `wrap_simulator`, `construct_process`, `compose_model`, `validate_coupling` (real), `verify_mathematical_contracts` | **Out of scope for this package** | These are `/viva-expert` and process-bigraph (`composite_spec`, `core_introspection`). The engine records `ModelVersion`s and routes BUILD to `needs_build`; it does not build models. The proposal states this boundary and then lists six methods across it. |
| `_SYNONYMS` capability routing (reuse/wrap/build) | **Demote to advisory** | Substring matching over a hand-written synonym table; useful as a hint, dangerous as a dependency. Label `heuristic`, keep the CLI verb. |
| Capability E: `compile_campaign`, `validate_campaign_readiness`, `run_ensemble`, `run_parameter_sweep`, `collect_observables`, `register_campaign_results` | **Cut** | The study spec already expresses replicates (`robustness.seeds`) and sweeps (`variants[]`); the workbench runs them; `study_outcomes.sync` collects and registers. The engine must not import or reimplement the runner (`test_no_workbench_import`). |
| "An existing run is not a blanket skip condition" | **Keep (as the per-mv skip rule)** | Correct and cheap. |
| Capability C: `derive_predictions`, `propose_interventions`, `design_controls`, `estimate_replication`, `optimize_experiment_design`, `validate_experiment_design` | **Cut 5 of 6; keep `validate_controls` (exists) and `materialize_design` (new)** | No predictive distributions exist to derive predictions or power from. Controls/interventions are already in `design_discriminating_study`; what is missing is the design → study materialization, not more design analytics. |
| Capability F: `quantify_uncertainty` (seed/param/measurement/model-form), `compare_predictions`, `derive_findings` | **Cut** | `compare_predictions` is `score_support`. `derive_findings` would have the engine write finding prose — violates the authored/derived separation that is the engine's core guarantee. Uncertainty: seeds give a spread; call it a spread. |
| `detect_model_disagreement` with units and normalized effect sizes | **Strengthen later, not now** | The absolute `spread >= 0.2` threshold is unit-blind (map §6 F) — replace with relative spread when `evidence.units` is present. Small; wave 4. |
| Capability G: `synthesize_narrative`, `critique_figure`, `update_findings`, `update_argument_graph` | **Cut** | `narrative.py`, `report_linter.py`, `viz_freshness.py` exist. Findings are authored. The argument graph follows `relation` tags; a revision writes a tag and the graph updates by derivation. |
| `invalidate_dependents` | **Keep (exists, wire it)** | Already implemented in evaluation.py:149-170 and unwired; call it from `apply_revision`. |
| "Autonomy: action authorization depends on consequence, novelty, resources and approvals" | **Keep consequence; defer the rest** | The consequence tier works. Novelty/resources scoring has no data behind it yet. `select_for_launch` wiring is wave 4. |
| N+2 transition model as a generic step | **Not generic; leave as the domain CLI verb it is** | Hard-coded endosymbiosis rule table (map §6 N+2). Do not generalize it; do not list it in the loop. |
| 6-wave roadmap with "Contracts" first | **Replace** | Writing schemas before knowing which fields the loop needs is how a 40-method spec gets written. Contracts land *with* the primitive that first writes them. |

\newpage

# 6. Pruned, sequenced roadmap — four waves

Each wave ends with the loop demonstrably one hop longer on a fixture and on the genesis instance, with pure functions tested without a runner (the `runner` seam in `execute_action` and the `tests/fixtures` convention already support this).

### Wave 0 — Land and tell the truth about evidence
*Scope:* merge `feat/de2-evidence-integrity` to `main` (the 8 Phase A–F modules exist only there); `classify_finding_evidence` value-lineage (§2 A); `supported` requires quantitative support; `load_cycle_inputs` returns `missing_members` → `validation_finding`; fix the `hypothesis.study` slug join; fix the `persisted knowledge_cycle` CLI text.
*Exit criterion:* a finding with a hand-typed `observed` and a real run id classifies PROVISIONAL with a reason naming the computed value; a missing member study appears on the frontier instead of vanishing; `discover reconcile-evidence eukaryogenesis` reports how many of the current 9 "verified" findings survive.

### Wave 1 — Models have identity; the delta has memory
*Scope:* `resolve_composite` factored from `run_readiness`; `build_catalog(…, ws_root)` with `resolves`; `model_version(composite, params, parent_ids, origin)` replaces the request-digest version; `bind_model_versions`; `model_versions[]` persisted by `persist_cycle`; `provenance.model_version` slot in `_fill_finding`; `evaluation_snapshot` in `build_cycle_state`; `discovery_delta(prev)`; `schemas/investigation-extensions.schema.json`; verify `findings[].provenance` schema permissiveness in viva-template.
*Exit criterion:* two consecutive `discover cycle` runs with no change yield `unchanged: true`; editing one baseline param yields a new mv id with the old as parent and a one-line `changed`; the genesis catalog reports which of its 6 composites resolve.

### Wave 2 — The feedback object exists and is planned
*Scope:* `model_revision.propose_model_revisions` with the four trigger rules and derivable-only candidate changes; `revision_proposals[]` append-only with content-addressed ids; frontier kinds `anomaly` / `model_disagreement` / `revision_proposed`, op `revise_model`, consequence `review`; `plan_candidate_actions` kind `REVISE` and `consequence` on every action; `discover revise <inv> <id> --approve|--reject --rationale`; decider seam extended to `revise_model`; workbench `STEPS` gains `propose_revisions`.
*Exit criterion:* on the genesis instance, S3's 2D/3D disagreement produces one versioned proposal naming the affected mv, a composite-replacement candidate (if `membrane_3d` resolves) and the discriminating behavior test; it appears on the panel as a review item; approving/rejecting changes only its history and discharges the frontier item; a re-run does not duplicate it.

### Wave 3 — Closure: apply, run, re-enter
*Scope:* `_inherit_arm` composite override; `apply_revision` (child study, engine-origin hypotheses with `origin`, finding stubs with `from_test`, mv with `parent_ids`, `alternative-to` prerequisite, `invalidate_dependents`); `materialize_design`; `advance_cycle` eligibility (`branch`/`revise_model` targets that are on disk + ready) and per-mv skip; post-run `sync` hook if the runner does not sync; proposal `evaluated` outcome written by the next `persist_cycle`; workbench `STEPS` gains `apply_revision` (sandboxed via `sandbox_advance`, review-gated for real files; `apply_cycle` never auto-approves).
*Exit criterion:* a two-cycle fixture test with a fake `runner` (writes a `runs.db` row + a tiny store): cycle 1 → proposal; `--approve --apply` → child study on disk, run-ready, hypotheses discriminable; `advance --autonomous` → run recorded, `computed_outcomes` + finding slots filled; cycle 2 → `discovery_delta.changed` names the child and the new mv, the proposal is `evaluated`, the frontier shows either closure of the disagreement or a new proposal. Same path exercised on genesis in the panel sandbox.

### Wave 4 (deferred, only if the loop is turning) — rigor and autonomy
`select_for_launch` wired into `advance`; `claim_requires[]` + `claim_scope` validator; relative-spread disagreement threshold using `evidence.units`; `manifest_json.result_fingerprint`; reconciling the banner's `runnable` count with what `advance` will actually run.

\newpage

# 7. Risks and invariants to preserve

**Invariants (non-negotiable; each new primitive is checked against them):**

1. **Derive-first purity.** Every new analytic (`bind_model_versions`, `propose_model_revisions`, `materialize_design`, `snapshot_evaluation`, `discovery_delta(prev)`, `resolve_composite`) is a pure function of `(inv_spec, studies, runs_by_study, prev_snapshot)` — `resolve_composite` uses `find_spec` only, never imports composite code (as `run_readiness` already does). Writes happen in exactly three places: `persist_cycle` (regenerable blocks + append-only proposals), `apply_revision` (new study file, hypotheses append, mv/origin, history event), `discover revise --approve/--reject` (history event). The panel's plain cycle stays read-only.
2. **Authored wins; the engine never writes prose.** Proposals, hypotheses and finding stubs the engine writes carry `origin: {kind: revision|design, ref}` so they are distinguishable and can be purged; `statement`/`summary`/`explanation` stay `null` until a human writes them; `_fill_finding` stays fill-absent-only; `apply_revision` creates a *new* child study rather than editing the parent (the only in-place path is the existing `refine-study --write`, which is explicitly opt-in).
3. **Epistemic separation, now with a third axis.** `verified` (evidence lineage) ≠ `supported` (hypothesis under evidence) ≠ true (never asserted; all validators keep `scientific_judgment: null`). New: `supported` is always *under* a `model_version` — `compare_hypotheses` reports it, the delta carries it, and a changed mv invalidates it (`invalidate_dependents`). A simulation under mv:B supporting a hypothesis that mv:A contested is a model disagreement, not a confirmation.
4. **Closure semantics unchanged.** The three new frontier kinds are non-blocking. The `challenge` edge created by an `alternative-to` prerequisite *is* blocking — deliberately: an open model disagreement with a pending discriminating run should prevent "Closed". Document this in the panel gate text.
5. **Schema compatibility.** New investigation blocks are additive under `additionalProperties: true`; the one study-level addition is a code-owned slot inside `findings[].provenance` (verify the sub-schema; patch viva-template first if closed). Readers remain tolerant (`_as_list`, `.get`) so pre-upgrade YAML loads unchanged. `_RETIRED_INV_CACHES` is the pattern if any new block is later retired.
6. **Workbench integration is additive.** `cycle_trace.STEPS` gains two steps; `sandbox_advance` keeps running the mutating path on a copy; `apply_cycle` writes verdicts/edges/cycle_state as today and **never** approves or applies a proposal (review tier → human). The engine keeps zero imports from `vivarium_workbench` (`test_no_workbench_import`).

**Risks and mitigations:**

| Risk | Mitigation |
|---|---|
| Stricter VERIFIED reclassifies most of the existing corpus to PROVISIONAL and the panel "looks worse" | That is the point; ship with a `reason` per row and a one-command repair (`viva-compute-outcomes` + `populate_finding_observations`) so genuinely run-backed findings re-verify mechanically. Wave 0 exit criterion reports the count on genesis before merging. |
| `_SCALAR_REL_TOL = 0.05` is wrong for some observables (counts, probabilities near 0) | Exact match for ints; absolute tolerance when `|measured| < 1e-9`; tolerance overridable per behavior test (`pass_if.tolerance`), defaulting to the constant. |
| mv identity churn — a non-semantic param (seed, output path) changes the id | Canonicalize params before hashing: drop keys in a small deny-list (`seed`, `seeds`, `emitter`, `out_dir`, `n_steps`) and sort. Seeds are replicates of the same model, not new models. |
| `advance_cycle` eligibility change causes re-runs or loops | Per-mv skip + the existing `seen` set + `max_runs`; `revise_model` targets run only after `apply` (file on disk, `run_readiness.ready`). Unit-test the no-loop property with the fake runner. |
| `apply_revision` on a BUILD-route candidate produces an unrunnable child | `apply` refuses unless `resolve_composite(...).resolves`; otherwise writes nothing and reports `needs_build` with the mechanism — same honesty rule as `seed-study --write` (refuses on `is_run_ready` gaps). |
| Engine-written hypotheses pollute the human hypothesis set | `origin` field; `discover revise --reject` on an applied proposal retires its hypotheses (`status: retired`, already respected by `_RETIRED_STATUS`); the validator census counts engine-origin hypotheses separately. |
| Proposal spam on noisy instances (many anomalies) | Content-addressed ids dedupe across cycles; cap proposals per study per cycle at 1 (highest spread/net first); `rejected` ids are never re-proposed while the source finding is unchanged (hash includes the finding's `observed`/`by_*` values). |
| Post-run sync not happening (runner path vs CLI path) | Verify what `vivarium-workbench run study` does before wave 3; if it syncs, add nothing; if not, best-effort `study_outcomes.sync` in `execute_action` after `ok`, matching sync's own never-raise discipline. |
| The two-cycle test needs a simulation | It does not: the `runner` seam writes a `runs_meta` row via `register_run` and a minimal parquet/sqlite store the `RunReader` can open; `compute_outcomes` then grades for real. `tests/test_computed_outcomes.py` already builds such stores. |
| Scope creep back toward the 40-method spec | Wave 4 is explicitly conditional on the loop turning on a real instance. Anything not in §1's table needs a frontier item it unblocks. |

# 8. Departures from the proposal, in one place

- No seven-stage orchestrator; the workbench step graph + `discover` verbs stay the single owner of sequencing.
- No Pydantic; JSON Schema at the two write boundaries.
- No `ScientificState` store; a slim `evaluation_snapshot` in `cycle_state`.
- `verify_observation_lineage` becomes a strengthening of the existing classifier, using `computed_outcomes` the engine already writes.
- Capability D shrinks to identity + resolution (`ModelVersion`, `resolve_composite`); model construction stays in `/viva-expert` and process-bigraph.
- Capability E shrinks to `advance_cycle` eligibility; no campaign runner in the engine.
- Capability C shrinks to `materialize_design`; no power analysis or design optimization.
- Capability F adds exactly `propose_model_revisions` and a real delta; no uncertainty decomposition, no engine-written findings.
- Capability G adds nothing new; `persist_cycle` is the commit.
- Four waves instead of six, contracts landing with the primitives that first write them, and a two-cycle fixture as the acceptance test for closure rather than per-capability acceptance tests.
- One addition the proposal missed: the Phase A–F modules must first be merged from the `de2` worktree branch to `main`.
