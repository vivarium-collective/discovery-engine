# Schema consolidation proposal — `investigation.yaml` & `study.yaml`

**Status:** Proposed (draft, for discussion)
**Date:** 2026-11-28
**Related:** the [investigation & study field map](field-map.html) (PR #216) — this proposal is built from what that map surfaced.

---

## Why

The field map quantified the slack: both schemas carry far more fields than
either the **workbench** or the **engine** uses. The excess falls into three
kinds, and each is treated differently here:

1. **Legacy duplicates** — two fields for one thing, one superseded. Safe to drop
   after a back-compat migration.
2. **Schema-defined but never written** — properties in the JSON schema that no
   instance populates and no code reads. Latent complexity; safe to delete from
   the schema.
3. **Unused authoring / seed blocks** — prose and plans neither system reads.
   *Not* safe to delete on software grounds alone — a human may rely on them, so
   these are **relocated**, not deleted.

**Goal:** cut field count and redundancy without touching intentional design.

## Principles

- **Keep intentional parallel slots.** The authored-vs-computed pairs
  (`verdict_status` / `computed_verdict_status`, `runs[].outcomes` /
  `runs[].computed_outcomes`) are *by design*, not duplication. They stay.
- **"Unused by software" ≠ "useless to humans."** Authoring prose is relocated
  to a plan doc, not dropped.
- **Migrate, don't just delete.** Every removal ships with a back-compat reader
  (accept the old key, prefer the new) and a comment-preserving migration.
- **The schema carries only what a tool reads/writes, or a human authors with
  purpose.** Everything else leaves the canonical schema.
- **Domain-neutral.** The engine serves any ecosystem; field names should not
  bake in one domain.

---

## Decisions already taken

- ✅ **Consolidate the claim/argument containers** into one `argument` model.
- ✅ **Replace `biological_story`** with a general, domain-neutral long-form field
  (it bakes "biology" into a domain-agnostic schema), folding legacy prose in.

All remaining open questions have since been resolved — see
**Decisions (resolved)** below.

---

## Target model 1 — expand `scientific_argument` (investigation)

> **The name `argument` is already taken.** It is the *derived* RFC-0005 Claim +
> argument-graph store (`discovery_engine.projection.argument.derive_argument`,
> merged authored-over-derived). So the authored containers consolidate into the
> existing **`scientific_argument`** block — **not** a new `argument` key, which
> would collide with the derived store.

**Today — six overlapping containers:** `scientific_argument`, `key_claims[]`,
`claims[]`, `hypotheses[]`, `competing_frameworks[]`, and the free-text
`synthesis`. The workbench Background tab and the engine's coverage corpus both
read across these; nothing needs them separate.

**Target — an expanded `scientific_argument:`:**

```yaml
scientific_argument:
  main_claim: ""
  evidence_for: []
  evidence_against: []
  caveats: []
  key_figures: []
  claims:                           # merges key_claims[] + claims[] (the cited ledger)
    - {id, statement, cites, status}
  hypotheses:                       # moved in, unchanged shape
    - {id, statement, predictions, status, competing_with}
  competing_frameworks:             # moved in
    - {name, relation}
```

`synthesis` splits: its prose goes to `background` (below); any structured
`evidence_chain` folds into `scientific_argument.claims`. Then `synthesis`,
`key_claims`, `claims`, top-level `hypotheses` and `competing_frameworks` are
removed.

## Target model 2 — one general `background` field (investigation)

**Today — four free-text prose fields:** `lead`, `description` (legacy),
`biological_story` (domain-specific name), and `executive.what_is_this`.

**Target:**

- `executive.what_is_this` — the short overview lede (**keep**).
- `background` — **one general, domain-neutral long-form markdown field.**
  **Replaces `biological_story`** and **absorbs legacy `description`** and the
  prose half of `synthesis`.
- `lead` — *open question:* fold into `background`'s first paragraph, or keep as
  a separate short intro. (Recommend fold.)

Rationale: `biological_story` names a domain in a schema that must stay
domain-agnostic; `background` serves any investigation and collapses three prose
fields to one.

## Target model 3 — `engine:` namespace & flat study config

**Investigation — one `engine:` block.** The engine's closed-loop state (today
~19 top-level keys, most of them instance-only) moves under one schematized key:

```yaml
engine:
  model_versions: []        # was top-level (extensions schema)
  viz_proposals: []
  capability_gaps: []
  revision_proposals: []
  cycle_state: {}
  decision_log: []          # was instance-only, un-schematized
  reconciliation_log: []
  executed_actions: []
  lifecycle_history: []
  resolutions: []
  cycle_flags: {}
  finding_candidates: []
  discoveries: []
  review_decisions: []
  # … feedback routing / validation snapshots
```

