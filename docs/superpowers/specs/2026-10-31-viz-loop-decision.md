---
title: "Discovery Engine — visualization loop decision"
subtitle: "Propose → accept → catch missing/broken → build → refine, as first-class cycle steps, by analogy to the closed-loop model-revision loop"
date: "2026-10-31"
inputs:
  - "~/code/discovery-engine--generality/discovery_engine/{discovery_engine,discover,validation,viz_freshness,model_revision,candidate_action,run_command}.py (HEAD 4136c71)"
  - "~/code/vivarium-workbench--cycle-viz/vivarium_workbench/lib/{investigations,study_charts,refresh_viz,viz_render,viz_gate,study_viz_views,investigation_viz_mutations,cycle_trace,needs_attention}.py, static/discovery-cycle.js, api/app.py"
  - "~/code/viva-genesis--flux-fix/workspace/studies/s5-symbiont-to-organelle/study.yaml, viva_genesis/viz_endosym.py"
  - "~/code/viva-superpowers/skills/viva-report/SKILL.md §A8 (the current, chat-only viz proposer)"
  - "docs/superpowers/specs/2026-10-30-closed-loop-upgrade-decision.md (the structural template)"
status: binding decision — read-only analysis, no code changed
---

# 0. Executive decision

**Do not build a visualization subsystem.** Everything the loop needs except the typed proposal object already exists in some form and is simply not connected to the crank:

