# 0.6 Upgrade Plan — Reconciliation against the code

**Baseline:** discovery-engine `main` @ `b3b97ca` (0.6.0 + cycle-map source), plus the
P0 snapshot-gate fix in PR #61.
**Source reviewed:** *Discovery Engine Revised Upgrade Plan — 0.6.0 Baseline* (Revision 2.0,
11 Nov 2026, 19 pp).
**Why this doc:** the plan is written as if the agentic/scientific-reasoning loop is largely
greenfield, and it explicitly asks that *"the first engineering task should confirm each
reported path by running the relevant commands against preserved investigation fixtures…
Gaps classified 'not documented' should be checked against the repository before writing
replacement modules."* This is that check. Four independent code audits traced the plan's
claims and backlog tickets (DE-01…DE-13) against the actual package, with `file:line`
evidence and a wiring check (is a capability invoked by the cycle/CLI, or present-but-orphaned?).

Every verdict below is anchored in code. "Wired" means reached by `discover/cycle.py`,
`discover/actions.py`, or `discover/cli.py` in a production path (not only tests).

---

## TL;DR

The plan's framing is **substantially overstated** for the evidence, evaluation, autonomy,
model-revision and headless-CLI layers, and **roughly accurate** for the
generation / critique / coordinator layers. Building the backlog as written would **rebuild
~several working subsystems.**

**Already built and wired (do not rebuild):**
- Deterministic derive-first grading + versioned evidence snapshot with provenance
  (grader/resolver versions, `inputs_hash`); the corrected stale-gate invariant.
- One `AutonomyPolicy` gating simulation / render / authoring / reopen, with a per-op
  `decision_record`.
