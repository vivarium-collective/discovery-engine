# RFC: Unified Graded-Test Ontology

**Status:** Draft (for review by sbml-cycle + the workbench Audit owner)
**Owner:** discovery-engine (this design)
**Consumers:** viva-sbml-decomposition (study migration), vivarium-workbench (Audit-tab rendering)
**Motivating case:** the recovery-sbml enzyme study's Audit tab shows the *same test* as both
`✓ met` (acceptance) and `FAIL` (behavioral) at once.

---

## 1. Problem — one question, up to five stored answers, two bases

"Did test *T* pass?" is persisted in up to five places, computed from **two different bases**
that drift apart:

| # | Representation | Produced by | Reads |
|---|---|---|---|
| 1 | `runs[].computed_outcomes[T]` | `study_evaluator.compute_outcomes` | **run data** (the only one) |
| 2 | `pipeline_gate.gate_evaluator` | `study_verdict.write_gate_evaluator` → `roll_up_verdict` | authored `canonical_outcomes` |
| 3 | `executive.computed_acceptance` | `investigation_status.roll_up_acceptance` | authored `canonical_outcomes` (cached snapshot) |
| 4 | `knowledge.verdict` | `knowledge_store.canonical_verdict` → `roll_up_verdict` | authored `canonical_outcomes` |
| 5 | `viz/report_card/*.verdict.json` axes | `study_evaluator._grade_axis_from_outcome` / `card_grade` | the measured value, graded a **third** way (tolerance math) |

Plus two more hand-authored verdicts that can disagree: the authored `runs[].outcomes[T].result`
and the top-level authored `gate_status`.

### Root cause (concrete)

`compute_outcomes` (`study_evaluator.py:1706`) is the **only** path that reads a `RunReader` and
writes `runs[].computed_outcomes[T].result` with a `reconcile` flag. But every roll-up —
`roll_up_verdict` → `gate_evaluator` (`study_verdict.py:52,665`), `roll_up_acceptance` →
`computed_acceptance` (`investigation_status.py:70,127`), and `knowledge_store.canonical_verdict`
(`knowledge_store.py:104`) — calls **`canonical_outcomes()`** (`viva_workspace.outcomes`), which
returns the canonical run's **authored** `outcomes[T].result` and **never looks at
`computed_outcomes`**.

So a test whose run data computes **FAIL** while its authored outcome still says **passed** yields
`gate_evaluator = passed`, `computed_acceptance = passing (✓met)`, `canonical_verdict = supported`
— *simultaneously with* `computed_outcomes[T].result == "FAIL"` and `reconcile == "divergent"`.
That is the reported bug, and it is structural, not a one-off stale file.