1. **The check exists but is off the cycle.** `viz_freshness.chart_freshness` classifies a declared chart `fresh | stale | unrendered`, and `run_readiness` already globs `studies/<slug>/viz/*.html` and reports "visualizations declared but not rendered". Neither reaches `run_cycle`, the validator registry, or the frontier. The validator registry has a `visual` type and a `figure` scope (`validation.py:36-44`) and **the `visual × figure` cell of `coverage_matrix` is empty** — the slot was reserved and never filled.
2. **The build exists but is a side action.** Two render paths are live in the workbench: `refresh_viz.refresh_study_viz` (runs an entry's `render:` shell command with `{chart}`, stamps `<chart>.meta.json` via `stamp_meta`) and `investigations.render_visualizations` (builds a viz-class composite from `address: local:Xxx` against `runs.db`, writes `viz/<name>.html`). They are reachable only from `/api/study-refresh-viz` and `/api/investigation-render-viz`; `advance_cycle` dispatches `test`/`branch` only (`_ADVANCE_OPS`, discover.py:1155).
3. **The proposer exists but is prose.** `/viva-report` §A8 requires the agent to propose ≥2 new visualizations per render, with title/form/finding/data-source/effort. Nothing persists them, nothing types them, nothing tracks whether one was built. It is `ModelRevisionProposal` before `model_revision.py` existed.
4. **The breakage is real and the current paths handle it badly.** viva-genesis s5 declares `eigen-catastrophe` as `{name, title, rendered: workspace/studies/s5-…/viz/eigen-catastrophe.html}` — no `address`, no `chart`, no `render`. `rendered:` is read by **nothing** in the workbench (grep: only `prepare_investigation` uses a local variable of that name). The native figure is produced by `viva_genesis.viz_endosym.render_s5_catastrophe` via `render_all(ws_root)`, which is wired to no CLI, Makefile, or hook. On the workbench's render path `_is_static_image_viz` is false (no address/chart), so `build_viz_composite` hits `viz_spec["address"]` → `KeyError: 'address'` (investigations.py:1513). Worse: `render_visualizations` catches that per-viz exception and **writes an error-stub HTML to `viz/eigen-catastrophe.html` — the exact file the native renderer produced** (investigations.py:2144-2154), i.e. the re-render path can clobber a real figure with `<p>Failed to render …</p>`.

**The decision:** close the visualization loop with **four new pure primitives, one write-back, one injected hook, and five strengthened existing functions**, persisted as **one additive block in `investigation.yaml`** (`viz_proposals[]`), **normalized fields on the existing `study.yaml: visualizations[]` entries** (no new study block), and the **existing `<chart>.meta.json` sidecar**. Four frontier kinds (`viz_broken`, `viz_missing`, `viz_proposed`, `viz_stale`) and two ops (`build_viz`, `refine_viz`) enter the `_PRIORITY`/`_OP` tables as **non-blocking** items. The executor is the workbench's **existing** two render paths behind one `render_hook`, never the engine. The engine stays general: it classifies viz *specs* and a figure *inventory* passed in as data, exactly as `evidence_state` and `catalog` are passed to `run_cycle` today.

Roughly 800 lines across both repos, three waves; the s5 `eigen-catastrophe` stub appears on the frontier at the end of wave 0.

# 1. The critical path — what is load-bearing

The loop in the engine's own vocabulary, with the status of each hop:

| # | Hop | Current mechanism | Status | What makes it real |
|---|-----|-------------------|--------|--------------------|
| 1 | Declared viz → classified state | `viz_freshness.chart_freshness` (static `chart:` entries only, workbench-called); `run_readiness.viz_rendered` (glob, boolean) | **Off-cycle, partial** — class vizzes (`local:`) and html figures are never classified; an entry with no renderable identity is not a state | `viz_freshness.normalize_entry` + `classify_viz` → `fresh / stale / unrendered / broken_stub`; `viz_inventory` harvest passed into `run_cycle` as data |
| 2 | Classified state → frontier item | none (`needs_attention._stale_viz_debts` reads a `freshness` field no writer sets) | **Missing** | validator `visual.figure.freshness` (fills the empty `visual × figure` cell) + typed projection into `viz_broken / viz_missing / viz_stale` |
| 3 | Frontier item → executable build | `/api/study-refresh-viz` (all entries, `render:` only); `/api/investigation-render-viz` (all entries, `local:` only) | **Disconnected from the crank**; no single-entry render; the two paths disagree on error handling (one keeps the old chart, one overwrites it with a stub) | `render_hook(study_dir, entry, latest) -> {ok, chart, source_run_id}` dispatching by kind; `_executor_for("build_viz"/"refine_viz")`; consequence `render` |
| 4 | Build → stamped artifact | `stamp_meta` (refresh path only); `render_visualizations` writes html, **no meta** | **Half** | both paths stamp; `_post_render_stamp` in advance mirrors `_post_run_sync` |
| 5 | Stamped artifact → item discharged | `frontier()` uniform `(kind, target)` resolution | **Real by construction** once hop 1 is on-cycle: a fresh figure is not emitted (derivation discharge, like a met criterion) | nothing new |
| 6 | Finding / gate / discriminating result with no figure → proposal | `/viva-report` §A8 prose | **Missing as an object** | `viz_proposals.propose_visualizations` (pure) → `VizProposal`, `viz_proposals[]` ledger |
| 7 | Proposal → accepted → declared entry → hop 1 | none | **Missing** | `accept_viz_proposal` write-back = history event + a `visualizations[]` entry with `origin`; then the entry is `unrendered` → hop 2 → `viz_missing` → hop 3 |
| 8 | Built → proposal closed | none | **Missing** | `persist_cycle` marks a proposal `built` when its declared entry first classifies `fresh` (the `evaluated` analog) |

**The smallest set of NEW primitives that makes 1→8 real:**

| New primitive | Module | Kind | One-line contract |
|---|---|---|---|
| `normalize_entry(entry) -> VizSpec` | viz_freshness | pure | resolves an entry's *kind* (`class` / `figure` / `command` / `broken_stub`) and its expected output path from `address` / `chart` / `render`; never reads disk |
| `classify_viz(spec: VizSpec, inv_row, latest, mv) -> status` | viz_freshness | pure | `fresh / stale / unrendered / broken_stub` from the normalized spec + an inventory row (exists, meta, mtime, hash) + the latest run (+ optional baseline mv) |
| `viz_inventory(ws_root, inv_slug, studies) -> {study: {name: row}}` | discover (next to `_load_runs_by_study`) | **impure harvest** | the one disk read: per declared entry `{path, exists, meta, mtime, content_hash}` + `viz/*.html` files present but undeclared; passed into `run_cycle(viz_state=…)` |
| `propose_visualizations(inv_spec, studies, cycle_result, viz_state, viz_catalog) -> [VizProposal]` | new `viz_proposals.py` | pure | gate / discriminating-hypotheses / model-disagreement observables that no declared viz covers → typed proposals; fills `suggested.address` only from the catalog, else `needs_author` |
| `accept_viz_proposal(ws_root, inv_slug, vp_id, *, address=None, rationale, by) -> {study_path, entry}` | discover (write-back; CLI `discover viz <inv> <vp> --accept`) | mutating | history event on the proposal + appends a `visualizations[]` entry carrying `origin: {kind: viz_proposal, ref}`; refuses when neither the catalog nor `--address` supplies a renderable address |
| `render_hook` seam on `execute_action` / `advance_cycle` | discover | injected | `render_hook(study_dir, entry, latest) -> {ok, chart, source_run_id, detail}`; absent → fall back to `runner(render_command(...))` subprocess |

**Strengthened (existing code, same signatures or one added kwarg):**

| Existing | Change |
|---|---|
| `viz_freshness.chart_freshness` | accept a normalized spec (output path derived from `address: html:…`/`svg:…` when `chart:` is absent); when meta is absent fall back to mtime vs `latest.completed_at` — the rule `study_spec.discover_viz_html_files` already applies to `viz/*.html`, so the two freshness paths stop disagreeing |
| `viz_freshness.stamp_meta` | optional `model_version=` field for figures that run the model directly (s5's sweep) rather than reading `runs.db` |
| `validation.ValidationContext` | `+ viz_state: dict` (default `{}`); new validator `visual.figure.freshness` (`type="visual"`, `scope="figure"`, `surfaces="frontier"`) — one record per declared entry, target `fig:<study>/<name>` |
| `discovery_engine.frontier` / `_PRIORITY` / `_OP` / `_executor_for` / `_OPP_INFO` / `_OPP_COST` | + kinds `viz_broken` (47, `refine_viz`), `viz_missing` (46, `build_viz`), `viz_proposed` (44, `build_viz`), `viz_stale` (38, `refine_viz`); step 4 skips `type == "visual"` records (projected as typed items in a new step 7, the `narrative` precedent); consequence `render` for build/refine on a declared entry, `review` for a proposal or a broken stub |
| `discover.run_cycle` / `_cycle_with_evidence` | `run_cycle(..., viz_state=None, viz_catalog=None)`; `_cycle_with_evidence` harvests both; `bundle["viz_proposals"]` |
| `discover.persist_cycle` | append-only merge of `viz_proposals[]` by content id (same loop as `revision_proposals`); `_close_built_viz_proposals`; `cycle_state.viz_census` |
| `discover.execute_action` / `advance_cycle` | consequence `render` dispatchable; `_ADVANCE_OPS += ("build_viz", "refine_viz")`; eligibility = entry resolvable (never `broken_stub`); skip iff `meta.source_run_id == latest.run_id` (the per-mv-skip analog); `_post_render_stamp`; decider branch for `vp:` targets (accept/reject) mirroring the `rp:` branch |
| `discover.RESOLUTION_KINDS` | + the four viz kinds (uniform discharge with a rationale, e.g. "hand-authored figure, no refresh recipe by design") |
| `run_command` | + `render_command(slug, name, ws)` with its own template (`"{runner} render-viz {slug} --viz {name} --workspace {ws}"`), so a non-workbench consumer swaps the renderer like the runner |
| workbench `investigations.render_visualizations` / `build_viz_composite` | **bug fix the design depends on:** an entry with no dispatchable address is *skipped and reported*, never overwritten with an error stub; `build_viz_composite` raises a typed `VizSpecError("no address")` instead of `KeyError` |
| workbench `render_visualizations` | `stamp_meta(viz/<name>.html, source_run_id=latest.run_id, …)` after a successful write (the refresh path already does) |

Everything else (figure critique, auto-authoring viz classes, a viz quality score, engine-side plotting) is cut (§7).

# 2. Reuse vs. build — per capability

## A — Declare (what a visualization *is*)

- **Reuse as-is:** `study.yaml: visualizations[]` (schema: `name` required, `additionalProperties: true` — new fields save cleanly); the workbench's address grammar — `local:Class` / `local:!dotted.Class` (viz-class composite), `svg:|png:|gif:|image:|file:` (static figure), `html:|threejs:` (self-contained iframe page); `chart:` (output path, the `viz_freshness` key), `render:` (shell command with `{chart}`, `VIVA_RUN_DIR`/`VIVA_RUN_ID` env), `requires: [observable]` (refuse-to-render contract), `source_run:` (pin).
- **Normalize, don't add:** `normalize_entry` derives one `VizSpec = {name, kind, address, output (study-relative), render, requires, source_run, covers}`:
  - `kind: class` — address scheme `local:`/`dashboard:` → `output = viz/<name>.html`
  - `kind: figure` — address scheme in the figure/iframe sets, or a file-pointer field (`chart`/`file`/`path`/`src`) with a figure suffix (the `resolve_declared_figure_ref` rule, reused verbatim) → `output = <ref>`
  - `kind: command` — `render:` present (output from `chart:` or the address ref)
  - `kind: broken_stub` — none of the above. **This is s5 `eigen-catastrophe`.** `rendered:` is deliberately *not* accepted as a pointer: no renderer reads it, and honoring it here would legitimize a dead field. The validator's recommendation names the fix.
- **One new optional field:** `covers: [observable, …]` — the observables this figure visualizes, for proposer coverage (§2F). Derived coverage (from `requires`, `config.inputs_map` values, and the viz catalog's declared inputs for `class` entries) is used when `covers` is absent, so existing entries need no edit to count.
- **Deprecate, don't delete:** `rendered:` joins the linter's `visualization_path_not_canonical` pointer list with the message "unread by any renderer — use `address: html:<path>` (+ `render:` if regenerable)".
- **Do not build:** a `VisualizationSpec` class hierarchy, a viz schema version bump, or a migration. Tolerant readers (`.get`) and the normalizer are the whole data model.

## B — Catch (the validator)

- **Reuse:** `validation.validator` registry; `type="visual"` is already in `_EXECUTION_ONLY_TYPES`, so the record's `scientific_judgment` is forced null — a fresh figure asserts nothing about truth, structurally. `run_validators` isolation means a broken harvest yields one `error` record, not a dead cycle.
- **Build:** `visual.figure.freshness`:
  ```
  for study, spec in ctx.studies:
    for entry in spec.visualizations:
      vs = normalize_entry(entry); row = ctx.viz_state[study][vs.name]
      status = classify_viz(vs, row, latest=ctx.viz_state[study]["_latest"], mv=…)
      record(type=visual, scope=figure, target=f"fig:{study}/{vs.name}",
             execution_status = passed if fresh else failed,
             criteria=[status], findings=[one line], recommendations=[the fix],
             evidence=[{path, exists, source_run_id, latest_run_id, content_hash}])
  ```
  Plus one record per **undeclared** `viz/*.html` present on disk (`criteria=["untracked"]`, `inconclusive`, standing) — the `manifest_diff` idea extended to `viz/`; it stays off the frontier (an orphan file is feedback, not work).
- **Purity preserved the existing way:** validators are pure over `ValidationContext`; the disk read is `viz_inventory`, called by the impure callers (`_cycle_with_evidence`, the workbench `load_inputs` step) and passed in — the pattern `reconcile_evidence_state` → `run_cycle(evidence_state=…)` already uses. `run_cycle(inv_spec, studies)` with no `viz_state` yields no visual records (not a flood of `unrendered`), so standalone/hermetic fixtures are unaffected.
- **Classification rules** (`classify_viz`):

  | kind | file absent | file present, no meta | meta present |
  |---|---|---|---|
  | `broken_stub` | `broken_stub` | `broken_stub` (file found by name is reported as a *derivable fix*, see §3.3) | `broken_stub` |
  | `class` / `figure` / `command` | `unrendered` | mtime ≥ `latest.completed_at` → `fresh`, else `stale` (the `discover_viz_html_files` rule); no runs at all → `fresh` | `source_run` pinned → fresh iff `meta.source_run_id == pin`; else fresh iff `meta.source_run_id == latest.run_id` and `rendered_at ≥ completed_at`; **if `meta.model_version` is present and the entry declares `requires: []`**, fresh iff it equals the study's current baseline mv (run-independent native figures) |

- **Do not build:** a figure *quality* validator (`critique_figure` was cut in the closed-loop decision and stays cut), a viz-class static analyzer (the linter's `visualization_address_missing` already checks `local:` names against `<pkg>/visualizations/`), or an image-diff.

## C — Project (frontier kinds + ops)

- **Reuse:** `frontier.add` (uniform dedupe + resolution), `_CLOSURE_BLOCKING` (unchanged — viz never blocks closure), `opportunities` scoring, `execute_plan`, `candidate_action` (`build_viz`/`refine_viz` classify as `EXTEND` with a viz rationale; no new candidate kind).
- **Strengthen (tables):**

  | kind | priority | op | consequence | trigger |
  |---|---|---|---|---|
  | `viz_broken` | 47 | `refine_viz` | `review` (needs a declaration fix; the engine may *derive* one, never write it unasked) | status `broken_stub`, or `class` whose address is not in the catalog, or `figure` whose file is absent *and* no `render:` |
  | `viz_missing` | 46 | `build_viz` | `render` | status `unrendered` on a resolvable entry |
  | `viz_proposed` | 44 | `build_viz` | `review` | an open `VizProposal` (status `proposed`) |
  | `viz_stale` | 38 | `refine_viz` | `render` | status `stale` |

  Placement: above `open_question` (40) for missing/broken — a gate with no figure is reviewer-visible debt sooner than an unassessed claim is engine-actionable; below `validation_finding` (45) for proposals; stale below everything scientific. `_OPP_INFO`: `build_viz` 0.4, `refine_viz` 0.2; `_OPP_COST`: 0.3 / 0.2 (a native render may run a sweep, so not free).
- **Step-4 skip:** `if rec.get("type") == "visual": continue` with the same comment the `narrative` skip carries — visual records are projected per figure as typed items in step 7, each with a build/refine op, instead of as one generic `validation_finding` with op `revise`.
- **Step 7 (new):** iterate the visual records → `add(kind_for(status), target, finding)`; iterate `viz_proposals` (disk status wins, like step 6) → `add("viz_proposed", vp.id, …)`; a proposal of any non-`proposed` status is never emitted; a *declared* entry with `origin.ref == vp.id` covers the proposal (so accept → the item becomes `viz_missing` on the entry, not two items).

## D — Build / refine (the executor)

- **Reuse:** `execute_action` consequence gating + injectable `runner`; `executed_actions` log; `advance_cycle`'s `seen` / `seen_slugs` / `gated_ops` / `max_runs`; the workbench's two render paths; `stamp_meta`; `viz_render.viz_render_hooks` (env-worker `inputs_by_class` + `build_and_run`); `refresh_viz._requires_refusal` (refuse-to-render when `requires` observables are absent from the run store — the "never fabricate" guard already exists on this path).
- **Build (workbench, ~120 lines):** `lib/viz_build.render_one(ws_root, study, name) -> {ok, chart, source_run_id, detail}` dispatching on `normalize_entry(...).kind`: `command` → `refresh_study_viz` filtered to that entry; `class` → `render_visualizations` filtered to that entry via `viz_render_hooks`, then `stamp_meta`; `figure` with no `render:` → `{ok: False, detail: "static figure has no refresh recipe"}`; `broken_stub` → refuse. Exposed two ways: a `vivarium-workbench render-viz <slug> --viz <name>` CLI verb (what `render_command` emits, so `discover advance` from a shell works) and as the `render_hook` the panel passes in-process.
- **Strengthen `execute_action`:** a `render` consequence branch parallel to the `simulation` branch: refuse unless the entry is resolvable (readiness analog); if `render_hook` is given call it, else `runner(render_command.replace("<ws>", ws))`; status `executed` / `failed`. A `vp:` target with `decider` + `autonomous` → accept/reject with a rationale (mirrors the `rp:` branch; accept is allowed only when the proposal is `derivable` — a `needs_author` proposal is `gated-human`).
- **Strengthen `advance_cycle`:** admit `build_viz`/`refine_viz` with consequence `render`; eligibility = resolvable entry (never `broken_stub`, never a bare proposal); skip rule = `meta.source_run_id == latest.run_id` (fresh → not on the frontier anyway, so this is belt-and-braces for a hook that forgot to stamp); on `executed` → `_post_render_stamp(sdir, entry)` writes meta if the hook did not (the `_post_run_sync` discipline); a render that `failed` → `gated_ops` (not retried this advance); `max_runs` counts renders.
- **No-loop property:** a render discharges its item only *by the next cycle's classification*. If a hook "succeeds" without producing a fresh classification (writes nothing, or writes without meta and mtime still predates the run), `_post_render_stamp` stamps; if the file is still absent the status is `failed` and the op is gated. Unit-test with a fake hook that (a) writes+stamps, (b) writes without stamping, (c) writes nothing.
- **Do not build:** an engine-side renderer (no plotly / process-bigraph import in `discovery_engine`; `tests/test_no_workbench_import.py` stays green), a render queue, or a `callable:` form of `render:` (deferred, §7 — a workspace exposes native renderers as `python -m <pkg>.<module> <figure> {chart}`; viva-genesis needs a ~15-line `__main__` in `viz_endosym.py`).

## E — Close (discharge + outcome)

- **Reuse:** uniform `(kind, target)` resolution; derivation discharge (a fresh figure simply is not emitted, exactly as a met criterion); `persist_cycle`'s re-read-on-disk + append-only merge; `_evaluate_applied_proposals` as the template.
- **Build:** `_close_built_viz_proposals(on_disk, viz_state)`: for each `accepted` proposal whose declared entry (matched by `origin.ref`) classifies `fresh`, write `built: {at, chart, source_run_id, content_hash}`, `status: built`, history event `{event: built, by: engine}`. A `rejected` proposal and a `built` proposal are terminal (`_TERMINAL`), never re-emitted while the source is unchanged (the id hashes the source).
- **Human discharge:** `discover resolve <inv> viz_stale fig:s5/… --rationale "pinned by design"` works through `RESOLUTION_KINDS` — but the recommendation printed with a `viz_stale` item is to set `source_run:` (the existing pin), which is the honest statement "this figure is from run X on purpose" and keeps freshness checkable.

## F — Propose (the viz analog of `propose_model_revisions`)

- **Reuse:** `study_evaluator.required_observables_for_measure` (gate measure → source observables, already used by `run_readiness`); `hypotheses.discriminating_hypotheses`; `evaluation.model_disagreement_map`; the `_hash8` content-id + `_TERMINAL` + per-study cap discipline from `model_revision`; `suggest_studies`' `ready / awaiting_expert` vocabulary; the viz **catalog as data** (the workbench already builds `inputs_by_class` / `build_visualization_instances`; it is passed to `run_cycle(viz_catalog=[{address, inputs: {port: type}, form?}])` the way `build_catalog` is passed for composites).
- **Build `propose_visualizations`** — three derivable triggers, capped at **2 per study per cycle**, kind-ordered then by id:

  | Trigger | Condition | Suggested form | `derivable` iff |
  |---|---|---|---|
  | `gate_uncovered` | an `acceptance_criteria`/primary behavior test whose measure's source observables are covered by no declared viz (`covers` ∪ `requires` ∪ `inputs_map` values ∪ catalog inputs of its `local:` class) | `band_overlay` (observable trace + `pass_if` band) | the catalog has a class whose declared inputs accept that observable's type |
  | `discriminating_uncovered` | `discriminating_hypotheses(inv)[study].discriminable` on an observable no viz covers | `prediction_bands` (the competing bands + observed) | same |
  | `disagreement_uncovered` | a `model_disagreement_map` row (`disagreement: true`) on an observable no viz covers | `arm_comparison` | same |

  A finding-level trigger ("a numeric finding with no figure") collapses into `gate_uncovered` because findings cite `evidence.from_test`; a finding with no `from_test` is an evidence problem (REPAIR), not a viz gap — no proposal, the same rule `contradicted` follows in `model_revision`.
- **What the engine refuses to derive:** a viz *class*. A non-derivable proposal carries `suggested.address: null, needs_author: true` and the brief (`form`, `observable`, `data_source`, `finding`) — the input `/viva-viz` wants. `/viva-report` §A8 keeps its creativity rule but **writes through the ledger** (`discover viz <inv> --propose <json>`) so its proposals persist, dedupe, and are tracked to `built`, instead of living in a chat transcript.

# 3. The minimal canonical contracts

**Format decision: plain dicts, tolerant readers, JSON Schema at the write boundary** — `schemas/investigation-extensions.schema.json` gains `viz_proposals`; `_validate_extensions` is already called in `persist_cycle`. No study-schema change: `visualizations[].additionalProperties: true` (verified in `viva-template/template/.pbg/schemas/study.schema.json:402-423`).

### 3.1 VizSpec (derived, never persisted — the normalized view of one `visualizations[]` entry)

```yaml
name: eigen-catastrophe
kind: class | figure | command | broken_stub
address: "html:viz/eigen-catastrophe.html"       # as declared (may be null → broken_stub)
output: viz/eigen-catastrophe.html                # study-relative; from chart:, else the address ref, else viz/<name>.html for class
render: "python -m viva_genesis.viz_endosym eigen-catastrophe {chart}"   # null when not regenerable
requires: []                                      # observables the render needs from runs.db ([] = runs the model itself)
source_run: null
covers: [settled_heritability]                    # authored, else derived from requires/inputs_map/catalog inputs
```

### 3.2 Figure provenance = the existing sidecar (`<output>.meta.json`, one optional field)

```json
{"source_run_id": "…__b1d6f7", "generation_id": null, "rendered_at": 1793462500.1,
 "command": "python -m viva_genesis.viz_endosym eigen-catastrophe viz/eigen-catastrophe.html",
 "content_hash": "sha256:…", "model_version": "mv:931b63c6aaec8660"}
```
`model_version` is new and optional: stamped when the entry declares `requires: []` (the figure ran the model, not the run store) from the study's baseline arm mv via `bind_model_versions`; the classifier prefers it over `source_run_id` for such entries.

### 3.3 Validation record (existing shape; one per declared entry)

```yaml
type: visual            # execution-only → scientific_judgment MUST be null (enforced)
scope: figure
target: fig:s5-symbiont-to-organelle/eigen-catastrophe
result: {execution_status: failed, scientific_judgment: null}
criteria: [broken_stub]
findings: ["declared with no address, chart or render — not renderable by any path (`rendered:` is read by nothing)"]
recommendations: ["set `address: html:viz/eigen-catastrophe.html` (a matching file exists in viz/) and a `render:` recipe so it can be refreshed"]
evidence: [{path: viz/eigen-catastrophe.html, exists: true, meta: null, derivable_fix: {address: "html:viz/eigen-catastrophe.html"}}]
provenance: {validator_id: visual.figure.freshness, validator_version: "0.1"}
```
`derivable_fix` is filled only when a file in `viz/` or `charts/` matches the entry `name` by stem — the engine derives, a human (or the decider) applies it via `discover viz <inv> fig:<study>/<name> --declare address=… [render=…] --rationale …` (review tier; it edits an authored list, so it is never automatic).

### 3.4 VizProposal (persisted, append-only, `investigation.yaml: viz_proposals[]`)

```yaml
- id: vp:s4-compartmental-energetic-coupling:7c1e2a9b     # sha256({kind, study, observable, source_ref})[:8]
  status: proposed | accepted | rejected | built
  study: s4-compartmental-energetic-coupling
  source: {kind: gate_uncovered, behavior_test: internalization-benefit, observable: pop/survivors}
  suggested:
    form: band_overlay
    title: "pop/survivors vs catalyst_efficiency with the acceptance band"
    data_source: {observable: pop/survivors, run_ids: [viva_genesis.composites.…__a1b2c3]}   # from runs[] / runs.db; [] if never run
    address: local:BandOverlayViz | null       # from the viz catalog only
    inputs_map: {series: pop/survivors}        # when derivable
    needs_author: false | true
    effort: render-only | needs-run | needs-author   # derived: observable present in a run store → render-only
  sharpens: {finding: f-s4-internalization-benefit, claim: "claim:s4-compartmental-energetic-coupling"}
  approval: review
  history:
    - {at: …, event: proposed, by: engine}
    - {at: …, event: accepted, by: human, rationale: "…", address: local:BandOverlayViz}
    - {at: …, event: built, by: engine, chart: viz/survivors-band.html, source_run_id: …}
  built: null | {at, chart, source_run_id, content_hash}
```
Rules: identity is content (re-deriving next cycle is a no-op); the engine fills `address` only from the catalog, never invents a class; `accepted` is written together with the `visualizations[]` entry (`name: <slug of title>, address, config.inputs_map, covers: [observable], origin: {kind: viz_proposal, ref: vp:…}`) in one `accept_viz_proposal` call; `rejected`/`built` are terminal.

### 3.5 Frontier item (existing shape)

`{kind: viz_missing, target: "fig:s5-symbiont-to-organelle/heritable-integration", priority: 46, op: build_viz, rationale: "declared local:S5HeritabilityViz has no rendered viz/heritable-integration.html"}` → `execute_plan` row `{executor: render-viz, command: "vivarium-workbench render-viz s5-symbiont-to-organelle --viz heritable-integration --workspace <ws>", consequence: render, auto_safe: false}`.

### 3.6 `cycle_state.viz_census` (slim)

`{fresh: 7, stale: 2, unrendered: 1, broken: 1, untracked_files: 3, proposals: {open: 2, accepted: 1, built: 4}}` + `drift.stale_viz`. Nothing else is cached; the records are re-derived each cycle.

# 4. Two cycles on viva-genesis s5, concretely

### Cycle N — the gap is caught

1. `_cycle_with_evidence` → `viz_inventory(ws, "eukaryogenesis", studies)`: for s5, `eigen-catastrophe` → `normalize_entry` gives `kind: broken_stub` (no address/chart/render); inventory finds `viz/eigen-catastrophe.html` on disk by stem → `derivable_fix`. `heritable-integration` → `kind: class` (`local:S5HeritabilityViz`), expected `viz/heritable-integration.html`; say the file exists, no meta, mtime predates the latest `runs[]` row's timestamp (1793462403) → `stale`. The viz catalog (workbench-supplied) lists `S5HeritabilityViz` with inputs `{heritability: list[float]}` → it *covers* `herd/heritability`.
2. `run_cycle(viz_state=…, viz_catalog=…)` → validator emits two `visual/figure` records (both `failed`). `propose_visualizations`: s5's gate `heritable-integration-stable` measures `settled_heritability` → `required_observables_for_measure` → `herd/heritability` → covered by the class viz → **no proposal for s5** (correct: the catalog's declared inputs are what keep the proposer quiet on an already-visualized gate). Suppose s4's gate observable `pop/survivors` is covered by nothing → one proposal `vp:s4…:7c1e2a9b`, `derivable: false` (no generic band-overlay class in this catalog), `needs_author: true`, `effort: render-only` (the observable is in s4's run store).
3. `frontier()`: `viz_broken fig:s5/eigen-catastrophe` (47, `refine_viz`, review, with the derivable fix in the rationale), `viz_stale fig:s5/heritable-integration` (38, `refine_viz`, render), `viz_proposed vp:s4…` (44, `build_viz`, review). `closure()` unchanged. `persist_cycle` appends the proposal; `cycle_state.viz_census = {stale: 1, broken: 1, proposals: {open: 1}}`.
4. The panel's gate inspector lists all three under "Tracked — non-blocking", labelled via `_KIND_LABEL` ("Figure declared but not renderable", "Figure stale vs latest run", "Visualization proposed"), jump-in tab `visualizations`; the stale item carries a **Build ▸** button (render tier), the broken item an **Apply fix ▸** form pre-filled with the derived address and a required rationale, the proposal **Accept… / Reject…** (accept asks for an address since `needs_author`).

### Between cycles — a human decides

`discover viz eukaryogenesis fig:s5-symbiont-to-organelle/eigen-catastrophe --declare address=html:viz/eigen-catastrophe.html render="python -m viva_genesis.viz_endosym eigen-catastrophe {chart}" requires="[]" --rationale "native sweep figure; declare + make it refreshable"` → the s5 entry becomes `{name, title, address, render, requires: [], covers: [settled_heritability]}`; `rendered:` is dropped. (viva-genesis adds the `__main__` to `viz_endosym.py`.) The s4 proposal: `--accept --address local:S4SurvivorsBandViz` after `/viva-viz` writes that class — or `--reject --rationale "the table suffices"`.

### Advance — the same crank builds

`discover advance eukaryogenesis --autonomous` (or the panel's Build buttons, in-process hook): plan rows with consequence `render` are now eligible. `eigen-catastrophe` re-classifies `kind: command`, file exists, no meta → `stale` → `refine_viz` → `render_one` → `refresh_study_viz` runs the `render:` command (the sweep, ~minutes, inside the 900 s timeout) → `stamp_meta(..., source_run_id=latest, model_version=mv:931b63c6aaec8660)` (since `requires: []`). `heritable-integration` → `render_visualizations` filtered to one entry → html written → stamped. The s4 accepted entry → `unrendered` → `viz_missing` → `build_viz` → rendered and stamped. Each is `seen`; `executed_actions` logs three `render-viz` rows; nothing in any `study.yaml` authored block changed.

### Cycle N+1 — the loop re-enters

1. Inventory: three files with meta. `classify_viz`: s5 both `fresh` (one by `model_version`, one by `source_run_id`); s4 `fresh`.
2. Validator: three `passed` records; frontier emits none of the three items (derivation discharge). `_close_built_viz_proposals` moves `vp:s4…` to `built` with `built.chart`, history `{event: built, by: engine}`.
3. `viz_census = {fresh: 3, proposals: {built: 1}}`; the linter's `viz_stale_vs_latest_run` and the Visualizations tab's freshness badges agree with the cycle because both read the same sidecar and the same `chart_freshness`.
4. A later run of s5 at a new `model_version` flips both s5 figures to `stale` (one via run id, one via mv) → `refine_viz` → the loop turns again. **That is one full turn of the visualization loop with every write explicit, every figure produced by the workspace's own renderer, and nothing fabricated.**

Code surface: `viz_freshness.py` +~90 (`normalize_entry`, `classify_viz`, meta `model_version`, mtime fallback); `viz_proposals.py` new ~200; `validation.py` +~50; `discovery_engine.py` +~45; `discover.py` +~200 (`viz_inventory`, `accept_viz_proposal`/`declare`, render branch, advance eligibility, `_post_render_stamp`, `_close_built_viz_proposals`, `discover viz` CLI); `run_command.py` +~15; schema +~25. Workbench: `viz_build.py` new ~120 + CLI verb ~30; `render_visualizations` fix + stamp ~20; `cycle_trace` +~40 (`propose_viz` step, labels, `viz_state`/`viz_catalog` harvest in `load_inputs`, `/build-viz` + `/viz` endpoints); `discovery-cycle.js` +~40. viva-genesis: `viz_endosym.__main__` ~15 + three entry edits. ≈ 850 lines plus tests.

# 5. Frontier kinds and ops — the complete delta to the tables

```python
_PRIORITY += {"viz_broken": 47, "viz_missing": 46, "viz_proposed": 44, "viz_stale": 38}
_OP       += {"viz_broken": "refine_viz", "viz_missing": "build_viz",
              "viz_proposed": "build_viz", "viz_stale": "refine_viz"}
_OPP_INFO += {"build_viz": 0.4, "refine_viz": 0.2}
_OPP_COST += {"build_viz": 0.3, "refine_viz": 0.2}
# _CLOSURE_BLOCKING unchanged.
# _executor_for:
#   build_viz / refine_viz on "fig:<study>/<name>" → {"executor": "render-viz",
#       "command": render_command(study, name), "consequence": "render", "auto_safe": False}
#   build_viz on "vp:…"  → {"executor": "discover viz", "command": "discover viz <inv> <vp> --accept|--reject",
#       "consequence": "review", "auto_safe": False}
#   refine_viz on a broken_stub → {"executor": "discover viz", "command": "discover viz <inv> fig:… --declare …",
#       "consequence": "review", "auto_safe": False}
RESOLUTION_KINDS += ("viz_broken", "viz_missing", "viz_proposed", "viz_stale")
_ADVANCE_OPS = ("test", "branch", "build_viz", "refine_viz")   # render consequence admitted
```
Workbench mirrors: `_KIND_LABEL`, `_KIND_TAB` (→ `visualizations`), `_RESOLVABLE_KINDS` (+4), `STEPS` + `{"id": "propose_viz", "module": "viz_proposals", "func": "propose_visualizations", "kind": "pure", "depends_on": ["run_cycle"]}`; the `run_cycle` step summary gains "· N figures (f/s/u/b)".

# 6. Separation of concerns

| Layer | Owns | Never does |
|---|---|---|
| **discovery-engine (core)** | `normalize_entry`, `classify_viz`, the validator, the frontier kinds/ops, `propose_visualizations`, the proposal ledger + `accept_viz_proposal`/`--declare` write-backs, `render_command` template, the `render_hook` seam, `_post_render_stamp`, `_close_built_viz_proposals` | render anything; import plotly / process-bigraph / a workspace package / `vivarium_workbench`; invent a viz class or an address not in the catalog; edit an existing `visualizations[]` entry without an explicit `--declare` + rationale |
| **discovery-engine (`viz_inventory`, in `discover.py` beside `_load_runs_by_study`)** | the one disk read (file existence, meta, mtime, hash, orphan `viz/*.html`) | execute a render |
| **vivarium-workbench** | `viz_build.render_one` (dispatch to the two existing render paths, stamp meta), the `render-viz` CLI verb, passing `render_hook` + `viz_state` + `viz_catalog` into the engine, the panel surfaces (Build / Apply fix / Accept / Reject, labels, tab links), the clobber fix in `render_visualizations` | decide what is stale or missing (it asks the engine), approve a proposal silently |
| **workspace (viva-genesis)** | native `render_*` functions behind a `python -m` entry; declaring `address`/`render`/`requires`/`covers` on its entries; viz classes under `<pkg>/visualizations/` | — |
| **viva-superpowers `/viva-report` §A8, `/viva-viz`** | creative, non-derivable proposals (written through `discover viz --propose`), authoring the class a `needs_author` proposal asks for | bypass the ledger |

The generality refactor is honored: a non-workbench consumer sets `set_render_command_template(...)` and supplies its own `render_hook`; with neither, `advance` reports render ops as `would-run` with the templated command, exactly as it does for `test` ops today.

# 7. Scope discipline — what is cut or deferred

| Considered | Verdict | Why |
|---|---|---|
| Engine-side rendering (a `render_viz` function in `discovery_engine`) | **Cut** | The engine is a dependency of the workbench and must not grow a renderer (same rule that kept the campaign runner out). Rendering is an injected capability. |
| `critique_figure` / figure quality or "informativeness" scoring | **Cut (again)** | No derivable signal; the honest version is `/viva-report` A8 prose, now persisted as proposals. |
| Auto-generating viz classes for `needs_author` proposals inside the cycle | **Cut** | Authoring stays `/viva-viz`; the proposal carries the brief. An AI decider may *accept* a derivable proposal, never write code. |
| Auto-accepting derivable proposals | **Cut** | Expert-gated like `revise --approve`; the decider seam exists for autonomous sessions, with a rationale, logged `by: discovery-ai`. |
| A `callable:` form of `render:` (`{callable: pkg.mod:fn, args}`) | **Defer (wave 3)** | A `python -m` entry costs the workspace 15 lines and needs zero engine/workbench change; `refresh_viz` already executes the string form with the right env. |
| Making viz kinds closure-blocking | **Cut** | Closure is scientific (criteria/gaps/challenges). Publication gating already exists in the linter (`missing_visualizations` hard gate, `viz_stale_vs_latest_run`) and stays there. |
| A new `study.yaml` block (`figures[]`, `viz_state`) or a viz schema bump | **Cut** | `visualizations[]` with normalized fields is the single declaration; `additionalProperties: true` already admits `render/requires/covers/origin`. |
| Migrating `embed_visualizations[]` into `visualizations[]` | **Defer** | The normalizer can read it as `kind: figure` entries later; nothing in the loop depends on it. |
| Integrating the native gallery (`viz.json` panels) into freshness | **Defer** | Those panels are run-produced, fresh by construction, and `study_charts` already dedupes them against declared entries by name. |
| Proposals from *every* numeric finding | **Cut → collapsed into `gate_uncovered`** | Findings cite `from_test`; a finding with none is an evidence problem (REPAIR), not a viz gap. Caps spam. |
| A second freshness store or "accepted figure snapshot" | **Cut** | The `<output>.meta.json` sidecar is the provenance; `source_run:` is the pin. |
| Changing `needs_attention._stale_viz_debts` | **Replace its input** | It reads a `freshness` field no writer sets; point it at the cycle's visual records (or `cycle_state.viz_census`) so the decisions-needed scan and the frontier agree. Small; wave 1. |
| Honoring `rendered:` as a file pointer | **Cut** | Nothing renders from it; accepting it would hide the broken stub the loop exists to catch. Linter nudge + `--declare` fix instead. |

# 8. Sequenced roadmap — three waves (+ one conditional)

### Wave 0 — Catch (the s5 stub appears on the frontier)
*Scope:* `normalize_entry`, `classify_viz` (with mtime fallback), `viz_inventory`, `ValidationContext.viz_state`, validator `visual.figure.freshness`, step-4 skip + step-7 projection for `viz_broken`/`viz_missing`/`viz_stale`, `RESOLUTION_KINDS`, `cycle_state.viz_census`; workbench: `load_inputs` harvests `viz_state`, labels/tab, **the `render_visualizations` clobber fix + typed error + `stamp_meta` in that path**.
*Exit criterion:* on viva-genesis, `discover cycle eukaryogenesis` lists `viz_broken fig:s5-symbiont-to-organelle/eigen-catastrophe` with the derived fix in its rationale and `viz_stale`/`viz_missing` for every `local:` viz lacking meta; `run_cycle(inv, studies)` with no `viz_state` emits zero visual records (fixtures unchanged); re-rendering s5 via `/api/investigation-render-viz` no longer overwrites `viz/eigen-catastrophe.html`.

### Wave 1 — Build / refine as cycle ops
*Scope:* `_executor_for` render tier, `render_command` template, `execute_action` render branch + `render_hook`, `advance_cycle` eligibility/skip/`_post_render_stamp`, `discover viz <inv> fig:… --declare`; workbench `viz_build.render_one`, `render-viz` CLI, panel Build / Apply-fix buttons, `needs_attention` repointed; viva-genesis `viz_endosym.__main__` + the three entry declarations.
*Exit criterion:* a fixture with a fake `render_hook` (writes+stamps / writes unstamped / writes nothing) shows: one advance renders each resolvable stale/missing entry once, stamps the unstamped one, gates the failing one, and the next cycle's frontier has none of the rendered items; on genesis, the sandboxed "Run continuously" shows `render-viz` rows and s5's two figures turn fresh.

### Wave 2 — Propose / accept
*Scope:* `viz_proposals.propose_visualizations` (three triggers, cap 2/study/cycle, catalog-derived addresses), `viz_proposals[]` + schema, `accept_viz_proposal` (+ `--reject`, `--propose` for A8), `viz_proposed` kind, decider branch for `vp:`, `_close_built_viz_proposals`, `propose_viz` STEP + Accept/Reject in the panel; `/viva-report` §A8 writes through the ledger.
*Exit criterion:* a two-cycle fixture: cycle 1 → one `gate_uncovered` proposal for an uncovered acceptance gate, none for a gate covered via catalog inputs; `--accept --address …` → a `visualizations[]` entry with `origin`, the proposal item gone, a `viz_missing` item present; fake-hook advance → cycle 2 shows the proposal `built` with `built.source_run_id`; a re-run proposes nothing new (content ids).

### Wave 3 (conditional on the loop turning on genesis)
`model_version` in meta for `requires: []` figures (classifier prefers it); `callable:` render form; `embed_visualizations[]` normalization; coverage from a viz class's `inputs()` when no catalog is passed (import-free, from the linter's `visualizations/` scan).

# 9. Invariants and risks

**Invariants (each primitive is checked against them):**

1. **Derive-first purity, one harvest.** `normalize_entry`, `classify_viz`, the validator, `propose_visualizations` are pure over `(inv_spec, studies, viz_state, viz_catalog, latest runs)`. The only new disk read is `viz_inventory`, called by impure callers and passed in — never inside `run_cycle`. Writes happen in exactly three places: `persist_cycle` (ledger merge, `built`, census), `accept_viz_proposal` / `--declare` (history event + one `visualizations[]` entry, with rationale and `origin`), and the render hook (figure file + meta sidecar — regenerable artifacts, not scientific state).
2. **Never fabricate, never clobber.** A render failure leaves the previous figure and meta untouched (`refresh_viz` already does; `render_visualizations` must). An unbuildable entry is *reported*, never replaced with a stub. No placeholder image is ever written to satisfy a frontier item.
3. **Authored wins.** `visualizations[]` is authored. The engine appends an entry only on explicit accept/declare with `origin: {kind: viz_proposal | derived_fix, ref}` so engine-origin entries are distinguishable and purgeable; it never edits `title`, `config`, or an existing address silently.
4. **Epistemic separation.** `visual` is execution-only: a fresh figure carries `scientific_judgment: null` by construction (`validation_record` raises otherwise). The loop makes findings *legible*; it never makes them *true*.
5. **Closure semantics unchanged.** All four kinds are non-blocking; publication gating stays in the linter.
6. **No loops.** Content-addressed proposal ids; discharge by re-classification only; render skip iff already stamped against the latest run (the per-mv-skip analog); `seen`/`gated_ops`/`max_runs`.
7. **Additive and general.** One investigation block, zero study-schema changes, one validator, two ops, one STEP, one CLI verb each side; zero `vivarium_workbench` imports in the engine; renderer and run command both templated.

**Risks and mitigations:**

| Risk | Mitigation |
|---|---|
| **Freshness is a proxy for figures that do not derive from `runs.db`** (s5's native sweep runs the model itself; the "latest run" in `runs.db` is unrelated to what the figure depends on) → false `stale`, needless minutes-long re-renders, erosion of trust in the frontier | Wave 0 classifies such entries by the `discover_viz_html_files` mtime rule, which only flags them once per new run; wave 1 stamps meta so they are fresh until the next run; wave 3's `model_version` in meta makes the signal exact for `requires: []` entries (stale iff the model changed). `source_run:` pin remains the explicit opt-out. |
| Every `local:` viz has no meta today → wave 0 floods the frontier with `viz_stale` | Mtime fallback limits it to figures older than their latest run; the clobber-fix PR also stamps on render so one `render-viz` pass clears the backlog; `viz_census` reports the count before merge (the "count on genesis first" discipline from wave 0 of the closed-loop decision). |
| Proposal spam on instances with many uncovered observables | Three high-signal triggers only (gate / discriminating / disagreement); cap 2/study/cycle; content ids; `rejected` is terminal; coverage via `covers` ∪ `requires` ∪ `inputs_map` ∪ catalog inputs means an already-visualized gate proposes nothing without any edit. |
| A render runs a simulation (native sweeps) under the `render` tier, so "cheap" is not guaranteed | Consequence `render` is not `auto_safe`; `max_runs` counts renders; `refresh_viz`'s 900 s timeout applies; `effort: needs-run` is surfaced on proposals so the expert sees the cost before accepting. |
| The two workbench render paths diverge (one stamps, one does not; one preserves on error, one overwrites) | `render_one` is the single dispatcher and the design requires the `render_visualizations` fix as wave-0 scope; a test asserts both paths stamp and neither overwrites on failure. |
| `--declare` on a broken stub edits an authored list | Review tier, rationale required, `origin: {kind: derived_fix}` written, never dispatched by `advance`; the derived address is only ever a file that exists in the study's `viz/`/`charts/` and matches by stem. |
| Catalog-derived `address` picks a class whose inputs match by type but not by meaning (any `list[float]` viz "covers" any observable) | Coverage uses the *observable name* through `inputs_map`/`covers`, not the type; type match only marks a proposal `derivable` — acceptance still names the address explicitly in history, and the expert sees the class. |
| `cycle_trace._run_one("run_cycle")` calls `run_cycle(inv_spec, studies)` without `viz_state` → the panel never shows viz items | `load_inputs` gains the harvest (as it gained `runs_by_study`), and `run_cycle` is called with `viz_state`/`viz_catalog` — part of wave 0's workbench scope, with a test that the panel's frontier includes `fig:` targets on the fixture workspace. |

# 10. Departures from the model-revision template, in one place

- Proposals persist on the investigation (`viz_proposals[]`, like `revision_proposals[]`), but **acceptance materializes into the study's existing `visualizations[]`** rather than a new child artifact — a figure belongs to a study, and the declaration is already the contract every renderer reads.
- **Discharge is by derivation, not by a status flip:** a built or refreshed figure leaves the frontier because the next cycle classifies it `fresh`; only the *proposal* carries a terminal `built` status (the `evaluated` analog).
- **The executor is a hook, not a study run:** `render_hook` / `render_command` sit beside `runner` / `run_command`; `advance` treats `render` as a dispatchable consequence distinct from `simulation`.
- **The check is a registered validator** (filling the reserved `visual × figure` cell) projected into typed kinds via the `narrative` precedent — not a new frontier source.
- **No analog of `materialize_design` or `bind_model_versions`:** the only identity a figure needs is its output path plus the meta sidecar; `model_version` is borrowed from the model loop as an optional freshness anchor for figures that run the model.
