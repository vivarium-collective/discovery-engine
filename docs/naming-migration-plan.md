# Discovery Engine naming migration — plan

Adopts the naming & architecture recommendations (the six-stage public cycle, four
services, the Observation→Finding→Discovery progression, frontier split) across the
public-facing surfaces. **Compatibility-preserving:** no `study.yaml`/`investigation.yaml`
schema, Python-API, or A–F execution-phase changes in these phases.

**Decisions taken:** depth = **Phases 1–2** (public-facing only); skills/CLI =
**hard rename** (not aliases); cycle map = **single source-of-truth data +
drift-check** (`cycle-spec.json`, rendered by the HTML and validated against the
engine). See [`glossary.md`](glossary.md) for the canonical vocabulary.

## Phase 1 — Conceptual + figures (`discovery-engine`) — IN PROGRESS

- [x] **Source of truth** — `docs/cycle-spec.json`: six stages, four services,
      scientific objects, frontier split, and the full node/function/skill/IO model.
- [x] **Interactive HTML** — `docs/cycle-map.html` renders from the embedded
      canonical spec (offline-safe), relabeled to the six stages, `/viva:` skill
      names, per-node stage shown; `docs/build_cycle_map.py` keeps it in sync.
- [x] **Drift-check** — `tests/test_cycle_spec_drift.py`: every referenced function
      resolves in the engine, skills are real, the HTML copy matches the spec.
- [x] **Glossary** — `docs/glossary.md`.
- [x] **Pages publish** — explicit `.github/workflows/pages.yml` (deploy `docs/` on
      push to `main`), replacing the implicit/legacy mechanism.
- [ ] **PDF map** — relabel `docs/discovery-engine-cycle-map.tex` stage headings to
      the six stages + add the four services + frontier split; recompile. (Next.)
- [ ] **Prose** — `architecture.md` / `roadmap.md` adopt the six-stage + four-service
      vocabulary. (Next.)

## Phase 2 — Public interface (`viva-superpowers` hard rename + DE CLI umbrella)

- [ ] **Hard-rename 6 skills:** `investigation→investigate`, `fill→reason`,
      `expert→model`, `harden-investigation→validate`, `navigate→explore`,
      `drive-engine→discover`. (`cite-bands→calibrate` / `biology-forward→interpret`
      are in-skill section renames — not standalone skills.)
- [ ] **Reference sweep (hard-rename blast radius):** `docs/skills.md`, `README`,
      `CLAUDE.md`/`AGENTS.md`, `plugin.json` + `marketplace.json`, the pinned
      `test_skill_manifests` set, the `orient` routing table, sibling cross-links,
      the memory index, the Mac-mini mirror. Update `_KNOWN_SKILLS` in the
      cycle-spec drift-check and the node `skills` refs to the new names, in lockstep.
- [ ] **CLI umbrella (additive):** `discover question|hypothesize|model|experiment|evaluate|synthesize`
      composing the existing verbs + the five entry points (`investigate`, `status`,
      `study`, `review`, `report`). No low-level verb renamed.
- [ ] **Release:** viva-superpowers 0.29 (lockstep version in 4 places + the
      pbg-superpowers shim).

## Acceptance

- **No-orphan:** nothing references a renamed skill by its old name (grep gate).
- **Language consistency:** map, PDF, glossary, CLI help map to one glossary.
- **Drift-check green:** `cycle-spec.json` references only functions/skills the
      engine has, and the HTML matches the spec.
- **Compatibility:** `study.yaml` / `investigation.yaml` / Python API / run metadata
      untouched across Phases 1–2.

Deferred (a later, audited effort — **not** in this migration): semantic object
differentiation at the schema level (`finding`→`observation` field migration), and
internal method/module consolidation.
