# The AI-Drivable Discovery Cycle — Design Spec

**Status:** draft for review · **Date:** 2026-10-30 · **Repos:** discovery-engine (core), viva-superpowers (fillers/skills), vivarium-workbench (panel)

## Intent — why this exists

The discovery cycle today has two halves that barely touch. The **engine**
(`discovery_engine`) is a pure, derive-first projection: it reconciles evidence,
evaluates claims, computes the frontier, and *already emits typed "slots"* for the
work it thinks should happen next — `suggest_studies`, `opportunities` (with
`proposed_validator: None`), `ExperimentDesign` (with `model_version: None`),
`ModelBuildRequest`, `candidate_action`. The **generative layer**
(viva-superpowers skills, human- or LLM-driven) does the creative science — propose
a study, build a model, author tests, interpret findings.

But the two only meet through **loose study.yaml fields** the engine later derives
from, plus one skill (`/viva-harden-investigation`) that reads the frontier via a
**prose op→skill table a human follows**. The engine's typed slots are *printed as
text* and never consumed. Model-sourcing is **duplicated and divergent** (engine
`model_architect` reuse/wrap/build, unused; `/viva-model-build`'s `module_sourcing`
reuse/compose/build-new, used). The only wired autonomy (`decider` /
`$VIVA_DECIDER_CMD`) automates **decisions and runs — never generation**; the
"AI proposes the next study" reactor is a literal `stub` in `event_client.py`.

The cost: prose intent (e.g. authored "bookend studies S0/S6") is invisible to the
engine; model-space exploration happens inside a skill, invisible to the cycle; and
a cycle cannot run without a human reading text and re-invoking skills by hand.

This spec formalizes the seam into a **typed generation contract** with
**per-stage review gates**, so a discovery cycle can be driven by a human, by AI, or
by any mix — gated exactly where the operator wants — while the engine stays pure
and LLM-free.

## Goals

- **One typed generation contract** (`GenerationRequest → GenerationResult`) that
  every generative act flows through: propose-study, acquire/build-model,
  author-tests, interpret-finding, resolve-decision, explain, visualize.
- **The engine emits requests, not text.** `run_cycle` produces structured
  `GenerationRequest`s derived from its existing slots; nothing is "printed to be
  re-read by a human."
- **Per-stage gates, operator-controlled from the panel.** Each request *kind* has
  a gate `{auto | review}`, configurable and persisted per investigation, with
  presets from gate-everything to free-run. A gated result queues for review; an
  auto result applies and the cycle continues.
- **Fully human-free capable.** With all gates off and an AI filler wired, a cycle
  runs end to end — propose → model → build → run → interpret → promote → refine —
  with no human, while every step is still recorded and auditable.
- **One model path.** `model_architect` (engine) and `module_sourcing`
  (`/viva-model-build`) are reconciled so model-space exploration is a first-class
  cycle opportunity, not a skill-local loop.
- **Viz + explanation are cycle outputs.** Each promoted finding/claim carries a
  generated visualization and a plain-language explanation, produced through the
  same contract and surfaced in the panel and the report.
- **Formalized fields.** The contracts have real schemas (JSON Schema), not loose
  dicts.

## Non-goals

- Replacing the existing `/viva-*` skills. They become **fillers** behind the
  contract; the human path is unchanged.
- Changing the deterministic spine (`select` / `seed`-scaffold / `run`-orchestrate
  / `refine` / `resolve`). It keeps working; we add the typed request layer over it.
- Building new science models or a new simulation runtime.

## Anti-goals (a failure even if it "works")

- **The engine must never import or call an LLM.** All generative/AI work lives in
  a *filler*, injected across the seam (as `decider` already is). An engine that
  calls a model is a regression, not a feature — it breaks determinism,
  reproducibility, and the AI-free dashboard guarantee.
- **No silent, ungated mutation.** Free-run is a *choice the operator makes in the
  panel*, never a default. Every applied result is recorded with its filler,
  rationale, and provenance so a human-free run is still fully reconstructable.
