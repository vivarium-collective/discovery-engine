# Discovery Engine — Interactive Map Specification

> Spec for the clickable cycle-diagram realization of `discovery-engine-cycle-map.pdf`.
> Ground-truth: `discovery-engine` 0.6.0 (`main`, HEAD `50dbfad`). Verified against the
> live codebase 2026-11-18. This document is the single source of truth for the HTML
> artifact's data model, layout, and interaction.

---

## 1. Purpose

A reader should be able to **see the whole discovery cycle at once** and **click any
stage** to learn: the functions that implement it, the skills that drive it, what it
consumes and produces, which of the four persistent artifacts it writes, and whether it
is real / conditional / unwired. The diagram is the hero; the detail panel is the depth.

Three things the static PDF cannot do that the HTML must:

1. **Trace information flow** — clicking a stage highlights the edges in/out of it
   (which artifact it reads, which it writes, which stage it feeds).
2. **Filter by provenance and grade** — dim everything except (H)-authored, or
   (D)-derived, or `real` vs `conditional` stages.
3. **Walk the loop** — a "play the cycle" control steps Load → … → Execute → back to
   the substrate, narrating each hop.

---

## 2. The model (nodes)

Every node is one object. The HTML embeds this array verbatim as its data source.

```
Node = {
  id:        string,              // stable key, kebab-case
  label:     string,              // short diagram label
  band:      Band,                // which ring/zone it sits in (layout + color)
  functions: [{name, module}],    // engine functions, with file anchor
  skills:    [string],            // /viva-* or engine authoring skills that drive it
  consumes:  string,              // one line
  produces:  string,              // one line
  writes:    [ArtifactId],        // which substrate artifacts it mutates (often [])
  reads:     [ArtifactId],        // which substrate artifacts it reads
  grade:     "real"|"conditional"|"thin",
  purity:    "pure"|"mutating",
  blurb:     string               // 1–3 sentence panel body
}

Band = "substrate" | "core" | "phase" | "gate" | "seam" | "growth" | "reopen" | "execute" | "discovery"
ArtifactId = "investigation" | "study" | "runs" | "modelversions"
```

### 2.1 Substrate artifacts (band: `substrate`) — the four persistent things

| id | label | authored | cycle-written | one measured? |
|---|---|---|---|---|
| `investigation` | `investigation.yaml` | question, studies[], hypotheses[], acceptance_criteria[], plan_gates[], required_mechanisms[], relation tags | resolutions[], **discoveries[]**, hypothesis_proposals[], critique_reports[], model_acquisition_proposals[], model_versions[], pending_feedback[], applied_feedback[], lifecycle_history, cycle_state.evaluation_snapshot | — |
| `study` | `study.yaml` (per member) | claim, behavior_tests (pass_if+measure), hypothesis, findings prose, baseline/variant arms, parent_studies | runs[] (synced), computed_outcomes, finding measured slots | — |
| `runs` | `runs.db` (per-study SQLite) | — | written by the simulation runner (`run_registry.register_run`) | **yes — the one measured input** |
| `modelversions` | `model_versions[]` (derived cache on investigation.yaml) | — | content-addressed `mv:` per arm; parent-linked children (`bind_model_versions`) | — |

### 2.2 Cycle stages (the ring + satellites)

