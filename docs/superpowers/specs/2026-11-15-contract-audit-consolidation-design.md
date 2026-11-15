# Contract & Audit Consolidation — Design

**Status:** Draft for review · **Extends:** RFC-0007 (the validator layer) ·
**Scope:** full stack — discovery-engine, bigraph-schema, process-bigraph, vivarium-workbench

## Why

"Contract" and "audit" each mean several different things across the stack, with
**two independent result vocabularies** and a dozen bespoke check surfaces that
grew before anyone unified them. The result: the same question ("does this
artifact meet what it promised?") is answered by unrelated functions returning
incompatible shapes, a process-side `Finding`/`AuditReport` that the study side
can't compose with, and a large dormant/dead apparatus (runtime contract
enforcement, hole-matching) that nothing adopted.

RFC-0007 already defines the target: **one `validation_record` + a `(type, scope)`
registry of pure validators**, with the execution-status / scientific-judgment
split ("a passing computational check ≠ biological truth"). This design finishes
that migration and extends it across the process framework and the workbench, so
there is **one** audit concept end to end.

## The consolidated concept

> A **contract** is what an artifact *promises* — a declared interface/obligation.
> It exists at several scopes and is just data (no behavior).
> A **validator** is a pure check `V(type, scope, target, evidence) → [record]`
> that reads an artifact (and, when relevant, its contract) and emits
> `validation_record`s. It never mutates.
> An **audit** is "run the registered validators for a scope and collect the
> records." There is no other audit type.

Contracts (the promise half) stay as distinct, scope-appropriate declarations:

| Contract | Scope | Lives in | Keep? |
|---|---|---|---|
| `ProcessContract` (typed face + units/bounds + conditions) | component | bigraph-schema `contract.py` | **keep** (22 adopters declare a face) |
| study `contract.face` | study | study.yaml schema | **keep** |
| L0–L5 reproducibility contract | workspace | `study/audit.py` (the *obligations*, not the checker) | **keep as data** |

The *checking* of all of them unifies onto RFC-0007.

## Goals / non-goals

**Goals**
- One `validation_record` shape and one registry as the single audit surface.
- Every current check is a registered `(type, scope)` validator, or is retired.
- The process-side contract audit (`bigraph_schema.contract_audit` kernel) is
  reachable *through* a registered validator, not a parallel vocabulary.
- The workbench *reads* validation-records; it stops running its own parallel
  contract catalog / composite audit / badges.
- The dead/dormant Layer-B apparatus is removed.

**Non-goals / anti-goals**
- **Not** forcing contract adoption. Coverage is a ratchet; validators degrade
  gracefully on an undeclared/prose-only contract (report low completeness,
  never error). Authoring rich contracts is separate, incremental work.
- **Never collapse epistemics.** An interface/unit/structural check passing must
  leave `scientific_judgment` null; no validator may mechanically upgrade a
  verdict (enforced by `epistemic_separation_violations`).
- **Never fabricate a disagreement** (e.g. a unit mismatch on a port that
  declares no units → `ok`, not a finding).
- Not a rewrite of RFC-0007 — it is the substrate; this extends it.

## Constraints