Three aggravators on top of the authored/computed split:
- **No freshness stamp is read.** `GRADER_VERSION`/`_grader` (added in #22) is written on
  `computed_outcomes` but nothing compares it to detect staleness; the only regrade check
  (`study_audit._check_verdict_regrade`, `:696`) validates report-card JSON against itself, never
  computed-vs-authored.
- **`computed_acceptance` is a cached snapshot** in `investigation.yaml`, refreshed only when
  `write_investigation_acceptance` re-runs — stale even against the authored outcomes it reads.
- **The axis grades a third time**: `_grade_axis_from_outcome` (`:108`) grades the same
  `measured_value` into `within_tol/drift/mismatch` with tolerance math independent of the
  `pass_if` PASS/FAIL.

The only things that notice any of this are the boolean `reconcile` / `diverges_from_authored`
flags surfaced by `report_linter._check_verdict_divergence` (`:856`) — after the fact.

---

## 2. Design — one graded result; computed is truth; everything else is a view

### Invariant

> There is exactly **one graded result per (test, run)**, computed from run data. Acceptance,
> gates, study verdict, and the report card are **views** over it. Authored values are
> **expectations**, not competing truths. Divergence and staleness are **surfaced**, never
> silently resolved.

### 2.1 The canonical graded result — `computed_outcomes[T]`

`computed_outcomes[T]` is THE graded result. Unified shape (superset of today's, additive):

```yaml
result: PASS | FAIL | needs_rerun | ungraded      # the one verdict for this (test, run)
measured_value: <scalar | {generation: value}>
evaluated_by: code | agent | authored              # HOW it was graded
grading: {kind: <measure.kind>, op: <pass_if.op>, operator: "<kind>/<op>"}
axis: {verdict: within_tol|drift|mismatch|ungraded, expected, margin, severity, units}  # the tolerance dimension — a DIMENSION, not a 2nd verdict
provenance: {run: <run name|id>, store: resolved | unresolved | store_less}
reconcile: agree | divergent | no_authored         # vs the authored expectation
_grader: {version: <GRADER_VERSION>, graded_at: <iso>, inputs_hash: <hash>}   # freshness
```

- `evaluated_by: authored` is allowed for a test that genuinely has no code/agent grader — the
  author's result is then *recorded as the computed result with that provenance*, not kept as a
  separate parallel field. (This is how an honestly author-graded test joins the one model.)
- `axis` is attached to the SAME record (already is, at `:722`) — it never becomes its own
  `verdict.json` source of truth. `report_card/*.verdict.json` becomes a **render** of these.

### 2.2 Authored `runs[].outcomes[T]` → expectation/annotation

Authored `outcomes` stays for its prose and for the author's *expectation*, but **no roll-up
reads it as the pass/fail**. `reconcile` is the surfaced signal when computed ≠ expected. (Schema:
mark `outcomes[T].result` as an expectation field; keep it writable, stop treating it as truth.)

### 2.3 The roll-ups become views — one new function

Add **`canonical_graded_outcomes(spec) -> {test: graded_result}`**: returns the canonical run's
`computed_outcomes` (computed-first), falling back to authored `outcomes[T]` **only** when no
computed grade exists, and tagging such fallbacks `evaluated_by: authored, provenance.store: n/a`
so a view can show "author-asserted, never computed."

Then repoint the three roll-ups off `canonical_outcomes` onto `canonical_graded_outcomes`:
- `roll_up_verdict` (`study_verdict.py:70`) → gate_evaluator, study verdict.
- `roll_up_acceptance` (`investigation_status.py:101`) → `computed_acceptance`.
- `knowledge_store.canonical_verdict` (`:112`) + `_merged_run_outcomes` (`:35`).

By construction, gate / acceptance / knowledge-verdict now agree with `computed_outcomes`. The
enzyme study reads FAIL everywhere, once.

### 2.4 Freshness + provenance — the invalidation contract

Every graded result and every roll-up carries `_grader: {version, graded_at, inputs_hash}`:
- `version` = `GRADER_VERSION` (already exists from #22).
- `inputs_hash` = hash of (the test definitions + run provenance id + the workspace evaluator
  registry fingerprint). Changes when the *grader or its inputs* change.

A consumer (the Audit tab, the card, the discovery cycle) compares a record's stamp to the
current grader; **mismatch ⇒ STALE ⇒ re-derive**, rather than trusting the cached value.
`computed_acceptance` stops being a blindly-trusted snapshot — it is re-derived when stale, and
the Audit tab can show "stale — re-grade" instead of a wrong ✓met. A `report_linter` check flags
any persisted roll-up whose stamp is behind the tests it summarizes.

### 2.5 report_card axis + gates + acceptance = computed views

- `report_card/*.verdict.json` → a **render** of the graded tests' `axis` dimension (no
  independent grade; `_check_verdict_regrade` then checks the render against the graded tests).
- `pipeline_gate.gate_evaluator`, `executive.computed_acceptance` → thin views over
  `canonical_graded_outcomes` + a freshness stamp. Authored `gate_status` → annotation.

---

## 3. Migration — phased, each phase shippable and reviewable

- **Phase 1 (highest leverage, smallest diff):** add `canonical_graded_outcomes` and switch the
  three roll-ups to it. Closes the ✓met-vs-FAIL divergence immediately. No schema change.
  Recovery-sbml is the fixture: 1 recover / 2 refuted / 2 Accepted + the stale acceptance roll-up
  collapses to the computed truth.
- **Phase 2:** the `_grader` freshness stamp (version + graded_at + inputs_hash) on outcomes and
  roll-ups; a staleness check that forces re-derive; the `report_linter` stale-stamp rule.
- **Phase 3:** fold `axis`/`report_card` into the graded test as a render; make `computed_acceptance`
  a pure (stamped) view.
- **Phase 4:** schema — authored `outcomes[T].result` becomes an expectation field; divergence is
  a first-class Audit signal.

## 4. Division of work

- **discovery-engine (me):** `canonical_graded_outcomes` + the roll-up switch (Phase 1); the
  `_grader` stamp + staleness contract (Phase 2); fold `axis`/report_card (Phase 3). The #22
  store-less + `ws_root` fixes (PR #22 merged, PR #23 open) are prerequisites — they make the
  store-less recovery tests actually produce `computed_outcomes` to be authoritative over.
- **viva-sbml-decomposition (sbml-cycle):** migrate the recovery studies to the unified shape;
  drop the manual re-grade once computed is authoritative and grades natively.
- **vivarium-workbench (Audit tab):** render ONE list — what ran, graded by code/agent/authored,
  fresh vs stale (via the stamp), cycle-updated vs not — with divergence surfaced, not four
  disagreeing panels.

## 5. Open questions (for review)

1. **Author-graded tests:** record the author's result as `evaluated_by: authored` inside the one
   model (§2.1), or keep a visibly-separate "unverified" lane? (Proposed: the former, tagged.)
2. **`canonical_run` robustness:** the roll-ups crash today on mixed `str`/`float` run timestamps
   (`viva_workspace.outcomes.canonical_run`); coerce the sort key to `str` as part of Phase 1.
3. **`inputs_hash` scope:** test defs + run id + evaluator-registry fingerprint — enough, or also
   the composite/model version?
4. **Where acceptance lives:** keep `computed_acceptance` persisted-but-stamped, or make it a pure
   on-read view (no persistence)? Persisted+stamped is friendlier to the published static bundle.