| id | label | band | functions (module) | skills | grade / purity |
|---|---|---|---|---|---|
| `inputs` | Load | core | `load_cycle_inputs` (discover/cycle.py) | `/viva-investigation` | real / pure |
| `core` | Project | core | `run_cycle` (discover/cycle.py) | — | real / pure |
| `reconcile` | A · Reconcile | phase | `reconcile_evidence_state` (phases/evidence_integrity.py) | — | real / pure |
| `plan` | B · Plan | phase | `plan_candidate_actions`, `select_portfolio` (phases/candidate_action.py) | — | real / pure |
| `design` | C · Design | phase | `design_discriminating_study` (phases/experiment_design.py) | — | conditional / pure |
| `build` | D · Build | phase | `build_catalog`, `bind_model_versions`, `model_version` (phases/model_architect.py) | `/viva-expert` | real / pure |
| `grade` | Grading | phase | `compute_outcomes` (study/evaluator/outcomes.py) | `/viva-study` | real / **mutating** |
| `evaluate` | F · Evaluate | phase | `compare_hypotheses` (phases/evaluation.py), `detect_finding_candidates` (phases/discovery_record.py), `diagnose_refutation` (phases/model_error.py) | `/viva-biology-forward` | conditional / pure |
| `gate` | Gates | gate | `frontier`, `closure`, `next_action`, `opportunities`, `execute_plan`, `AutonomyPolicy` (projection/engine.py, discover/_policy.py) | `/viva-harden-investigation`, `/viva-navigate` | real / pure |
| `emit` | Generate · emit | seam | `generation_requests` (phases/generation_emit.py) | — | real / pure |
| `fill` | Generate · fill | seam | `subprocess_filler` (phases/generation_apply.py) | **`/viva-fill`** | real / mutating (subprocess) |
| `apply` | Generate · apply | seam | `apply_result`, `run_requests` (phases/generation_apply.py) | `hypothesize`, `critique`, `acquire_model` | real / mutating |
| `propose` | Grow · propose | growth | `suggest_studies` (discover/cycle.py) | — | real / pure |
| `select` | Grow · select | growth | `select_for_launch` (growth/seeding.py) | — | real / pure |
| `seed` | Grow · seed | growth | `build_seeded_study`, `build_child_seed_from_finding` (growth/seeding.py, growth/seed_from_followup.py) | — | real / pure |
| `refine` | Grow · refine | growth | `refine_study` (growth/refinement.py) | — | real / pure |
| `reopen` | Reopen | reopen | `reconcile_and_reopen` (feedback/compiler.py), `ingest_feedback` | — | real / pure-planner |
| `execute` | E · Execute | execute | `advance_cycle` (discover/cycle.py) | `/viva-harden-investigation` | real / **mutating** |
| `discovery` | Discovery | discovery | `detect_finding_candidates`, `review_findings`/`promote_finding_candidate` (discover/actions.py) | — | real / mutating (on promote) |

> The nine generation **KINDS** are a sub-object of the `apply` node, rendered as a table
> in its panel: interpret_finding, explain, author_tests, resolve_decision (native);
> hypothesize, critique, acquire_model (registry); propose_study, visualize (queued).

### 2.3 Field-level I/O (the `IO` map)

Each node also carries the **exact `investigation.yaml` / `study.yaml` / `runs.db` fields**
it reads and writes, verified against 0.6.0 HEAD `50dbfad`. The HTML embeds this as a
separate `const IO = { <node-id>: { r:{inv:[],study:[],runs:[]}, w:{…} } }` map and
renders it in the detail panel under **"Information flow — by field"**; the PDF carries
the same data as its §4 field-flow table. Keep the two in sync — both regenerate from the
field-level inventory passes, not by hand-editing.

Conventions that matter for fidelity:
- `interpret_finding` writes `findings[].statement/explanation/status` — **not `summary`**.
- `model_versions[]` write subfields: `id, composite, params, parent_ids, origin, bound_to`.
- The gate reads only `investigation.yaml` caches (`resolutions[]`, `pending_feedback[]`,
  the proposal blocks, `cycle_flags`, …) — no `study.yaml` directly; study verdicts arrive
  via the in-memory cycle bundle.
- Phase functions (Evaluate, candidates, diagnosis) are pure — their results are
  **persisted by `persist_cycle`**, which writes `cycle_state.evaluation_snapshot` and
  `finding_candidates[]`; the reviewed promotion (`review-findings`) is the only writer of
  `discoveries[]`.
