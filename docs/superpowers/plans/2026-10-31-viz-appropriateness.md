# Visualization Appropriateness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:subagent-driven-development.

**Goal:** Land the visualization-APPROPRIATENESS check — the third leg of the viz triad (freshness="is it current?", proposals="is it missing?", appropriateness="is a DECLARED viz shown in the right place?"). Flags `misplaced_scope` (an investigation-scope viz hosted under a plain study), `untied` (no covers/requires), and `duplicated_cross_arm`; exempts synthesis studies (build-on ≥2 arms). Purely derived, non-blocking — mirrors `viz_freshness`/`viz_proposals` 1:1.

**Base:** this branch = current `main` + the peer's `proto/viz-appropriateness` (`discovery_engine/viz_appropriateness.py` + `tests/test_viz_appropriateness.py`, 13 tests; preserved with attribution). Harden the module against the review findings, then wire it like the other viz legs.

**Spec / authority:** `discovery_engine/viz_freshness.py` + `discovery_engine/viz_proposals.py` + the viz frontier wiring in `discovery_engine.py` / `discover.py` (all on main) — the templates. The code-review findings (below) are the correctness checklist.

## Global Constraints
- Mirror `viz_freshness`/`viz_proposals`: PURE projection (no I/O, no clock, no `scientific_judgment`); NON-BLOCKING (no appropriateness kind in `_CLOSURE_BLOCKING`); deterministic.
- Domain-general (passes `tests/test_no_domain_coupling.py`).
- A pure function over user YAML must NEVER raise on malformed input.
- NO AI attribution in commits. No `git add -f` under `.superpowers/`. Full engine suite green (`PYTHONPATH="$PWD" ~/code/discovery-engine/.venv/bin/python -m pytest -q`).