- **Additive + version-tolerant.** Wiring must work on the current runtime and
  strengthen as contracts gain faces/units. (Mirrors the 2026-10-19 unification
  plan's constraint.)
- **Pure, derive-first validators.** `set-verdict` / `resolve` / `relate` remain
  the only writers.
- **One record, two statuses.** `execution_status` always; `scientific_judgment`
  only for `empirical`/`logical`/`scientific`/`semantic`/`narrative` types.

## As-is inventory (what we are consolidating)

### Contract declarations (the promise half — kept)
- `bigraph_schema.contract.ProcessContract` — component face + conditions.
  `resolve_contract` runs on every edge; `from_description` parses a contract
  from the docstring as a fallback. **22 non-test files declare a class-level
  `contract = ProcessContract(...)`** (v2ecoli 21, sms-ecoli 6, viva-cpm 6,
  viva-genesis 3, 3d-ecoli, mosaic-compose). Only viva-genesis declares real
  pre/post `conditions` (`narrow_condition`).
- study `contract.face` (+ `conditions`) in study.yaml.
- L0–L5 obligations (`study/audit.py`).

### Audit surfaces (the sprawl — consolidate or retire)

**Already RFC-0007 validators (11 — the model works):**
`empirical.claim.band_provenance`, `logical.claim.provenance`,
`logical.relationship.edge_integrity`, `narrative.narrative.coherence`,
`scientific.study.rigor`, `structural.investigation.{hygiene, members,
narrative_completeness}`, `structural.study.{deprecated_fields,
narrative_completeness}`, `visual.figure.freshness`.

**Discovery-engine, still bespoke (migrate → validators):**
- `study/contract.py` — `study_face_findings` (reuses the bigraph-schema kernel
  `audit_face`) + `contract_face_diff`. NOT yet a registered validator.
- `study/audit.py` — L0–L5 reproducibility audit with its **own** `AuditReport`.
- `phases/evidence_integrity.py` — evidence/run reconciliation.
- `reporting/linter/checks_{completeness, findings, render, report, rigor,
  structure}` + `investigation_checks` — the report linter.
- `study/observable_lint.py`, `readout_resolver`/`readout_migration`,
  `reporting/validation.py`'s own un-adapted helpers.

**Process framework (separate `Finding` vocabulary):**
- `bigraph_schema.contract_audit` — `audit_contract`/`audit_face`/`completeness`/
  `Finding`/`AuditReport`. **The kernel the study spine already reuses.** Keep
  the kernel; reach it through a validator.
- `process_bigraph.composite_audit` (`audit_composite`) — **dead** (no consumer;
  the workbench has its own).
- `process_bigraph.contract_drift` — **dead** (test-only).
- `process_bigraph.audit_contracts` CLI gate — runs only in process-bigraph's own
  CI; no downstream consumer.
- `process_bigraph.contract_strict` + `bigraph_schema.contract_expr.evaluate` —
  runtime enforcement, wired into `composite.py` but `off` by default with **zero
  adopters** → dormant.
- `bigraph_schema.matching.find_candidates` + `subsumption` + `holes` report +
  `draft_process.draft_to_contract` — hole-matching; **effectively dead**.

**Workbench (parallel stack):**
- `lib/contract_catalog.py` (wraps `contract_audit`), `lib/composite_audit.py`
  (own wiring audit), `readout_validation.py`, `run_readiness`, `gate_evaluator`,
  per-process/composite contract badges + `§ Contract` panels,
  `GET /api/composite-contract-audit`, and a **dead** find-candidates chain
  (panel with no mount point, endpoint reachable only by direct HTTP).

## Target architecture

**RFC-0007's `validation_record` + `(type, scope)` registry is the single audit
surface.** The `coverage_matrix()` (8 types × 7 scopes) is the map of what the
system can check; every audit is `run_validators(ctx)` filtered by scope.

Key additions:

1. **A `component` scope is first-class.** The process/composite contract audit
   becomes component-scope validators, e.g.
   `structural.component.contract_completeness` (typed-port + predicate
   completeness, from the bigraph-schema kernel) and
   `semantic.component.wiring` (composite shared-store type/unit agreement).
   These wrap the *kernel* (`audit_face`/`completeness` + `core.resolve_schemas`/
   `_compute_unit_scale`) and emit `validation_record`s — one vocabulary.
2. **`study_contract` becomes `semantic.study.contract_face`** — `study/contract.py`'s
   `study_face_findings`/`contract_face_diff` register as a validator (it already
   uses the kernel; this just gives it a record + a registry slot).
3. **The L0–L5 audit and the report linter checks become validators** under the
   appropriate `(type, scope)` — `structural.*`, `semantic.*`, `logical.*` — and
   their bespoke `AuditReport`/`Finding`/lint-result shapes collapse into
   `validation_record`. A thin adapter maps the legacy shapes during transition.
4. **One Finding→record adapter bridges the process kernel.** `bigraph_schema`
   `Finding` is translated to a `validation_record` at the component-scope
   validator boundary; downstream everything speaks records.
5. **The workbench reads records.** The badges / `§ Contract` panel / composite
   audit endpoint are re-pointed at `run_validators(scope="component")` (served by
   the discovery-engine), replacing `contract_catalog` + the workbench's own
   `composite_audit`. The workbench stops owning a parallel audit.

## Migration map (each concept → target)

| Current surface | Target |
|---|---|
| `bigraph_schema.contract_audit` kernel | **keep**; wrapped by `structural.component.contract_completeness` validator |
| workbench `lib/contract_catalog.py` | **retire**; badges/panels read `run_validators(component)` |
| workbench `lib/composite_audit.py` | **fold** into `semantic.component.wiring` validator |
| `study/contract.py` face audit | **register** as `semantic.study.contract_face` |
| `study/audit.py` L0–L5 + own `AuditReport` | **migrate** checks to `structural/semantic` validators; `AuditReport`→records |
| `reporting/linter/checks_*` | **migrate** each to a validator; linter becomes `run_validators` + render |
| `phases/evidence_integrity.py` | **register** as `empirical.claim.evidence_integrity` (or keep as the cycle's first move, emitting records) |
| `study/observable_lint`, readout validation | **migrate** to `structural/semantic.study` validators |
| `process_bigraph.composite_audit` | **delete** (dead) |
| `process_bigraph.contract_drift` | **delete** (dead) |
| `process_bigraph.audit_contracts` CLI | **delete** or re-express as `run_validators(component)` over a registry; its CI step drops |
| `process_bigraph.contract_strict` + `contract_expr.evaluate` | **delete** (dormant, zero adopters) |
| `find_candidates` + `matching` + `subsumption` + `holes` report + `draft_to_contract` | **delete** (effectively dead) |
| workbench find-candidates chain (panel/endpoint/routing/CSS/tests) | **delete** (dead UI) |
| `ProcessContract` conditions (`narrow_condition`/`amend`) | **keep** iff the component validator consumes pre/post predicates (viva-genesis); else retire with `contract_strict` |

## Per-repo change plan (phased, additive)

**Phase 1 — discovery-engine: complete the internal migration.**
Register the still-bespoke DE checks (study_contract, L0–L5, linter checks,
evidence_integrity, observable/readout validation) as validators; collapse their
report shapes into `validation_record` behind adapters; keep the legacy callables
as thin wrappers during transition. One repo, no cross-repo risk.

**Phase 2 — bridge the process kernel.**
Add `component`-scope validators in the discovery-engine that wrap
`bigraph_schema.contract_audit` (completeness) and the composite wiring check,
translating `Finding`→record. Nothing in bigraph-schema changes yet.

**Phase 3 — retire dead Layer-B.**
Delete `process_bigraph.{composite_audit, contract_drift, audit_contracts,
contract_strict}` + the `composite.py` strict hooks, `bigraph_schema.{matching,
subsumption, contract_expr.evaluate}` + `holes` report + `draft_to_contract`.
Blast radius is small (all dead/dormant); update docs (README,
designing-with-holes.md). `bigraph_schema.contract_audit` + `ProcessContract`
declaration layer stay.

**Phase 4 — workbench: read, don't run.**
Re-point the per-process/composite contract badges + `§ Contract` panel +
`/api/composite-contract-audit` at the discovery-engine component-scope
validators; delete `lib/contract_catalog.py`, the workbench `lib/composite_audit.py`,
and the dead find-candidates chain (`walkthrough.js` panel, `env_worker` shim,
`app.py` route, routing entry, CSS, JS/py tests). (Full strip map in the survey.)

## Invariants (enforced)
- `epistemic_separation_violations` — execution-only types never assert judgment.
- Validators are pure; only `set-verdict`/`resolve`/`relate` write.
- Undeclared/prose-only contract → low-completeness record, never an error, never
  a fabricated mismatch.
- Coverage ratchet: no gate floor raised above current coverage.

## Open questions
- **Where do component validators live?** In the discovery-engine (consumes the
  bigraph-schema kernel, like `study/contract.py` does today) vs. a thin shared
  package. Proposed: discovery-engine, mirroring the existing study-spine pattern.
- **Keep `conditions` (pre/post predicates)?** Only viva-genesis declares them.
  Keep iff a component validator reads them; otherwise they retire with
  `contract_strict`.
- **Does the workbench call the discovery-engine for component audits**, or does
  the DE expose a pure function the workbench imports? (The workbench already
  depends on `viva-discovery-engine`.)
- **L0–L5 `--gate` CLI** — preserve as `run_validators(scope) + gate`, or keep the
  standalone entrypoint wrapping the registry?