- `build_seeded_study` / `refine_study` are pure spec builders; the disk writes live in
  `write_child_study` (new child + parent `findings[].seeded_study` stamp) and
  `execute_action` (member registration into `studies[]`).

---

## 3. The model (edges) — information flow

```
Edge = { from: NodeId, to: NodeId, kind: EdgeKind, label?: string }
EdgeKind = "flow" | "read" | "write" | "feedback"
```

- **flow** (solid, accent) — the pure projection ring, stage → stage.
- **read** (thin, muted) — a stage reads a substrate artifact.
- **write** (bold, warn) — a stage writes a substrate artifact (the mutating edges).
- **feedback** (dashed, muted) — a loop-closing edge back into the substrate / Load.

### Edge list (authoritative)

```
# the pure projection ring
investigation → inputs            (read)
study         → inputs            (read)
inputs        → core              (flow)
runs          → grade             (read)      # the one run-data read
grade         → study             (write)     # computed_outcomes
core          → reconcile         (flow)
reconcile     → plan              (flow)
plan          → design            (flow)
design        → build             (flow)
build         → modelversions     (write)     # mv: ids, parent-linked
core          → evaluate          (flow)
grade         → evaluate          (flow)      # computed-first verdicts
evaluate      → gate              (flow)
evaluate      → discovery         (flow)      # FindingCandidate
build         → evaluate          (flow)      # model arms → disagreement

# the gate fans out
gate          → emit              (flow)      # opportunities → requests
gate          → propose           (flow)      # frontier → suggestions
gate          → execute           (flow)      # execute_plan → dispatch

# the generation seam
emit          → fill              (flow)
fill          → apply             (flow)
apply         → investigation     (write)     # registry proposals + resolutions
apply         → study             (write)     # native finding/test writers

# growth (generative loop)
propose       → select            (flow)
select        → seed              (flow)
seed          → study             (write)     # new run-ready study.yaml
refine        → study             (write)
seed          → inputs            (feedback)  # new member re-enters
evaluate      → propose           (flow)      # refuted finding → next study

# execute (the one mutating phase)
execute       → runs              (write)     # runs sims
execute       → investigation     (write)     # promotes seeds, builds, reopens
execute       → modelversions     (write)     # needs_build → new mv

# discovery
discovery     → investigation     (write)     # review-findings --promote → discoveries[]

# reopen (re-entry)
reopen        → investigation     (write)     # reopened spec + lifecycle_history
investigation → reopen            (read)      # pending_feedback[]
reopen        → inputs            (feedback)  # re-enters the cycle
```

---

## 4. Layout

A **radial cycle**. The four substrate artifacts sit in a **center column**; the pure
projection ring wraps clockwise around them; the seam, growth, reopen, and discovery
nodes are satellites tethered to their anchor stage.

```
                        ┌──────────── pure projection ring ────────────┐
                        │   Load → Project → A → B → C → D              │
   ┌─────────────┐      │                      │                       │
   │ investigation│◀────┤                      ▼                       │
   │ study.yaml   │     │   Execute ◀ Gates ◀ F·Evaluate               │
   │ runs.db      │────▶│      │        │          │                   │
   │ model_versions│     │      │        ├─▶ Generate(emit→fill→apply)  │
   └─────────────┘      │      │        └─▶ Grow(propose→select→seed)   │
      (substrate,       │      ▼                   │                    │
       center)          │   writes            Discovery / Reopen        │
                        └───────────────────────────────────────────────┘
```

**Responsive:** at phone width the ring linearizes into a vertical stack (Load at top,
Execute at bottom, substrate as a sticky header card); edges become left-rail connectors.
No horizontal scroll; 16px side gutter.

**Node size** encodes nothing; **band** encodes color; a small **corner glyph** encodes
purity (● pure, ◆ mutating) and a **dot** encodes grade (green real / amber conditional /
red thin).

---

## 5. Color system (tokens)

Mirror the PDF palette. Define on `:root`; redefine under the two dark-mode guards.

