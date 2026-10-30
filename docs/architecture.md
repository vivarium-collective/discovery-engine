# discovery-engine architecture

How the engine is organized, what it produces, and the line it does not cross.

The engine turns an **investigation** (an `investigation.yaml` + its member
`study.yaml` files, plus each study's registered runs) into a reproducible view
of what is known and what to do next — and drives the cycle that advances it. It
performs **no science itself**: it reconciles, plans, designs, routes model
acquisition, evaluates, and composes; the actual simulation runs happen in the
workspace (via vivarium-workbench), and the engine reads their results back.

Two properties hold everywhere:

- **Derive-first.** Every view is computed from the studies, then merged over any
  authored override — authored wins; an empty authored value never clobbers a
  derived one. Same inputs → same bundle.
- **Pure core, I/O at the edge.** The projection and the phase functions are pure
  (dicts in, dicts out). Only the CLI and a few explicit writers touch disk.

---

## 1. The data substrate

- **`investigation.yaml`** — the member studies, acceptance criteria, executive
  verdict/decisions, epistemic relations between studies.
- **`study.yaml`** — the canonical 8-section study: question, hypotheses,
  behavior tests (measure + `pass_if`), baseline + variant arms (each a
  composite + params), observables, findings (evidence + provenance), runs,
  acceptance criteria, robustness.
- **`runs.db`** (per study) + the per-run emitter store (`.pbg/runs/<id>/`, or a
  zarr/sqlite store) — read through `viva-emitters` `RunReader` and
  `run_registry`.
- Workspace layout is resolved through `viva-workspace` (never hardcoded).

Authored content (prose findings, verdicts, hypotheses) and derived content
(computed outcomes, support rollups, gates) are merged by the derive-first rule.

---

## 2. Layer 1 — the pure projection

`run_cycle(inv, studies)` returns one bundle: knowledge, argument, narrative,
audit, gates, validators, suggestions. Over it:

| Function | Produces |
|---|---|
| `frontier` | the prioritized unresolved items (by kind: `unaddressed_criterion`, `narrative_gap`, `pending_gate`, `open_question`, `open_challenge`, `open_decision`) |
| `closure` | the machine-checkable closure test — `{closed, open_items, …}` |
| `opportunities` / `next_action` | value-ranked frontier (info · impact · enable · closure − cost) |
| `diagnose` | the crank's productivity bottlenecks |
| `run_readiness` | is a study materially ready to produce an evaluable, rendered result? (composite imports, observables emittable, measures resolvable, viz rendered) |
| `execute_action` / `advance_cycle` | carry out / run the next move, consequence- and autonomy-gated |
| `persist_cycle` | the slim regenerable `cycle_state` + persist-time narrative renewal |

Each frontier kind maps to an RFC-0005 operation: `unaddressed_criterion` /
`narrative_gap` → **branch** (open a study), `pending_gate` / `open_question` →
**test** (run the gate), `open_challenge` → **synthesize**, `open_decision` →
**steer**.

---

## 3. Layer 2 — the phased discovery cycle

One cycle over an investigation runs phases A–F, then (when the member studies
compose into a larger model) N+2. Each phase is a pure function producing a typed
contract, driven by one CLI verb. The mandated build order — evidence integrity
and experiment discriminators before any general autonomous behavior — is
preserved.

### A — Reconcile evidence · `evidence_integrity` · `discover reconcile-evidence`
Classify each finding's evidence against the registered runs:
**VERIFIED** (from a registered run), **PROVISIONAL** (a numeric result with no
resolvable run), **CONTRADICTED** (an unconditioned finding whose observed value
violates its own test's `pass_if`), **UNSUPPORTED** (prose only). Reverse links
trace a claim to its runs. A prose finding is never evidence.

### B — Plan candidate actions · `candidate_action` · `discover plan`
Classify every opportunity into **REPAIR** (fix missing/unregistered evidence),
**RESOLVE** (run a study that discriminates competing hypotheses), or **EXTEND**
(a new study that opens further work), and emit one portfolio ranked
REPAIR → RESOLVE (by discrimination, then value) → EXTEND, with `depends_on`
edges so a repair is scheduled before the study that needs it.

### C — Design discriminating studies · `experiment_design` · `discover design`
Turn a study's competing hypotheses into an `ExperimentDesign`: the hypothesis
contrast, the intervention arms, the controls (the null/failure hypotheses'
arms), a registered `Prediction` per hypothesis (observable + intervention +
expected band + falsifier), and a **committed decision rule** — all before any
run.

### D — Build / acquire models · `model_architect` · `discover build-model`
Given a `ModelBuildRequest` (required mechanisms + observables), query a catalog
derived from the campaign's own study composites by capability signature, and
choose a route per mechanism: **reuse** a strong catalog match, **wrap** a
partial one, or **build** a missing one. Record an immutable, content-addressed
`ModelVersion` (components, parent ids, parameterization, digest).
`validate_coupling` flags a composite that mixes fidelities with no coupling
contract.

### E — Execute
Run the designed studies via Layer 1 (`advance` / `execute`), consequence-gated
and under the autonomy policy. Registration (`register_run`) and the run store
are produced by the workspace; the engine reads them back.

### F — Evaluate & record · `evaluation` · `discover evaluate` / `discover delta`
Promote or reject claims on two **separate** axes: a hypothesis's *support*
(does the evidence back it?) and its evidence *integrity* (is the evidence a
registered run?). A claim backed only by provisional evidence is
**provisionally-supported**, never accepted. `model_disagreement_map` surfaces
multi-arm disagreement (do not average); an **adjudicated** supersession (a
higher-fidelity run confirmed it, `evidence.superseded.adjudicated_by`) is
resolved history, not a live caveat. `discovery_delta` is the per-cycle record of
added/qualified claims, gated studies, disagreements, anomalies, and recorded
regressions.

### N+2 — Transition composition · `transition_model` · `discover transition`
Compose the validated sub-models into a versioned transition composite (routing
each mechanism through the Phase-D catalog, pinning the Cycle N+1 `ModelVersion`s
as parents) and estimate a **conditional phase diagram**: which regime of the
conditioning parameters yields which outcome, with the conditioning caveats
derived from the live model disagreements.

---

## 4. The contracts

Typed records the phases produce (dicts with a stable shape), each owned by one
module:

| Contract | Fields (abridged) | Module |
|---|---|---|
| **Hypothesis** | id, study, statement, predictions[], status | `hypotheses` |
| **Prediction** | id, hypothesis_id, intervention, observable, expected, falsifier | `experiment_design` |
| **Evidence** (classification) | status, reason, from_test, run_ids | `evidence_integrity` |
| **ExperimentDesign** | id, study, hypothesis_contrast, interventions, controls, predictions, decision_criterion | `experiment_design` |
| **CandidateAction** | kind (repair/resolve/extend), op, value, discrimination, depends_on, discriminates | `candidate_action` |
| **ModelBuildRequest** / **ModelVersion** | question_id, required_mechanisms, routes; id, components, parent_ids, executable_digest, validation | `model_architect` |
| **DiscoveryDelta** | added_or_qualified, gated, model_disagreements, anomalies, regressions, fingerprint | `evaluation` |

---

## 5. The growth loop — propose → seed → run → promote → refine

The engine both *advances* an investigation and *grows* it, under one autonomy
setting:

- **suggest** (`suggest_studies`) — proposes new studies from three sources:
  authored followup proposals, audit-derived narrative-gap bridges, and
  `decisions_needed` that name a member study (the last are gated on expert
  input).
- **select** (`study_seeding.select_for_launch`) — the autonomy gate:
  `human_in_the_loop` (default: only approved, non-expert-gated proposals launch)
  vs `autonomous` (every ready proposal launches; expert-direction decisions stay
  gated).
- **seed** (`study_seeding.build_seeded_study`) — authors a complete, **run-ready**
  `study.yaml` from a parent + proposal/finding: inherits the parent's composite,
  observables and behavior tests (so the child is runnable and evaluable by
  construction), applies the proposal's `model_change` as the variation under
  test.
- **run → promote** — Phases E/A/F take over.
- **refine** (`study_refinement.refine_study`) — deepens an existing study from
  the engine's own signals (`rigor` replicate count, `fidelity_flags`
  `low_fidelity_support`): adds replicate seeds, sweeps a parameter into variant
  arms (clamped to the parameter's valid range), and/or raises model fidelity.

A parameter-sweep variant arm characterizes a regime; it is tagged as a variant
so it does not gate the baseline claim (the canonical arm gates; the sweep maps
the boundary).

---

## 6. Separation invariants

- **Progress ⊥ verdict** — a failed execution is not a refuted hypothesis.
- **Closure ⊥ frontier** — a settled argument can still carry non-blocking work.
- **Publishable ⊥ closure** — presentation-completeness is its own axis.
- **Evidence integrity ⊥ support** — backing is distinct from registration; a
  provisional-backed claim is provisionally-supported, never promoted.
- **Specify before run** — predictions and the decision rule are committed before
  the run.
- **Declaration ⇒ materialization** — a run is gated unless it can produce an
  evaluable, rendered result.
- **Epistemic separation** — a validator's execution status never implies its
  scientific judgment.

---

## 7. How a consumer drives it

A viva-superpowers skill (or a human) calls `discover <verb> <investigation>`
against a workspace; the engine reads the study specs + run stores and returns a
summary. vivarium-workbench renders the same investigation/study model in its UI.
The engine is the lower layer: it imports `viva-workspace` (layout) and
`viva-emitters` (run reading), and **never** imports vivarium-workbench or
viva-superpowers (enforced by `tests/test_no_workbench_import.py`).

Lineage: the operational algebra (branch / compose / test / synthesize / steer)
is RFC-0005; epistemic relations are RFC-0006; validation is RFC-0007.