- **No third parallel path.** If this adds a *third* model-sourcing or proposal
  mechanism beside the engine's and the skill's, it has failed — the point is to
  unify.

## Constraints (carried from the ecosystem)

- **Derive-first / pure:** every engine function stays a pure projection over
  structured state. `GenerationRequest` emission is a projection; *filling* a
  request happens outside the engine.
- **The decoupled seam holds:** discovery-engine never imports its consumers
  (CI-enforced by import-linter). The filler protocol is injected, not imported.
- **Back-compat:** existing fields (`followup_proposals[]`, `behavior_tests[]`,
  `findings[]`, `conclusion_verdicts`) keep working; a `GenerationResult`'s artifact
  is written into those same fields. The contract is additive.
- **viva-superpowers is the facade:** skills and the AI filler live there; the
  engine exposes the request/result types and the dispatch points.

## The design

### 1. The generation contract

Two types, schema'd in `discovery_engine/schemas/` and mirrored as dataclasses.

```
GenerationRequest {
  id:         str                 # stable, content-addressed on (kind, target, provenance)
  kind:       "propose_study" | "acquire_model" | "author_tests"
            | "interpret_finding" | "resolve_decision" | "explain" | "visualize"
  target:     str                 # investigation / study / claim / decision / finding id
  produces:   str                 # the contract the artifact must satisfy
                                  #   e.g. "study.yaml/v4", "model_spec/v1",
                                  #   "behavior_tests[]", "finding/v1", "decision/v1",
                                  #   "explanation/v1", "viz_spec/v1"
  context:    dict                # the typed slot payload + the cycle state the filler needs
                                  #   (hypotheses to discriminate, competing predictions,
                                  #    parent study, catalog routes, evidence summary, …)
  rationale:  str                 # why the cycle wants this (from the frontier/opportunity)
  value:      {info, impact, enable, cost, closure, discriminate, score}  # for prioritization
  provenance: {source, op, opportunity_id}   # which frontier op / opportunity produced it
  gate:       "auto" | "review"   # resolved from GateConfig at emit time
}

GenerationResult {
  request_id: str
  filler:     {kind: "human" | "ai", id: str, model?: str}
  artifact:   dict                # content satisfying `produces`
  confidence: float | null        # the filler's self-reported confidence (AI) or null (human)
  rationale:  str
  status:     "applied" | "queued_for_review" | "rejected"
  applied_to: [str]               # the fields/files written (for audit + undo)
}
```

Every generative act is one `(request, result)` pair. The artifact is written into
the **existing** structured fields (so the rest of the engine derives from it as
today) — the contract is the *envelope*, not a new storage format.

### 2. The cycle emits requests

The projection is **pure** and takes the already-derived cycle projections as
inputs — it does **not** read disk (the caller computes `opportunities`,
`candidate_actions`, and the Phase-A `evidence_state` and passes them in; building
`evidence_state` needs `runs_by_study`, which the caller loads). This keeps
`generation_requests` a pure function and the engine disk-free:

```
generation_requests(
    inv_spec, studies, *,
    cycle_result,        # run_cycle bundle (suggestions/argument/audit/open_calls/…)
    opportunities,       # discovery_engine.opportunities(cycle_result, inv_spec)
    candidate_actions,   # candidate_action.plan_candidate_actions(cycle_result, inv_spec, evidence_state)
    evidence_state,      # evidence_integrity.reconcile_evidence_state(inv_spec, studies, runs_by_study)
    gates,               # GateConfig
) -> list[GenerationRequest]
```