| token | light | role |
|---|---|---|
| `--ink` | `#1F2937` | text |
| `--muted` | `#64748B` | secondary text, read edges |
| `--accent` | `#2563EB` | flow edges, links, core band |
| `--good` | `#15803D` | `real` dot |
| `--warn` | `#B45309` | `conditional` dot, write edges, mutating glyph |
| `--bad` | `#B91C1C` | `thin` dot, blocking frontier kinds |
| band `substrate` | `#FFFFFF` / border | the four artifacts |
| band `core` | `#E0E7FF` (authored) | Load, Project |
| band `phase` | `#F1F5F9` (panel) | A–D, Grading, Evaluate |
| band `gate` | `#F1F5F9` | the gate views |
| band `seam` | `#F3E8FF` (skill) | emit / fill / apply |
| band `growth` | `#E0E7FF` | propose / select / seed / refine |
| band `reopen` | `#E0E7FF` | reopen |
| band `execute` | `#FEF3C7` (measured) | advance (the mutating phase) |
| band `discovery` | `#DCFCE7` (derived) | candidate → Discovery |

Provenance chips in panels: **(H)** ink, **(AI)** seam-purple, **(D)** good-green, **(CFG)** muted.

---

## 6. Interaction

1. **Default** — full diagram, nothing selected, legend visible.
2. **Hover node** — tooltip (label + one-line role); incident edges brighten.
3. **Click node** — opens the **detail panel** (right rail on desktop, bottom sheet on
   mobile) with: title, band tag, grade + purity badges, Functions (name → `module` as
   monospace), Skills (chips linking to §6 of the PDF concept), Consumes / Produces,
   Writes-to (artifact chips that pulse on the diagram), and the blurb. For `apply`, also
   the nine-KINDS table. For `gate`, also the frontier-kinds table + the opportunities
   score formula. Non-incident nodes dim to 25%.
4. **Filters** (top bar, multi-select):
   - **Provenance**: H / AI / D / CFG — dims nodes whose authorship chip doesn't match.
   - **Grade**: real / conditional / thin.
   - **Purity**: pure / mutating (one click to see "what touches disk" — the mutating set).
   - **Writes**: pick an artifact → highlight only the stages that write it (answers
     "what can change my investigation.yaml?").
5. **Play the cycle** — a transport control that walks the ring (Load→…→Execute), opening
   each panel ~1.2s, drawing the active edge, then the feedback edge back to the substrate.
6. **Deep link** — `#<node-id>` opens that node's panel on load (shareable).
7. **Search** — a box that matches node labels, function names, and skill names; Enter
   selects the first match.

Keyboard: arrow keys move selection along the ring; Esc closes the panel; `/` focuses search.

---

## 7. Content fidelity rules

- Every function name and module path must match the live code (the tables in §2 are the
  verified set as of 0.6.0 HEAD `50dbfad`). If the engine moves, regenerate from the three
  inventory passes, do not hand-patch.
- **Do not** describe `detect_discoveries` as live — it is retired; the path is
  `detect_finding_candidates` → `review-findings --promote`.
- **Do not** say changed measured inputs make a snapshot stale — only grader/resolver
  **version** drift does.
- `propose_study` and `visualize` are **queued** at the apply seam, not native-wired.
- The three blocking frontier kinds are exactly `unaddressed_criterion`, `narrative_gap`,
  `open_challenge`.
- Keep `/viva-suggest` and `/viva-run` labeled "not part of the cycle."

---

## 8. Build notes

- Single self-contained HTML file; inline SVG for the diagram (no chart lib needed), the
  node/edge arrays as a `const DATA = {…}` block near the top.
- Title (2–4 words): "Discovery Engine Map". Explanation goes in `<meta name=description>`.
- Works at phone width; both light and dark mode; `body` has an explicit background.
- Footer line mirrors the PDF: `discovery-engine 0.6.0 · derive-first · one measured input
  · one LLM seam · the loop closes`.
```
