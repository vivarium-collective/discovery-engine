# Proactive question generation (coverage-gap frontier + agent-authored hypotheses)

**Status:** Phase 1 implemented (this PR); Phase 2 proposed.
**Issue:** AI-drivable-cycle gap #2 — "surface fundamental questions not yet asked."

## Problem

The discovery frontier is **reactive**. Every frontier source responds to
something that already exists: an acceptance criterion not yet met, an anomaly
outside the live bands, an unassessed claim, an authored decision, a capability
gap. Nothing **proactively proposes a new fundamental question** about the
system's behaviour — the kind a scientist asks of a well-characterised baseline
(growth-law scaling across conditions, single-cell variability, proteome
allocation limits, robustness).

Empirically confirmed: on `baseline-showcase` (6 studies, rich runs + figures +
behavior_tests, 0 findings, 0 hypotheses), `discover hypothesize` is anomaly-only
("no unexplained anomalies") and `suggest` only completes declared decision
points. The four fundamental hypotheses the investigation needed (growth laws /
adder / initiation mass / overflow) had to be authored **by hand** — the engine
could not generate them.

## Design

Two layers, with a hard boundary between them. **The engine stays LLM-free.**

### Layer 1 — deterministic coverage-gap frontier (Phase 1, this PR)

`discovery_engine/projection/coverage.py::coverage_gap_items(cycle_result, inv_spec)`
emits one `coverage_gap` frontier item per **configured scientific dimension no
study covers yet**:

```yaml
# investigation.yaml
discovery:
  coverage_dimensions:
    - name: growth-law scaling across conditions
      keywords: [growth law, scaling, nutrient]
      question: How does growth rate scale across nutrient conditions?   # optional
    - name: single-cell variability
      keywords: [variability, cell-to-cell, noise]
    - name: proteome allocation limits
      keywords: [proteome, allocation, ribosome budget]
    - name: robustness
      keywords: [robustness, perturbation, stress]
```

A dimension is **covered** when any of its keywords (case-insensitive substring;
the `name` itself when no keywords are given) appears in the investigation's
authored framing (`question`, `hypothesis`, `scientific_argument`, `key_claims`,
`biological_story`, `at_a_glance`) plus the derived claim statements. Otherwise a
`coverage:<slug>` item is raised, priority 34 (exploratory, non-blocking, never a
closure-blocker), op `branch` → `propose_study` → the `viva-investigation` skill
(the request now carries that skill via #1). The dimension's `question` becomes
the request rationale.

**Why config, not inference.** The engine is domain-general; it cannot know that
"growth-law scaling" matters for an *E. coli* baseline without being told — that
is domain knowledge. The workspace declares the dimensions its domain should
cover; with none configured the detector emits nothing. This keeps the engine
honest (it never invents a domain's science) and the gap detection deterministic
and testable.

### Layer 2 — agent-authored questions/hypotheses (Phase 2, proposed)

The `hypothesize` generation kind already exists (produces `hypothesis_proposal/v1`,
engine skill `discovery_engine/skills/hypothesize/SKILL.md`). Phase 2 routes a
`coverage_gap` to an authoring agent that writes the actual **question + hypothesis
about what SHOULD happen** (not just "propose a study"), grounded in the model +
literature. The engine emits the gap and the request envelope; the agent authors
the science.

Reference output shape (the four baseline hypotheses authored by hand on
`baseline-showcase`): H1 Scott–Hwa growth laws, H2 Taheri-Araghi adder, H3
Donachie initiation mass, H4 overflow metabolism — competing/proposed hypotheses
grounded in the model + literature.

**Honesty boundary (both phases).** The engine detects the gap deterministically.
It never authors the question or hypothesis itself — a template "fundamental
question" generator would emit filler. A `coverage_gap` with no agent downstream
proposes a study (Phase 1); the *content* of the question/hypothesis is always
agent-authored.

## Tests (Phase 1)

`tests/test_coverage_frontier.py`: no-config → no items; uncovered raised /
covered skipped; the dimension's `question` is the rationale; coverage by a
derived claim; keyword-less name match; and the end-to-end
`coverage_gap → frontier → propose_study (viva-investigation)` route.