The mapping is keyed on the **frontier op** and the slot's state, not on loose
heuristics (in particular not on `opportunities[].proposed_validator`, which is
hard-coded `None` for every opportunity today and so can't select anything):

| Source + condition | frontier `op` | → Request kind |
|---|---|---|
| `suggest_studies` entry (divergence / open-question / followup) | `branch` | `propose_study` |
| required mechanism with no bound `model_version` (§5) | `branch`/`revise` | `acquire_model` |
| study with a `pending_gate` / missing `behavior_tests` | `test` (pre-run) | `author_tests` |
| `candidate_action` RESOLVE on a claim lacking a finding | `synthesize` | `interpret_finding` |
| `executive.decisions_needed` / frontier `steer` | `steer` | `resolve_decision` |
| promoted claim without a figure | (post-promote) | `visualize` |
| promoted claim without a narrative | (post-promote) | `explain` |

The engine's seven frontier ops (`revalidate, test, branch, reconcile, revise,
steer, synthesize`) project onto the seven request kinds via this table; `test` on an
already-authored, run-ready study is a *run* (deterministic spine), not a generation
request. This replaces the `_summarize_*` "print as text" CLI behavior: `discover
plan`/`design`/`build-model` still print for humans, but the **machine path** is the
request list. The panel renders it; a filler consumes it.

### 3. Per-stage gates (the autonomy model)

A `GateConfig` is stored per investigation (in `investigation.yaml` under
`discovery.gates`) and edited from the panel:

```
GateConfig {
  default: "auto" | "review"
  by_kind: { propose_study: "review", acquire_model: "review",
             author_tests: "auto", interpret_finding: "review",
             resolve_decision: "review", explain: "auto", visualize: "auto" }
  presets: "all_gated" | "propose_review" | "free_run"   # panel one-click
}
```

At emit time each request's `gate` is resolved from this config. A **runner**
(outside the engine) dispatches requests: `gate == auto` → fill → apply and
re-crank; `gate == review` → fill (or not) → **queue** in the review panel for the
operator to approve/edit/reject. Presets: *all_gated* (review every kind),
*propose_review* (gate only the branch-points — new studies, model choices,
decisions), *free_run* (no gates → fully human-free with an AI filler).

### 3a. One dispatcher, one autonomy knob, on the existing spine

Three existing mechanisms must be **unified**, not paralleled (the "no third
parallel path" anti-goal):

- **The runner generalizes `execute_action`/`execute_plan`.** The engine already has
  a recommendation→execution bridge: `execute_plan` ranks the opportunities and
  `execute_action` dispatches the top one, gated by a **consequence class**
  (`auto_safe` / `simulation` / `steer`-with-decider / human-only), appending to an
  `executed_actions` log and re-cranking. The new runner is this bridge generalized
  from "execute one ranked action" to "dispatch a typed `GenerationRequest` to its
  filler." We extend `execute_action`, we do not add a second dispatcher beside it.
- **GateConfig subsumes the three autonomy knobs.** Today autonomy is set in three
  places: `execute_action(autonomous=…, decider=…)`, `select_for_launch(autonomy=…)`,
  and `/viva-model-build --autonomous`. The per-kind `GateConfig` **replaces** all
  three as the single operator-facing control: a kind's gate (`auto`/`review`) *is*
  its consequence decision. The consequence class (`auto_safe`/`simulation`/`steer`)
  becomes a *default* the GateConfig can override, so there is one knob, surfaced in
  the panel, not three.
- **Requests/results/log ride the existing event spine.** `event_client.py` already
  provides an event-sourced spine (`.pbg/events.jsonl` + per-consumer cursors,
  at-least-once) whose stub reactor is literally *"propose the next study here."* The
  `GenerationRequest`/`GenerationResult` stream **is** events on that spine, the
  `generation_log[]` is its projection, and the runner is the **agentic reactor the
  stub anticipates** — not a new persistence layer. `investigation.yaml` holds only
  the `GateConfig` and a derived summary; the durable trail is the event log.

### 4. The filler protocol + the AI driver

A **filler** answers one request kind. Fillers are registered per kind; the runner
picks the configured one. Two filler families, same contract:

- **Human fillers** = the existing `/viva-*` skills. The runner surfaces the request
  (panel/CLI); the human runs the skill; the authored artifact is captured as the
  `GenerationResult`. No skill is rewritten — the request just carries the context
  the skill needs.
- **AI filler** = the autonomous path. Generalize the existing `decider` seam:
  `$VIVA_DECIDER_CMD` (which already takes `{target, detail}` JSON → `{choice,
  rationale}` JSON for decisions) becomes `$VIVA_FILLER_CMD`, taking a
  `GenerationRequest` JSON → `GenerationResult` JSON for **any** kind. viva-superpowers
  ships the AI filler: it invokes the same skills' generative logic programmatically
  (propose a study, build a model, author tests, interpret a finding, resolve a
  decision, write an explanation, specify a viz).

The engine stays LLM-free: it emits requests and consumes results; the filler (the
only place an LLM runs) is injected across the seam.

**Execution model — two tiers, because the kinds differ by orders of magnitude.**
The existing decider is a blocking `subprocess.run(timeout=1800)` returning a tiny
JSON. That survives the *authoring* kinds (`resolve_decision`, `interpret_finding`,
`explain`, `visualize`, `author_tests`, and the *spec* of a `propose_study`) — they
are seconds-to-minutes and produce a dict. It does **not** survive the kinds that
**build a model or run a simulation** (`acquire_model` with route `build`, and the
run that a proposed+seeded study triggers): those are minutes-to-hours, write to the
workspace, and must outlive any request. So:

- **Authoring kinds** are filled **synchronously** (decider-style subprocess) and the
  `GenerationResult` returns inline.
- **Build/run kinds** are filled **asynchronously via the workbench's existing
  detached-run subsystem** (the run-request-file → detached `vivarium-workbench run`
  → `runs.db` model). The runner records a `pending` result on the event spine and
  captures the `GenerationResult` when the job completes (the run outcome is the
  artifact). The cycle does not block on them; the next `run_cycle` sees the new
  state.

**Apply routing — each kind flows through its canonical write op, not a generic
writer.** A `GenerationResult` is applied by a per-kind handler that calls the
existing invariant-preserving op: `resolve_decision` → `record_resolution`;
`interpret_finding` → the `findings`/`set-verdict` writers; `propose_study` →
`build_seeded_study` + the run-readiness gate; `acquire_model` → the model_version
binder; relations → `connect_investigation`/`relate`. This keeps the vocab guards,
atomic comment-preserving YAML writes, and back-compat the existing ops enforce — the
generic path is the *envelope*, the write is always the typed op.

### 5. Unifying model-space exploration

Reconcile the two model paths so `acquire_model` is a real cycle opportunity:

- Adopt `model_architect`'s `choose_acquisition_route` (reuse/wrap/build) as the
  engine's canonical routing, and map `/viva-model-build`'s `module_sourcing`
  vocabulary (reuse/compose/build-new) onto it (compose ≈ wrap-of-multiple;
  build-new ≈ build). One vocabulary, documented.
- **Granularity: emit `acquire_model` per required mechanism** (the engine's native
  unit — `choose_acquisition_route` returns one route per mechanism), and treat a
  **study's model as the composition of its per-mechanism routes**. The skill's
  per-study `module_sourcing` decision becomes the **rollup** of those routes (all
  reuse → reuse; any build → the study has a build request). The real work of Phase 5
  is this N-routes-→-one-study-model reconciliation + a single documented vocabulary
  (reuse / wrap≈compose / build≈build-new), not the token rename. Note
  `choose_acquisition_route`/`build_catalog` are **not** called inside `run_cycle`
  today (only the `build-model` CLI + `transition_model`), so this is new wiring.
- `run_cycle` calls `build_catalog` + `choose_acquisition_route` for each required
  mechanism that lacks a bound `model_version`, emitting `acquire_model` requests
  (reuse → cheap auto; build → gated by default). This makes **proposing new models
  / variants to try** a frontier opportunity, ranked by `value`, instead of a
  skill-local loop. `study_refinement` (sweep / raise_fidelity) feeds the same
  stream as model-space moves on an existing study.

### 6. Visualization + explanation as outputs

Two request kinds, emitted for every promoted claim/finding that lacks them:

- `visualize` → artifact `viz_spec/v1` (a declared chart over the run's recorded
  observables — reuses the workbench's existing viz machinery; no fabricated data).
- `explain` → artifact `explanation/v1` (plain-language statement of what the finding
  means, its evidence class, and its scope caveats — authored by the filler).

Both are surfaced in the panel beside the finding and embedded in the report, so a
human-free run still produces a legible, illustrated result.

### 7. The panel as control surface

The discovery-cycle panel (already a per-stage step graph) gains:

- **Gate controls** — per-kind `auto/review` toggles + the preset buttons, writing
  `GateConfig`.
- **A review queue** — pending `queued_for_review` results to approve / edit / reject;
  approving applies the artifact and re-cranks.
- **The generation trail** — per step, the `request → filler → result → applied_to`
  chain, so the operator can see (and trust) exactly what the AI or the human did.
- **Run modes** — "step" (one request), "cycle" (drain the auto requests, queue the
  gated), "continuous" (loop until closed or the queue blocks).

### 8. Formalized fields

JSON Schemas in `discovery_engine/schemas/`. Two groups, and the distinction matters:

- **New envelopes** — `generation_request`, `generation_result`, `gate_config`: genuinely
  new shapes this spec introduces.
- **Formalizations of existing shapes** — `model_spec`, `test_spec`, `finding`,
  `decision`, `explanation`, `viz_spec`: these **reverse-engineer and validate the
  loose dicts that already exist** (from `model_version`, `behavior_tests[]`,
  `findings[]`, `conclusion_verdicts`, and the workbench's viz machinery). They must
  validate today's data unchanged — this is a *formalize-and-pin* job under the
  back-compat constraint, not greenfield schema design. Where current data fails the
  drafted schema, the schema bends to the data (or a migration is called out), never
  the reverse.

The existing study/investigation schemas gain a `discovery.gates` block (the
`GateConfig`) and a derived `discovery.generation_summary`; the durable
request/result trail lives on the event spine (§3a), not inline. Validation runs in
the engine's `validation` layer.

## Phased build plan

1. **Contract + emission (engine, pure).** Schemas + dataclasses; `generation_requests()`
   projection over existing slots; unit tests. Panel renders the request list
   read-only. *No behavior change, no writes.*
2. **Gates + review queue (panel + persistence).** `GateConfig` in investigation.yaml;
   panel toggles/presets; the review queue UI; `generation_log[]`.
3. **Human fillers.** The runner dispatches a request to a human skill invocation and
   captures the result; wire the 2-3 highest-value kinds (`propose_study`,
   `interpret_finding`) first.
4. **AI filler.** `$VIVA_FILLER_CMD` protocol; viva-superpowers' AI filler for each
   kind; free-run end-to-end on a fixture investigation, fully gated → fully free.
5. **Unify model paths.** `run_cycle` emits `acquire_model`; reconcile
   `module_sourcing` ↔ `model_architect`; model-space exploration as opportunities.
6. **Viz + explanation.** The two request kinds + panel/report surfacing.

Each phase is independently useful and shippable; 1–2 deliver value (visible,
gate-controlled cycle) before any AI is wired.

## What's left to the builder

- The exact `value`-ranking weights for ordering requests (reuse `opportunities`'
  scoring; tune later).
- Whether the runner is exposed as a workbench endpoint (per-request HTTP, for the
  panel) or a thin `discover run-cycle --fill` CLI (for headless/CI) — **both wrap the
  same generalized `execute_action`**; likely both ship.
- The AI filler's internal prompts/skill wiring (viva-superpowers' concern, behind
  `$VIVA_FILLER_CMD`).
- The precise event schema on the spine for the request/result/pending-result records
  (reuse `event_client.py`'s envelope; add the new event types).

## Review focus (where this is most likely to bite)

- **A gated request that was never filled** must not block the whole cycle — the
  runner drains auto work and leaves gated work queued, never deadlocks.
- **A rejected/edited review** must re-crank from the *edited* artifact, not the
  original request.
- **An AI filler returning a malformed artifact** must fail validation and queue as
  an error, never apply silently.
- **Free-run must stay reconstructable:** every applied result carries filler +
  rationale + provenance, so a human can audit a no-human run after the fact.
- **The engine stays pure:** no code path from `run_cycle`/`generation_requests` may
  reach a filler or an LLM; the import-linter contract must still pass.
