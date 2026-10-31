# Closed-Loop Discovery Engine Upgrade — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the discovery loop so one unresolved scientific question runs end to end — gap → versioned model → materialized/executed experiment → evidence-verified finding → model-revision proposal → next cycle — with six new primitives and five strengthened ones, no new orchestrator, no runner in the engine.

**Architecture:** Keep the derive-first engine (`run_cycle` pure projection, one `frontier()`/gate engine, explicit write-backs). Add value-linked evidence, model identity (`ModelVersion`), a feedback object (`ModelRevisionProposal`), and a delta with memory. Persist three additive `investigation.yaml` blocks (`model_versions[]`, `revision_proposals[]`, `cycle_state.evaluation_snapshot`) + one code-owned `findings[].provenance.model_version` slot. The executor for a revision is the existing `build_seeded_study`/`refine_study`/`advance_cycle` path.

**Tech Stack:** Python 3.12, plain dicts + tolerant readers (no Pydantic), JSON Schema at write boundaries, ruamel/yaml for writes, `importlib.util.find_spec` for composite resolution, pytest with a fake-runner seam.

**Spec:** `docs/superpowers/specs/2026-10-30-closed-loop-upgrade-decision.md` (Fable's streamlined decision — the binding authority) and its two inputs: `discovery_engine_logical_capabilities_upgrade.pdf` (the DE 2.0 proposal, pruned by the decision) and `discovery-engine-map.md` (current architecture map). Where this plan and the decision disagree, the decision wins; where the decision and the PDF proposal disagree, the decision wins (it deliberately cuts the proposal — see decision §5).

## Global Constraints

Copied verbatim from the decision (§7); every task's requirements implicitly include these.

- **Base branch:** this branch is cut from `feat/de2-evidence-integrity` (the 8 Phase A–F modules + ~423 lines in `discover.py` live only there, not `main`). Landing this work merges de2 to `main` too. Verify at setup: `git log --oneline main..HEAD` shows the de2 commits beneath this plan's commits.
- **Derive-first purity:** every new analytic (`bind_model_versions`, `resolve_composite`, `propose_model_revisions`, `materialize_design`, `snapshot_evaluation`, `discovery_delta(prev)`) is a pure function of its inputs; `resolve_composite` uses `importlib.util.find_spec` only, never imports composite code. Writes happen in exactly three places: `persist_cycle` (regenerable blocks + append-only proposals), `apply_revision`, `discover revise --approve/--reject/--apply`.
- **Authored wins; the engine never writes prose:** engine-written proposals/hypotheses/finding stubs carry `origin: {kind: revision|design, ref}`; `statement`/`summary`/`explanation` stay `null` until a human writes them; `_fill_finding` stays fill-absent-only; `apply_revision` creates a new child study, never edits the parent in place.
- **Epistemic separation (three axes):** `verified` (evidence lineage) ≠ `supported` (hypothesis under a `model_version`) ≠ true (never asserted; validators keep `scientific_judgment: null`).
- **Closure semantics:** the three new frontier kinds (`anomaly`, `model_disagreement`, `revision_proposed`) are **non-blocking**; the `challenge` edge from an `alternative-to` prerequisite **is** blocking (deliberate).
- **Schema compatibility:** new `investigation.yaml` blocks are additive under `additionalProperties: true`; the one study-level addition is a code-owned slot inside `findings[].provenance` — verify that sub-schema in viva-template before writing it (patch viva-template first if closed). Pre-upgrade YAML must load unchanged (tolerant `.get`/`_as_list` readers).
- **Workbench isolation:** the engine keeps **zero** imports from `vivarium_workbench` — `tests/test_no_workbench_import.py` must stay green. No runner, campaign engine, or orchestrator in the engine.
- **No AI attribution** in commits or PRs (no `Co-Authored-By`, no "Generated with" footer).
- **mv identity is content:** `"mv:" + sha256(json.dumps({composite, params_canonical}, sort_keys=True))[:16]`, params canonicalized by dropping a deny-list (`seed`, `seeds`, `emitter`, `out_dir`, `n_steps`) — never a title/slug/question.

## Review Focus

Input classes / failure modes the spec implies that per-task tests must exercise (each is pinned to its owning task below):

- **VERIFIED tolerance edges** — counts/ints (exact match), probabilities near 0 (absolute tolerance when `|measured| < 1e-9`), per-test `pass_if.tolerance` override. (Task 1)
- **mv identity churn** — a non-semantic param (seed/out_dir) must NOT change the mv id; a semantic param must. (Task 5)
- **Schema back-compat** — a pre-upgrade `investigation.yaml`/`study.yaml` (no new blocks, no `provenance.model_version`) loads and cycles unchanged. (Task 6)
- **Proposal dedupe across cycles** — re-deriving the same proposal next cycle is a no-op (same content-addressed id); a `rejected` proposal is not re-proposed while its source finding is unchanged; cap 1 proposal/study/cycle. (Task 8)
- **BUILD-route apply refusal** — `apply_revision` on a candidate whose composite does not `resolve` writes nothing and reports `needs_build` with the mechanism (never fakes a run). (Task 12)
- **advance no-loop** — the per-mv skip + `seen` set + `max_runs` prevent re-running the same model and prevent loops; `revise_model` targets run only after apply (file on disk + `run_readiness.ready`). (Task 13)
- **Closure correctness** — the new frontier kinds do not flip `closed`; an open `alternative-to` challenge edge does. (Task 9, Task 12)

An empty section would mean none were found; these were found.

---

## Task 1: Value-linked VERIFIED (evidence lineage)

**Files:**
- Modify: `discovery_engine/evidence_integrity.py` (`classify_finding_evidence`, caller `reconcile_evidence_state`)
- Modify: `discovery_engine/hypotheses.py` (reuse `_SCALAR_REL_TOL`; expose if needed)
- Test: `discovery_engine/tests/test_evidence_integrity.py` (add cases)

**Interfaces:**
- Consumes: the study's `runs[]` entries (keyed by run name) carrying `status`, `computed_outcomes[test].{measured_value, evaluated_by}`, `variant` (written by `study_evaluator.compute_outcomes` / `study_outcomes.sync`).
- Produces: `classify_finding_evidence(finding, study_spec, registered_run_ids, run_rows)` — new 4th arg `run_rows: dict[str, dict]` (run-name → run record). Ledger rows gain `measured_value`, `evaluated_by`.

- [ ] **Step 1: Write failing tests** in `test_evidence_integrity.py`:
  - `test_verified_requires_value_match`: finding `observed=22.1`, `from_test=T`, run `r1` registered with `computed_outcomes[T].measured_value=22.0, evaluated_by="code", status="complete"` → VERIFIED; reason names run `r1` and `22.0`.
  - `test_hand_typed_value_is_provisional`: same but `measured_value=3.4` → PROVISIONAL, reason `observed 22.1 ≠ computed 3.4 for 'T' in run r1`.
  - `test_not_code_graded_is_provisional`: `evaluated_by="agent"` → PROVISIONAL (`graded by agent, not code`).
  - `test_incomplete_run_is_provisional`: run `status="failed"` → PROVISIONAL (`run r1 status=failed`).
  - `test_int_exact_and_prob_near_zero`: int `observed=5, measured=5` → VERIFIED; int `5` vs `6` → PROVISIONAL; `observed=1e-12, measured=0.0` with abs-tol → VERIFIED.
  - `test_pass_if_tolerance_override`: behavior_test `pass_if.tolerance=0.2` widens the match.
- [ ] **Step 2: Run tests, verify they fail** — `pytest discovery_engine/tests/test_evidence_integrity.py -v`; Expected: FAIL (signature mismatch / wrong status).
- [ ] **Step 3: Implement** the decision §2-A step 3/4 ordering in `classify_finding_evidence`: after the existing registration check, require for the canonical cited run `status ∈ {complete, done, ran, completed}`, `computed_outcomes[from_test].evaluated_by == "code"`, and `|observed − measured_value| ≤ tol·|measured_value|` (tol = `pass_if.tolerance` else `_SCALAR_REL_TOL=0.05`; exact for ints; absolute `1e-9` when `|measured|<1e-9`) → VERIFIED else PROVISIONAL with a specific reason. Thread `run_rows` from `reconcile_evidence_state` (build it from `study_spec["runs"]` keyed by `name`).
- [ ] **Step 4: Run tests, verify pass**; Expected: PASS.
- [ ] **Step 5: Run the file's full suite** `pytest discovery_engine/tests/test_evidence_integrity.py -v` (existing tests still green), then commit.

## Task 2: `supported` requires quantitative support; fix the slug join

**Files:**
- Modify: `discovery_engine/evaluation.py` (`compare_hypotheses`, `evaluate_claim_support`, the `hypothesis.study` match ~`:62`)
- Modify: `discovery_engine/hypotheses.py` (`rollup_support` / match ~`:30`)
- Test: `discovery_engine/tests/test_evaluation.py`

**Interfaces:**
- Produces: `evaluate_claim_support(summary, evidence_status, *, has_quantitative: bool)` — `supported` iff `net>0 and evidence_status=="verified" and has_quantitative`; prose-only net>0 caps at `provisionally-supported`. `compare_hypotheses` output gains `model_version` (filled in Task 6 wiring; `None` for now). `hypothesis.study` matches `inv_spec["studies"]` keys, not `name`/`slug` only.

- [ ] **Step 1: Write failing tests:**
  - `test_prose_only_caps_at_provisional`: hypothesis supported only by `alternate_hypotheses.evidence_for` (no quantitative `supports`) → `provisionally-supported`, not `supported`.
  - `test_supported_needs_verified_and_quantitative`: net>0, quantitative support present, study evidence `verified` → `supported`.
  - `test_slug_join_matches_inv_studies`: `hypothesis.study` given as the `inv_spec.studies` slug while the spec's `name` differs → support still attaches (regression for hypotheses.py:30 / evaluation.py:62).
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** the `has_quantitative` gate (true iff ≥1 `supports` log entry came from `score_support`, not the prose fold) and the slug-join fix (resolve each hypothesis's study against `inv_spec["studies"]` first).
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Full file suite, commit.**

## Task 3: `missing_members` surfaced; stale CLI text fixed

**Files:**
- Modify: `discovery_engine/discover.py` (`load_cycle_inputs` → also return/annotate missing members; `cycle` CLI "persisted knowledge_cycle" → "persisted cycle_state")
- Modify: `discovery_engine/validation.py` (a validator that emits a `validation_finding` for each missing member) OR surface via `run_cycle` (choose the lower-coupling path; a validator is preferred)
- Test: `discovery_engine/tests/test_discover_inputs.py`

**Interfaces:**
- Produces: `load_cycle_inputs` returns its existing tuple unchanged for back-compat, plus a module-level helper `missing_members(ws_root, slug) -> [slug]` (or an added 4th tuple element behind a keyword — choose non-breaking: a helper). A `structural.investigation.members` validator raises a frontier `validation_finding` per missing member.

- [ ] **Step 1: Write failing tests:** an investigation listing a slug with no `study.yaml` on disk → `missing_members` returns it; a `run_cycle` over it yields a `validation_finding` naming it (not silently dropped). Assert the CLI prints "persisted cycle_state".
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** without changing `load_cycle_inputs`'s return arity (add the helper + validator); fix the print string.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Full file suite, commit.**

> **Wave 0 exit (record in the ledger):** run `discover reconcile-evidence eukaryogenesis` against the genesis workspace and note how many of the current 9 "verified" findings survive the stricter classifier (expected: most → PROVISIONAL with reasons). This is an observation, not a code gate.

## Task 4: `resolve_composite` + catalog resolution

**Files:**
- Modify: `discovery_engine/model_architect.py` (add `resolve_composite`; `build_catalog(studies, ws_root=None)`)
- Modify: `discovery_engine/discover.py` (`run_readiness` ~`:733-749` calls the new `resolve_composite`)
- Test: `discovery_engine/tests/test_model_architect.py`

**Interfaces:**
- Produces: `resolve_composite(composite: str, ws_root) -> {"resolves": bool, "module": str|None, "spec_path": str|None}` — pure, `find_spec` only (factored from `run_readiness`). `build_catalog(studies, ws_root=None)`: each component entry gains `resolves`, `module`, `spec_path` (only when `ws_root` given; absent/`None` → omit, preserving today's output).
- Consumes: used by `run_readiness` (Task 13) and `bind_model_versions` (Task 6).

- [ ] **Step 1: Write failing tests:** a dotted composite that imports → `resolves: True` with module/spec_path; a nonexistent path → `resolves: False`; `build_catalog(studies, ws_root)` marks each component; `build_catalog(studies)` (no ws_root) is byte-identical to today.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement**; make `run_readiness` call `resolve_composite` (no behavior change — same find_spec check, now shared).
- [ ] **Step 4: Run, verify pass** (incl. existing `run_readiness` tests unchanged).
- [ ] **Step 5: Commit.**

## Task 5: `model_version(composite, params, parent_ids, origin)` + canonicalization

**Files:**
- Modify: `discovery_engine/model_architect.py` (replace `model_version(request, routes)` ~`:170-188`)
- Test: `discovery_engine/tests/test_model_architect.py`

**Interfaces:**
- Produces: `model_version(composite: str, params: dict, *, parent_ids=(), origin=None) -> dict` → `{id, composite, params, parent_ids, origin}`; `id = "mv:" + sha256(json.dumps({"composite": composite, "params": _canonical_params(params)}, sort_keys=True))[:16]`. Drop the old `validation.status` constant. `_canonical_params(params)` drops `_MV_DENYLIST = {"seed","seeds","emitter","out_dir","n_steps"}` and sorts.
- Interfaces consumed by: `bind_model_versions` (Task 6), `apply_revision` (Task 12).

- [ ] **Step 1: Write failing tests:** same composite+params → same id; changing `diffusion` → new id; changing only `seed` → SAME id (`test_seed_does_not_change_mv`); `parent_ids`/`origin` round-trip; two different questions with the same composite+params → same id (regression vs the old request-digest).
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement.** Grep callers of the old `model_version`; update them (and `transition_model.py` if it calls it) or adapt.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit.**

## Task 6: `bind_model_versions`, persistence, `provenance.model_version` slot, schema

**Files:**
- Modify: `discovery_engine/model_architect.py` (`bind_model_versions`)
- Modify: `discovery_engine/discover.py` (`build_cycle_state` adds `model_versions` ids; `persist_cycle` writes `investigation.yaml: model_versions[]`)
- Modify: `discovery_engine/finding_observations.py` (`_fill_finding` adds `provenance.model_version` ← mv of the canonical run's arm; fill-absent-only)
- Create: `discovery_engine/schemas/investigation-extensions.schema.json` (the 3 new blocks; validated in `persist_cycle`/`apply_revision` only)
- Test: `discovery_engine/tests/test_model_binding.py`, extend `test_persist_cycle.py`

**Interfaces:**
- Produces: `bind_model_versions(studies) -> {slug: {arm: model_version_dict}}` — baseline + variant arms; variant `base:` arms inherit baseline composite, overlay params; `parent_ids` from `seeded_from`/`refined_from`/`model_change`; `origin.kind ∈ {authored, seeded, refined, revision}`. Persisted `model_versions[]` per the decision §3.1 shape (`bound_to`/`resolves` regenerable; `origin` written once).
- **Precondition check (do first):** verify `findings[].provenance` in viva-template `study.schema.json` is not `additionalProperties:false`; if it is, patch viva-template to allow `model_version` before writing the slot. Record the finding in the ledger.

- [ ] **Step 1: Write failing tests:** `bind_model_versions` yields a stable id per arm; a seeded child's baseline carries the parent's mv in `parent_ids`; `persist_cycle` writes `model_versions[]` and it validates against the new schema; a pre-upgrade `study.yaml` with no `provenance` block still fills nothing and doesn't crash (`test_backcompat_no_provenance`); `_fill_finding` sets `provenance.model_version` only when absent.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement**; keep `model_versions[]` regenerable (projection of arms + the non-derivable `origin`).
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit.**

## Task 7: `snapshot_evaluation` + `discovery_delta(prev)` with memory

**Files:**
- Modify: `discovery_engine/evaluation.py` (`snapshot_evaluation`; `discovery_delta(inv, studies, ev, prev=None)`)
- Modify: `discovery_engine/discover.py` (`build_cycle_state` adds `evaluation_snapshot`; delta call passes `prev=cycle_state.evaluation_snapshot`)
- Test: `discovery_engine/tests/test_evaluation.py` (delta cases)

**Interfaces:**
- Produces: `snapshot_evaluation(cmp, evidence_state, model_versions) -> dict` (decision §3.5: `{fingerprint, by_study, model_versions, proposals}`); `discovery_delta(..., prev=None)` adds `prev_fingerprint`, `changed[]`, `new_model_versions`, `proposals`, `unchanged: bool`.

- [ ] **Step 1: Write failing tests:** two identical snapshots → `unchanged: True`, empty lists; a study flipping `unassessed→supported` with a new mv → one `changed` row with `model_version_to`; `prev=None` → degrades to today's "measured against empty" behavior (back-compat).
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit.**

> **Wave 1 exit (ledger note):** two consecutive `discover cycle` runs with no change → `unchanged: true`; editing a baseline param → new mv id with old as parent + a one-line `changed`; genesis catalog reports which of its 6 composites resolve.

## Task 8: `propose_model_revisions` (the feedback object)

**Files:**
- Create: `discovery_engine/model_revision.py` (`propose_model_revisions`, dedupe, cap, candidate derivation)
- Test: `discovery_engine/tests/test_model_revision.py`

**Interfaces:**
- Produces: `propose_model_revisions(inv_spec, studies, evaluation, evidence_state, model_versions, catalog) -> [dict]` — one `ModelRevisionProposal` (decision §3.4 shape) per trigger, content-addressed id `rp:<study>:<mv>:<hash8>` where the hash includes the finding's `observed`/`by_*` values. Triggers + derivable candidate changes per decision §4 table:
  - `model_disagreement` (`model_disagreement_map[].disagreement`) → composite candidate (higher `_FIDELITY_RANK` catalog entry for the same capability tokens, if it `resolves`) + param sweep.
  - `anomaly` (`anomalies()` row) → param sweep + `mechanism: needs_author` with the derived question.
  - `contested` (`compare_hypotheses[].status=="contested"`) → param sweep (never "retire hypothesis").
  - `contradicted` → **no proposal** (routed to REPAIR; evidence problem).
  - The engine **never** fills a `mechanism` change value (`needs_author: true`).
  - Cap: 1 proposal/study/cycle (highest spread/net first).

- [ ] **Step 1: Write failing tests:** S3-style `by_topology={2d:0.12,3d:0.71}` disagreement → one proposal naming the affected mv, a composite candidate (if a higher-fidelity catalog entry resolves) and the behavior test the finding cites; a `contradicted` finding → NO proposal; re-running on unchanged input → identical id (dedupe); a `mechanism` candidate is always `needs_author: true`; cap of 1/study enforced; a `rejected` proposal id is not re-emitted while the source finding is unchanged.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** (pure; no I/O). Reuse `_FIDELITY_RANK`/capability tokens from `model_architect`.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit.**

## Task 9: Frontier/plan wiring + persisted `revision_proposals[]`

**Files:**
- Modify: `discovery_engine/discovery_engine.py` (`frontier` + `_executor_for` + priority/op tables)
- Modify: `discovery_engine/candidate_action.py` (`plan_candidate_actions` kind `REVISE`; consume `execute_plan` so `consequence` travels)
- Modify: `discovery_engine/discover.py` (`run_cycle` calls `propose_model_revisions` and includes proposals in the bundle; `persist_cycle` appends new proposals to `revision_proposals[]`)
- Test: `discovery_engine/tests/test_frontier_revision.py`

**Interfaces:**
- Consumes: Task 8 proposals, `model_disagreement_map`, `anomalies`.
- Produces: frontier kinds `anomaly` (prio 48, op `revise_model`), `model_disagreement` (48), `revision_proposed` (58); `_executor_for("revise_model", target) -> {"executor":"discover revise","command":"discover revise <inv> <id> --apply","consequence":"review","auto_safe":False}`. `plan_candidate_actions` rows gain `consequence` and a `REVISE` kind ranked between REPAIR and RESOLVE. All three kinds **non-blocking** (not in `_CLOSURE_BLOCKING`). A proposal is excluded from the frontier when `status ∈ {rejected, applied, evaluated}` or discharged by a `resolutions` entry.

- [ ] **Step 1: Write failing tests:** a proposal in the cycle result → a `revision_proposed` frontier item with op `revise_model`, consequence `review`; `closure()` unaffected (`closed` unchanged by the new kinds); `plan_candidate_actions` emits a `REVISE` action carrying `consequence`; `persist_cycle` appends to `revision_proposals[]` idempotently (same id not duplicated).
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, verify pass** (existing frontier/closure tests unchanged).
- [ ] **Step 5: Commit.**

## Task 10: `discover revise --approve|--reject` + decider seam

**Files:**
- Modify: `discovery_engine/discover.py` (`revise` CLI verb: `--approve`/`--reject --rationale`; `history` events; extend the `execute_action` `steer` branch ~`:610` to handle `revise_model` via the decider, `by: discovery-ai`)
- Test: `discovery_engine/tests/test_revise_cli.py`

**Interfaces:**
- Produces: `discover revise <inv> <proposal-id> --approve|--reject --rationale "…"` appends a `history` event; `--approve` sets `status: approved`, `--reject` sets `rejected` (frontier item discharges). `execute_action(..., decider, autonomous=True, target="rp:…")` records `by: discovery-ai`. (The `--apply` path is Task 12.)

- [ ] **Step 1: Write failing tests:** approve appends `{event: approved, by: human, rationale}` and flips status; reject flips to `rejected` and the next cycle's frontier omits it; the decider seam records `by: discovery-ai`.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** (history is the only mutation; no verdict/evidence change).
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit.**

> **Wave 2 exit (ledger note):** on genesis, S3's 2D/3D disagreement → one versioned proposal (affected mv + composite candidate if `membrane_3d` resolves + discriminating test), shown as a review item; approve/reject changes only history; a re-run does not duplicate it.

## Task 11: `_inherit_arm` composite override + `materialize_design`

**Files:**
- Modify: `discovery_engine/study_seeding.py` (`_inherit_arm` ~`:60-72` honours `model_change.composite`)
- Modify: `discovery_engine/experiment_design.py` (`materialize_design`)
- Test: `discovery_engine/tests/test_materialize_design.py`

**Interfaces:**
- Produces: `materialize_design(design, parent_spec, inv_spec, model_version) -> dict` (pure study dict; `_write_seeded_study` does I/O) — baseline via `build_seeded_study(parent, inv, new_slug, model_change=…)`; one `variants[]` arm per `design.interventions` (parameter-valued `when` → `params` override, else arm `needs_author`); control arms from `design.controls`; child `hypotheses[]` from `design.predictions` with `origin: design:<id>`. `_inherit_arm` applies a `model_change.composite` override alongside `params`.

- [ ] **Step 1: Write failing tests:** `_inherit_arm` with `model_change={composite: X}` → arm composite becomes X (params override still works); `materialize_design` emits one variant per intervention, marks controls, and writes child hypotheses with `origin`; a non-parameter `when` → arm `needs_author: true`.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit.**

## Task 12: `apply_revision` + `discover revise --apply` + `invalidate_dependents`

**Files:**
- Modify: `discovery_engine/discover.py` (`apply_revision`; `revise --apply [--change N]`)
- Modify: `discovery_engine/evaluation.py` (wire existing `invalidate_dependents` ~`:149-170`)
- Test: `discovery_engine/tests/test_apply_revision.py`

**Interfaces:**
- Produces: `apply_revision(ws_root, inv_slug, proposal_id, change_index=0) -> {"study_path", "model_version"}` — per decision §4 "Apply": picks the candidate change; `composite` → `build_seeded_study(..., model_change={composite})`; `param` → `model_change={params}` + `refine_study(child, needs=[SWEEP], …)`; writes child via `_write_seeded_study`; appends engine-origin disjoint-band hypotheses (`origin: {kind: revision, ref}`) + finding stubs with `from_test` set and `statement: null`; registers mv with `parent_ids`; adds `pipeline_gate.prerequisites: [{study: parent, relation: alternative-to}]`; appends `applied` history with `child_study`+`model_version`; calls `invalidate_dependents({kind: model, id: parent})` (display-only in the cycle result). **Refuses** (writes nothing, reports `needs_build` + mechanism) unless `resolve_composite(candidate).resolves` and `is_run_ready(child)` is empty.

- [ ] **Step 1: Write failing tests:**
  - `test_apply_composite_candidate`: approved proposal with a resolving composite candidate → child study on disk, `parent_studies`/`alternative-to` prerequisite set, two disjoint-band hypotheses with `origin`, finding stub with `from_test`+`statement: null`, new mv with `parent_ids`, `applied` history.
  - `test_apply_build_route_refuses` (**Review Focus: BUILD-route refusal**): candidate composite does not resolve → nothing written, result reports `needs_build` with the mechanism.
  - `test_alternative_to_blocks_closure` (**Review Focus: closure**): after apply, the derived `challenge` edge makes `closure().closed` False until the child's discriminating test passes.
  - `test_invalidate_marks_parent_review`: parent hypotheses/claim show `REVIEW_REQUIRED` in the cycle result; no authored field changed.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit.**

## Task 13: `advance_cycle` eligibility + per-mv skip + post-run sync

**Files:**
- Modify: `discovery_engine/discover.py` (`advance_cycle` ~`:796-866`)
- Test: `discovery_engine/tests/test_advance_revision.py`

**Interfaces:**
- Produces: `advance_cycle` eligible ops become `test`, `branch`, `revise_model` — the latter two only when the target resolves to an on-disk, `run_readiness.ready` study (i.e. the proposal is already applied). Skip rule: skip iff a run exists whose arm mv equals the target arm's current mv (compute via Task 5 `model_version(params/variant, composite)`); a `refine_study --write` composite raise must re-run. Post-run: if the runner's `study.yaml` lacks `computed_outcomes` for the new run, call `study_outcomes.sync(study_dir)` (best-effort). **Verify first** (ledger note) whether `vivarium-workbench run study` already syncs — if yes, add nothing.

- [ ] **Step 1: Write failing tests (fake runner):**
  - `test_advance_runs_applied_revision`: an applied child (run-ready) is picked and run by a fake `runner` that writes a `runs_meta` row + a tiny store.
  - `test_per_mv_skip` (**Review Focus: no-loop**): a study with a run for its current mv is skipped; a revised study (new mv) is NOT skipped; `max_runs`/`seen` prevent loops.
  - `test_unapplied_proposal_is_gated`: a `review`-tier proposal with no applied child is never run by advance.
- [ ] **Step 2: Run, verify fail.**
- [ ] **Step 3: Implement** (reuse the injectable `runner` seam).
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Commit.**

## Task 14: Two-cycle closure acceptance test + `evaluated` outcome

**Files:**
- Modify: `discovery_engine/discover.py` (`persist_cycle` writes `evaluated: {outcome}` on a proposal when the next delta shows the source disagreement resolved/persisting)
- Create: `discovery_engine/tests/test_closed_loop_two_cycle.py` (the acceptance test)
- Create/extend: `discovery_engine/tests/fixtures/` (a minimal investigation + a fake runner writing a `runs_meta` row + a tiny parquet/sqlite store `compute_outcomes` can grade; mirror `tests/test_computed_outcomes.py`)

**Interfaces:**
- Consumes: everything above.
- Produces: proposal `history`/`evaluated` closure; the end-to-end test is the Wave-3 exit gate.

- [ ] **Step 1: Write the acceptance test** (`test_closed_loop_two_cycle`): fixture with an S3-style disagreement →
  cycle 1: `run_cycle` → one `revision_proposed` on the frontier;
  `discover revise <inv> <id> --approve --apply` → child study on disk, run-ready, discriminable hypotheses;
  `advance_cycle(autonomous=True, runner=fake)` → run recorded, `computed_outcomes` + finding slots filled;
  cycle 2: `discovery_delta(prev=snapshot).changed` names the child + new mv; the proposal is `evaluated`; the frontier either closes the disagreement or lists a new proposal.
- [ ] **Step 2: Run, verify fail** (the `evaluated` write + any seams not yet connected).
- [ ] **Step 3: Implement** the `evaluated` outcome write in `persist_cycle` and connect remaining seams.
- [ ] **Step 4: Run, verify pass.**
- [ ] **Step 5: Run the whole engine suite** `pytest discovery_engine/tests/ -q` (all green; `test_no_workbench_import` included), then commit.

> **Wave 3 exit:** `test_closed_loop_two_cycle` green; the same path exercised once on the genesis instance in the workbench sandbox (manual, ledger note).

---

## Companion work (not tasked here — small, separate)

Workbench side (repo `vivarium-workbench`, separate worktree/PR): add `propose_revisions` + `apply_revision` steps to `lib/cycle_trace.STEPS` (apply via `sandbox_advance`, review-gated; `apply_cycle` never auto-approves), and surface proposals/`REVISE` actions in the discovery-cycle panel's gate list (the panel already renders frontier items + consequence tiers). Zero new engine imports. Tracked after the engine lands.

## Deferred (Wave 4 — only if the loop turns on a real instance)

`select_for_launch` wired into `advance`; `claim_requires[]` + a `claim_scope` validator; relative-spread disagreement threshold using `evidence.units`; `manifest_json.result_fingerprint` checksums; reconciling the panel's `runnable` count with what `advance` runs. None blocks the loop.

---

## Self-review (run against the spec)

1. **Spec coverage:** decision §1 primitives — value-lineage (T1), quantitative-support+slug (T2), missing_members (T3), resolve_composite+catalog (T4), model_version (T5), bind_model_versions+persist+slot+schema (T6), snapshot+delta(prev) (T7), propose_model_revisions (T8), frontier/plan+persist proposals (T9), revise CLI+decider (T10), materialize_design+inherit_arm (T11), apply_revision+invalidate (T12), advance eligibility+per-mv skip (T13), two-cycle acceptance+evaluated (T14). All six new primitives + five strengthened ones + three persisted blocks + the finding slot are covered. Cuts (§5) are explicitly out of scope.
2. **Review Focus:** VERIFIED edges → T1; mv churn → T5; schema back-compat → T6; proposal dedupe/cap → T8; BUILD-route refusal → T12; advance no-loop → T13; closure correctness → T9/T12. Each pinned to an owning task's tests.
3. **Type/interface consistency:** `model_version(composite, params, parent_ids, origin)` (T5) is consumed identically by `bind_model_versions` (T6) and `apply_revision` (T12); `resolve_composite` (T4) consumed by `run_readiness` (T13) and `bind_model_versions` (T6); `propose_model_revisions` output shape (T8 = decision §3.4) consumed by frontier (T9) and apply (T12); `discovery_delta(prev)` (T7) consumed by the acceptance test (T14).
4. **Proportion:** the plan is signatures + test assertions, not code bodies; the binding detail lives in the decision doc (the Spec), referenced per task.