## Review Focus (correctness checklist — from the prototype review)
Important: **(1) synthesis exemption reads only the legacy `parent_studies`** — misses the CANONICAL `pipeline_gate.prerequisites` and `parent_relations` (see `discover.py` `_set_relation_inplace`, `argument_store.py`), so it FALSE-FLAGS the very synthesis studies it should allow; and it counts parents by entry not distinct study. **(2) `duplicated_cross_arm` false positives** — flags a legitimate synthesis re-host as a dup; the same address twice in one study counts as 2; a name-only (no address) collides on name. **(3) `_covers` omits `config.inputs_map` values** (disagrees with `viz_proposals.covered_observables` → the two legs disagree on "tied"), and crashes (`TypeError`) on malformed `covers` like `[["x"]]`. **(4) Silent inertness** — an unresolved class → UNKNOWN scope → no issues; in the headless path (`viz_catalog=None`) the whole check is inert; `ok` can be True for an UNKNOWN-scope viz (should read "not assessed", not "OK"). Minor: stale docstring ("arms/labels" → arms only); `untied` fires on unresolved/non-class entries (high legacy rate → rollout flag); the non-blocking test asserts invented strings (vacuous); 4 mutation SURVIVORS = test omissions (synthesis with a non-`builds-on` relation; a `requires`-only tie; a plain study's roll-up row flagged misplaced; case/whitespace normalization).

---

## Task 1: Harden the pure module (`viz_appropriateness.py`)
**Files:** modify `discovery_engine/viz_appropriateness.py`; `tests/test_viz_appropriateness.py`.
- [ ] (1) `is_synthesis_study`: gather build-on edges from ALL of `pipeline_gate.prerequisites`, `parent_relations`, and legacy `parent_studies`, DEDUPED by distinct study; a `builds-on` to ≥2 distinct parents → synthesis. (Confirm the exact canonical shapes by reading `discover.py` `_set_relation_inplace` + `argument_store.py`.)
- [ ] (2) `duplicated_cross_arm`: dedupe hosting studies by slug; EXCLUDE synthesis hosts (a synthesis re-host is legitimate, not a dup); key on address (fall back to name only when no address, and don't collide unrelated names).
- [ ] (3) `_covers`: also count `config.inputs_map` VALUES as ties (align with `viz_proposals.covered_observables`); guard against malformed `covers`/`requires` so it NEVER raises (coerce safely).
- [ ] (4) UNKNOWN scope: a viz whose scope can't be resolved yields `state: "not_assessed"` (NOT `ok:True`, NOT an issue) — distinct from "appropriate".
- [ ] Fix the 4 mutation survivors with real tests: synthesis with a non-`builds-on` relation is NOT synthesis; a `requires`-only tie counts as tied; a PLAIN study's roll-up row carries `misplaced_scope` (test `study_viz_appropriateness`, not just `classify_viz`); case/whitespace normalization. Replace the vacuous non-blocking test with one that fires if a real kind is added to `_CLOSURE_BLOCKING`.
- [ ] Update the stale docstring (arms only, not "arms/labels"). Full suite green; commit.

## Task 2: Wire into the engine (frontier + schema + scope provider)
**Files:** modify `discovery_engine/discover.py`, `discovery_engine/discovery_engine.py`, `schemas/investigation-extensions.schema.json`.
**Mirror:** the `viz_proposed`/`viz_*` frontier projection + how `viz_catalog` is threaded into `run_cycle` (the workbench supplies it — reuse that path).
- [ ] A `scope_of` provider that resolves a viz address to its input-port schema from the injected `viz_catalog` (the same catalog the viz-loop already threads); in the headless path (no catalog) scopes are UNKNOWN → "not_assessed", never a false alarm.
- [ ] Call `investigation_viz_appropriateness` in `run_cycle` beside the other viz projections; frontier kinds `viz_misplaced` / `viz_untied` / `viz_duplicated` (REVIEW-TIER, advance never auto-executes) with `_PRIORITY`/`_OP`/`_OPP_INFO`/`_OPP_COST`/`RESOLUTION_KINDS` entries; NOT in `_CLOSURE_BLOCKING`; a census in `build_cycle_state`. `untied` behind a rollout flag (off by default) to avoid flooding legacy studies.
- [ ] Schema: an `appropriateness` block (or extend the viz census) — validated on save.
- [ ] Tests (mirror the viz T3 frontier tests): the three kinds project non-blocking; UNKNOWN→not_assessed is inert; full suite green; commit.

## Task 3: Two-cycle / acceptance test (engine wave exit)
**Files:** `tests/test_viz_appropriateness_acceptance.py`.
- [ ] On a fixture investigation: a cross-arm (investigation-scope) viz hosted under a PLAIN study surfaces `viz_misplaced` (non-blocking); the same viz under a SYNTHESIS study (builds-on ≥2, declared the CANONICAL way via `pipeline_gate.prerequisites`) does NOT; an `untied` viz surfaces only with the rollout flag; a duplicated cross-arm viz across two plain studies surfaces `viz_duplicated` once; a pre-aggregated viz with explicit `scope: investigation` is handled; UNKNOWN-scope (no catalog) is "not_assessed", surfaces nothing; closure unaffected throughout. Watch fail, connect minimally, watch pass. Full suite green; commit. **Engine wave exit.**

## Task 4: Workbench UI (SEPARATE repo — vivarium-workbench)
> Dispatched against a vivarium-workbench worktree off main, after the engine lands. Surface the appropriateness items on the discovery-cycle panel (misplaced/untied/duplicated), each with its rationale; a remediation affordance ("move to investigation level" / "declare scope") — at minimum a clear flag + census, since the move-remediation has no engine action yet. Detailed brief authored when T1–T3 are green.

## Self-Review
- Spec coverage: T1 fixes the 3 Important correctness bugs + the 4 test survivors; T2 wires frontier/schema/scope; T3 proves it end-to-end; T4 is the UI. Mirrors the viz-loop's module→wire→frontier→acceptance→UI shape.
- Review Focus → T1 owns the correctness findings; T3 owns the synthesis-exempt + non-blocking + not_assessed end-to-end checks.