**Study — flat config.** The v4 `conditions{}` wrapper is dropped; the canonical
layout is top-level `baseline[]` / `variants[]` / `observables[]`, with the
tunable knobs promoted to a top-level `model_settings[]`.

---

## Full per-field plan

Actions: **KEEP** · **DROP** (delete, with back-compat) · **MERGE→** (fold into
another field) · **RELOCATE→** (out of the canonical schema) · **NAMESPACE→**
(group under a parent key, no data lost). Only fields that change are listed;
everything not listed is **KEEP**.

### `investigation.yaml`

| Field | Action | Notes | Tier |
|---|---|---|---|
| `biological_story` | MERGE→ `background` | general name; see model 2 | ✅ decided |
| `description` | MERGE→ `background` | legacy prose | ✅ decided |
| `lead` | MERGE→ `background` | folded in as first paragraph | ✅ decided |
| `scientific_argument` | EXPAND | absorbs the containers below (see model 1); `argument` is the derived store, so it stays the authored container | ✅ decided |
| `key_claims[]` | MERGE→ `scientific_argument.claims` | | ✅ decided |
| `claims[]` | MERGE→ `scientific_argument.claims` | instance-only ledger | ✅ decided |
| `hypotheses[]` | MOVE→ `scientific_argument.hypotheses` | | ✅ decided |
| `competing_frameworks[]` | MOVE→ `scientific_argument.competing_frameworks` | | ✅ decided |
| `synthesis` | SPLIT→ `background` + `scientific_argument` | then remove | ✅ decided |
| `executive.computed_verdict_status` + `computed_acceptance` | NAMESPACE→ `executive.computed{}` | one computed block | propose |
| `decision_log`, `reconciliation_log`, `executed_actions`, `lifecycle_history`, `resolutions`, `cycle_flags`, `finding_candidates`, `discoveries`, `review_decisions`, `applied_feedback`, `pending_feedback`, `critique_reports`, `validation_snapshot`, `model_acquisition_proposals` | NAMESPACE→ `engine:{}` | engine-owned, instance-only today; schematize under one key | ✅ decided |
| `model_versions`, `viz_proposals`, `capability_gaps`, `revision_proposals`, `cycle_state` | NAMESPACE→ `engine:{}` | already the extensions-schema cluster | ✅ decided |
| `transition_barriers`, `candidate_composites`, `candidate_software`, `flagship_studies`, `final_deliverable`, `regimes`, `model_families`, `physical_scalings`, `first_cycle_priority`, `promotion_rules`, `seed_acceptance_checklist`, `seed_id`, `plan_acts`, `object_of_evaluation` | DROP | removed from schema — **neither system reads any of these** | ✅ decided |
| `proposed_inputs` | KEEP + WIRE | integrity guardrail; render pending items as accept/decline cards in the **Decisions tab** (backend `decide_proposed_input` already exists) | ✅ decided |

Kept as-is: the authored/computed verdict slots, `studies`, `acceptance_criteria`,
`executive.*` verdict/decision fields, `how_to_read`, `glossary`, `contributors`
(legit optional features), `plan_gates`, `narrative.questions`, `inputs`,
`expert_docs`, `discovery.*` (engine config).

### `study.yaml`

| Field | Action | Notes | Tier |
|---|---|---|---|
| `status` | DROP | legacy single-axis; superseded by the multi-axis set | safe |
| `phase` | DROP | derive for display from the axes | safe |
| `confidence` | DROP | workbench *derives* it (`derive_confidence`); engine uses `verdict`/`gate_status` | safe |
| `implementation_status` | DROP (schema) | **absent from all ~40 studies** | safe |
| `expert_review_status` | DROP (schema) | absent from all studies | safe |
| `objective` | DROP | deprecated; superseded by `experiment` | safe |
| `purpose.question` | MERGE→ `question` | duplicate | safe |
| `concept` | MERGE→ `claim`/`question` | instance-only | safe |
| `conclusions` | MERGE→ `conclusion` | plural dup of singular | safe |
| `hypothesis` | MERGE→ `claim` | deprecated; engine reads it, so back-compat required | ✅ decided |
| `expected_behavior` | DROP | legacy v3 DSL; superseded by `behavior_tests[]` | safe |
| `tests` (config block) | MERGE→ `behavior_tests` | | structural |
| `conclusion_logic` | DROP (schema) | absent | safe |
| `report`, `study_card`, `conclusion_verdicts`, `biological_summary`, `design_pivot_required` | DROP (schema) | v4 blocks, never adopted | safe |
| `model_change`, `implementation_requirements`, `implementation_tasks`, `runtime`, `enforced_params`, `expert_questions`, `bibliography`, `seeded_from`, `follow_up_of`, `followup_proposals` | DROP (schema) | schema-only, absent from all instances | safe |
| `parent_relations[]` | MERGE→ `parent_studies[]` | legacy back-compat edges | structural |
| `builds_on[]` | MERGE→ `parent_studies[].relation: builds-on` | underscore alias | structural |
| `handoff`, `plan_id` | MERGE→ `upstream_state_consumed[]` | numeric plan-id dataflow | structural |
| `conditions{}` | DROP (flatten) | adopt flat `baseline`/`variants`/`observables`; promote `conditions.model_settings`/`expert_inputs` → top-level `model_settings[]` | ✅ decided |
| `readouts[]` *vs* `observables[]` | MERGE→ `observables[]` | two observable declarations | structural |
| `variants[].base`/`.over` *vs* `.params` | ONE encoding | recommend `base`/`over` (matches instances) | structural |
| `discovery_implications.followup_study_proposals` | canonical followups | reconcile the one follow-up encoding | structural |
| `runs[]` | DEPRECATE | `runs.db` is canonical; mark deprecated now, back-compat read for one window, then remove | ✅ decided |