- Typed `ModelRevisionProposal` (≈ the plan's `ModelChangeProposal`) with a discriminating
  test, wired through the cycle and its apply path.
- Content-addressed, parent-linked `ModelVersion` identity + reuse/wrap/build routing.
- Headless `discover` verbs incl. `report`, `lint`, `populate-observations`, `linkage`.
- A typed `ExperimentDesign` artifact (pre-registered predictions, pre-committed decision rule).

**The real gaps (worth building), narrower than the plan implies:**
1. **No `DiscoveryAgent` coordinator** (§9 / DE-07) — the genuine central gap. Orchestration
   today is deterministic functions in `cycle.py`; AI enters only via an injected hook.
2. **Discovery is an automatic synonym for a verified verdict flip** (§11 / DE-03) — the exact
   pattern the plan warns against. No `FindingCandidate` / reviewed novelty assessment.
3. **No hypothesis *generator*** (§8) and **no critique capability** (§7 `CritiqueReport`, §11
   `DiscrepancyRecord`) — hypotheses are author-supplied; refutation routing is a 2-way
   heuristic, not a competing-cause diagnosis.
4. **Evidence is an untyped, simulation-only ledger** (§7 `EvidenceRecord`, §13 / DE-11) — no
   typed record, no uncertainty field, no distinct validation for lab / external / literature.
5. **`ExperimentDesign` is CLI-only, not in the autonomous cycle**; its child-study
   materializer is orphaned (§10 / DE-09).
6. **No benchmark harness** (§16 / DE-13).

**Do not prioritize:** the §4 repackage (`core/ skills/ agents/ …`) is mostly a directory
rename of working code; `schemas/` and `reporting/` already exist and orchestration is a
coherent (if large) `discover/cycle.py`.

**Correctness P0 (Wave 0) is done:** DE-01/DE-02 landed as PR #61 (snapshot-comparison truth
table + the gate fix). See also the [map correction](#map-correction-needed) below.

---

## Capability reconciliation

Legend: **BUILT** (real + wired) · **PARTIAL** (exists, incomplete or unwired) · **MISSING**.

### Evidence, grading, snapshot (plan §1, §5, §6, §7)

| Plan claim | Verdict | Evidence | Note |
|---|---|---|---|
| Derive-first projection + deterministic grading | BUILT | `phases/evaluation.py:62` `evaluate_claim_support`; `phases/evidence_integrity.py:156` `classify_finding_evidence`; `study/evaluator/outcomes.py:192` `compute_outcomes`; wired `cycle.py:143,291` | Pure; only impurity `computed_at`, excluded from fingerprint (`evaluation.py:367`). |
| Grader/resolver version + `inputs_hash` freshness | BUILT | `study/evaluator/workspace_plugins.py:130` (`GRADER_VERSION`), `:136` (`RESOLVER_VERSION`); `evaluation.py:310` `evidence_inputs_hash`, `:383` stamps snapshot | `grade_freshness` keys on `inputs_hash` (correct — grade recompute, distinct from the Discovery gate). |
| Corrected stale-gate invariant (method drift re-baselines; new evidence surfaces) | BUILT | `evaluation.py:330-355` `snapshot_is_stale` (version-only); gate `cycle.py:386-391` | PR #61. `inputs_hash` kept as provenance, non-gating. |
| §7 typed `EvidenceRecord` {source, measure, conditions, uncertainty, hash, provenance} | PARTIAL | ad-hoc dict ledger row `evidence_integrity.py:156-164` | No typed record, no `uncertainty`, aggregate-only hash; `conditions` on the finding, not the row. |
| §13 multi-source evidence (lab / external / literature, distinct validation) | MISSING | VERIFIED assigned by one rule `evidence_integrity.py:194-218` (`from_test` + registered run + code-graded outcome) | Structurally simulation-only; no `source_type`, no per-source validators. |

### Hypotheses, design, discrepancy (plan §7, §8, §10, §11)

| Plan claim | Verdict | Evidence | Note |
|---|---|---|---|
| §8 `hypothesize` — generate ≥2 alternatives w/ predictions/assumptions/falsifiers | MISSING | `phases/hypotheses.py` is entirely a *consumer* (`score_support:213`, `discriminating_hypotheses:427`, `anomalies:578`) | Hypotheses are authored in `investigation.yaml`; `anomalies()` emits a `proposed_question` string, not a hypothesis. |
| §7 typed `HypothesisProposal` | MISSING | grep `HypothesisProposal` → 0 | No mechanism/assumptions/alternatives fields anywhere. |
| §7/§10 typed `ExperimentDesign` + predictions-before-run + decision rule | BUILT (artifact), PARTIAL (reach) | `phases/experiment_design.py:98` `design_discriminating_study` → full typed dict `:130-143`; `register_prediction:35`; `commit_decision_rule:60` (`precommitted`) | **CLI-only** (`cli.py:989` `discover design`); **not** called by the cycle. `materialize_design:147` (design→child study) is **orphaned** (zero callers). |
| §10 active selection — 6 discrimination patterns, scientific value | PARTIAL (1 of ~6) | disjoint-interval on a shared scalar: `experiment_design._discriminating`, `hypotheses.discriminating_hypotheses:460` | Missing time-course, spatial, population-phenotype, dose-response, uncertainty/ensemble/EIG. Value signal is explicitly heuristic, "not a calibrated EIG". |
| §7 `ModelChangeProposal` | BUILT (as `ModelRevisionProposal`) | `phases/model_revision.py:147` `propose_model_revisions`, record `:128-144` {inadequacy, affected_model_version, candidate_changes, discriminating_test}; wired `cycle.py:152`, apply `actions.py:440` | Strong match; `mechanism` changes are `needs_author:true` (never fabricated). |
| §11 discrepancy diagnosis (revise-model vs revise-hypothesis) | BUILT (binary only) | `phases/model_error.py:20-38` `diagnose_refutation`; wired `cycle.py:673-675` | A 2-way route keyed on `model_disagreement_map`; **not** the 6-cause taxonomy. |
| §11 typed `DiscrepancyRecord` (6 competing causes) + critic/designer loop | MISSING | no `discrepancy` type in package | No enumeration of structure/params/context/observation/numerical/hypothesis. |
| §7 `CritiqueReport` + §8 `critique` skill | MISSING | all `critique` hits are source comments citing an external review doc | No module emits alternative diagnoses / unsupported-claim lists. |

### Discovery, findings, novelty (plan §11)

| Plan claim | Verdict | Evidence | Note |
|---|---|---|---|
| Discovery = **reviewed** novelty/significance/validation, not an auto verdict-synonym | **CONTRADICTED** | `phases/discovery_record.py:61-106` builds a Discovery directly on a `supported`/`contested` flip on VERIFIED evidence; `novelty` is a hardcoded string `:72-74`; wired `cycle.py:378-399` | Exactly the pattern the plan wants replaced. No review gate, no significance assessment. |
| `EvidenceUpdate → FindingCandidate → Finding → Discovery` chain | PARTIAL / MISSING | only `EvidenceUpdate` exists (`model_error.py:20`); no `FindingCandidate`/`DiscoveryAssessment` | Verdict→Discovery is direct; no candidate/review stage. |

### Execution, autonomy, coordinator, CLI, layout (plan §1, §2, §4, §9, §13)

| Plan claim | Verdict | Evidence | Note |
|---|---|---|---|
| §2 ONE `AutonomyPolicy` across sim/render/authoring/review/build/reopen | BUILT | `discover/_policy.py:31`; `pol.allows()` sim `cycle.py:794`, render `:828`, authoring `:887`, reopen `:937`; `decision_record:1099` | One frozen policy, resolved once (`:786`). **Asymmetry:** `build_process` gates on a derived `autonomous` bool (`:1051`), bypassing `allowlist`/`approved_ids` — worth tightening. |
| §1 Growth execution — advance promotes/builds/runs/reopens | BUILT | `advance_cycle` `cycle.py:1509`, dispatch `:1575`; authoring `:883`, reopen `:931`, build `:1040` | Autonomous loop is real. |
| §1 "`select_for_launch` is now wired" | **PARTIAL — mis-stated** | `select_for_launch` is referenced only in comments/docstrings (`cycle.py:625`, `_policy.py:9`) and 3 test files; production authoring gate is `pol.allows(consequence="authoring")` `cycle.py:887` | The *equivalent gate* is wired; the *named function* is a tested-but-unused shim. See [map correction](#map-correction-needed). |
| §1/§10 headless verbs report/lint/observations/linkage | BUILT | `cli.py` `report:567`, `lint:577`, `populate-observations:588`, `linkage:607` (34 verbs total) | All present, engine-native. |
| §13 model adapter: proposal+design → reuse/wrap/build → execute → content-addressed ModelVersion | PARTIAL (by decomposition) | routing `model_architect.py:190` `choose_acquisition_route`; identity `model_version:250` (sha256 + `parent_ids`); exec via `build_hook` `cycle.py:1055` | Pieces exist but no single adapter stitches proposal+design→execute→mv; `model_version` is lineage identity, not a runner input. |
| §9 / DE-07 `DiscoveryAgent` bounded coordinator + replay | MISSING | no coordinator/orchestrator class; "agent" in-code = evaluator fallback bucket (`study/evaluator/common.py:48`); "replay" = run manifests (`study/run_registry.py:76`) | **The central gap.** |
| §4 repackage `core/ skills/ agents/ schemas/ adapters/ storage/ reporting/ workflows/` | MISSING (proposal) | actual: `discover/ growth/ phases/ projection/ investigation/ study/ reporting/ schemas/ feedback/ …`; no `skills/`, `agents/`, `SKILL.md`, or `workflows/discovery_cycle.py` | Mostly cosmetic; `schemas/`+`reporting/` already exist; orchestration is `discover/cycle.py`. |

---

## Corrected backlog (DE-01…DE-13 re-verdicted)

| Ticket | Plan priority | **Actual state** | Corrected action |
|---|---|---|---|
| DE-01 snapshot truth table | P0 | **DONE** (PR #61) | — |
| DE-02 evidence-hash comparison | P0 | **DONE** (PR #61) | — |
| DE-03 `FindingCandidate` + typed evidence-change events | P0 | **MISSING** | **High-value, do next.** Insert a reviewed stage between a verified flip and a Discovery; demote `detect_discoveries` to emit `FindingCandidate`; keep existing `discoveries[]` with migration metadata. |
| DE-04 pure normalization/grading/evaluation facade + golden tests | P1 | **PARTIAL** | Evaluator is modularized but `__init__` leaks private helpers for monkeypatch; add a sealed facade + golden contract tests. Medium value. |
| DE-05 versioned HypothesisProposal/ExperimentDesign/CritiqueReport schemas | P1 | **PARTIAL** | `ExperimentDesign` exists un-versioned in-code; version it. HypothesisProposal/CritiqueReport are net-new — build only when §8/§11 skills land (don't front-run). |
| DE-06 `skills/hypothesize` + `skills/critique` | P1 | **MISSING** | Real gap. No `skills/` infra at all. The hypothesize generator + critique are the scientific core. |
| DE-07 `DiscoveryAgent` (bounded runtime + replay) | P1 | **MISSING** | **The central architectural gap.** Build the bounded coordinator over the existing deterministic phases. |
| DE-08 experiment compiler → intervention/time/uncertainty contrasts | P1 | **PARTIAL (1 pattern)** | Extend `feedback/compiler.py` + `experiment_design.py` beyond disjoint-scalar to time-course and uncertainty contrasts. |
| DE-09 design skill + portfolio eval; replace unconditional child seeding | P1 | **PARTIAL** | `design_discriminating_study` exists (CLI-only); wire it into the cycle and replace idempotency-only seeding with policy/value gating. Wire the orphaned `materialize_design`. |
| DE-10 wire validated designs → ModelVersion/AutonomyPolicy/runner | P1 | **PARTIAL** | Policy↔runner is wired; stitch ExperimentDesign→ModelVersion→runner into one pipeline (mv is currently lineage-only). |
| DE-11 empirical EvidenceRecord adapters + provenance validators | P2 | **MISSING** | Type the ledger first (EvidenceRecord), then add per-source validators so lab/external/literature aren't VERIFIED by the simulation rule. |
| DE-12 research/analyze/synthesize skills + narrative explanatory-gap feedback | P2 | **MISSING (skills) / PARTIAL (`narrative_gap`)** | `narrative_gap` is a live, wired frontier kind (`projection/engine.py:41`); the skills are net-new. |
| DE-13 benchmark full vs 0.6.0 + ablations | P1 | **MISSING** | No harness. Needed to justify the whole effort; build alongside the first vertical slice (§16). |

---

## Recommendations

1. **Wave 0 is done** — merge PR #61; nothing else in the correctness wave remains.
2. **Next two units, both bounded and high-leverage, both extend existing code:**
   - **DE-03** — Finding/Discovery separation (make Discovery a reviewed assessment). Directly
     continues the snapshot-gate work and fixes a live correctness-of-meaning issue.
   - **DE-07** — the `DiscoveryAgent` coordinator, built *over* the existing wired phases
     (evaluation, model_revision, experiment_design), not as a parallel state store.
3. **Then the scientific skills (DE-06):** the `hypothesize` generator and `critique` — the one
   genuinely greenfield capability the plan is right about. These pull DE-05's schemas into
   existence; don't build the schemas speculatively first.
4. **Wire what's already built (DE-09/DE-10):** `ExperimentDesign` and `materialize_design`
   exist but sit outside the cycle. Plumbing them in is cheap relative to their value.
5. **Defer the §4 repackage.** Add new modules behind existing `discover` verbs; don't relocate
   working code into aspirational folders.
6. **Tighten the one autonomy asymmetry:** route `build_process` through `pol.allows()` like the
   other consequential ops, so build honors `allowlist`/`approved_ids`.
7. **Build DE-13 with the first vertical slice (§16)**, so each new capability is measured against
   the 0.6.0 baseline rather than asserted.

---

## Map correction needed

The cycle map (`docs/discovery-engine-cycle-map.{tex,pdf}`, shipped in PR #60) states
`select_for_launch` is "now wired". Verified against code: `select_for_launch` is called only
in docstrings/comments and tests; the production authoring gate is
`pol.allows(consequence="authoring")` (`cycle.py:887`). The *AutonomyPolicy itself is wired*, so
the map's intent is right but the named mechanism is wrong. The "what mutates" / scorecard rows
that cite `select_for_launch` should instead cite the inline `AutonomyPolicy` authoring gate.