Kept as-is: `claim`, `question`, `experiment`, `result`, `conclusion`, the kept
status axes (`design_status`, `simulation_status`, `evaluation_status`,
`gate_status`, `execution_status`, `verdict`, `evidence_tier`), `baseline`,
`behavior_tests`, `pipeline_gate`, `findings`, `runs[].outcomes` /
`computed_outcomes` (parallel slots), `arms`, `visualizations`, `narrative.*`,
`parent_studies`.

---

## Decisions (resolved)

1. **Config layout → flat.** Drop the v4 `conditions{}` block; the canonical
   layout is top-level `baseline` / `variants` / `observables`, with
   `conditions.model_settings` / `expert_inputs` promoted to a top-level
   `model_settings[]`.
2. **Seed / authoring blocks → drop.** `transition_barriers`,
   `candidate_composites`, `candidate_software`, `flagship_studies`,
   `final_deliverable`, `regimes`, `model_families`, `physical_scalings`,
   `first_cycle_priority`, `promotion_rules`, `seed_acceptance_checklist`,
   `seed_id`, `plan_acts`, `object_of_evaluation` are removed from the schema (not
   relocated).
3. **Namespace engine writes → yes.** The engine's instance-only writes and the
   extensions-schema cluster move under one `engine:` block and become a
   validated contract (Target model 3).
4. **`lead` → fold into `background`.** No separate short-intro field.
5. **`runs[]` → deprecate.** Mark the inline array deprecated now; `runs.db` is
   canonical. Back-compat read for one window, then removed.
6. **Study `hypothesis` / `concept` → fold** into `claim` / `question`.

**`proposed_inputs` → keep and wire.** It is an integrity guardrail — no outside
reference or mechanism enters `inputs` without an explicit expert accept/decline.
The backend already exists (`decide_proposed_input`, `POST
/api/proposed-input-decision`, promote-on-accept into `inputs.references`); the
only gap is UI. Resolution: render pending items as accept/decline cards in the
**Decisions tab**, beside the engine decisions. (Tracked as a workbench change.)

---

## Migration & rollout

- **Back-compat readers first.** `discovery_engine.investigation.canonicalize`
  and the workbench loaders accept the old keys, prefer the new, and emit the
  canonical shape — so old workspaces keep working through a deprecation window.
- **Comment-preserving migration** (ruamel round-trip) run across `viva-genesis`
  and the other workspaces; a `--check` mode for CI (mirror
  `tests/test_cycle_spec_drift.py`).
- **Blast-radius grep before each removal** across the consumers: `viva-superpowers`
  (skills), `vivarium-workbench` (UI/server), and every workspace. A field is
  removed from the schema only after no consumer references it.
- **Schema deprecation window** — mark superseded props `deprecated: true` for one
  release before deletion.
- **Update the field map afterwards** — once the slim schema lands, update the
  `INV`/`STUDY` arrays and the `SCH_*`/`TAB_*` maps in `docs/field-map.html` to
  match. (This is the agreed "update the map after the plan" step.)

## Projected reduction

| File | Field-groups today | After | Cut |
|---|---|---|---|
| `investigation.yaml` | ~62 | **~38** | prose + argument merge, drop seed blocks, ~19 engine keys → one `engine:` |
| `study.yaml` | ~55 | **~32** | status-axis collapse, deprecated-duplicate drops, schema-only removals, flat config, deprecate `runs[]` |

## Non-goals

- Not changing the **authored ↔ computed** design (verdict/outcome parallel slots).
- Not removing the engine's **closed-loop fields** — they're used; they get
  namespaced and schematized, not dropped.
