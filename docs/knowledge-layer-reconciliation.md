# Knowledge-Layer Plan — Reconciliation against the roadmap

**Baseline:** discovery-engine `main` — the cycle map (`discovery-engine-cycle-map.pdf`) and
[`roadmap.md`](roadmap.md) (the eight-stage scientific cycle, FR/AC-driven), plus the
content-addressed object model already shipped and wired (`ModelVersion`, the versioned
evidence snapshot, one `AutonomyPolicy`, `Discovery`/`FindingCandidate`).

**Source reviewed:** the *Knowledge-Layer Update Plan* — the knowledge-graph-centric
reformulation of the Formal Architecture, grounded against `main` and a profile of five real
workspaces.

**Why this doc:** the reformulation and the engine roadmap read as if they were one program.
They are two axes. This reconciles them stage by stage, marks what to **fold in** vs. **build**
vs. **defer**, and records the profiling finding that resets the cross-investigation premise.
Same discipline as [`upgrade-plan-0.6-reconciliation.md`](upgrade-plan-0.6-reconciliation.md):
anchored in the code and the data, not the prose.

---

## TL;DR

The knowledge-layer plan is **complementary, not competing**.

- The **roadmap deepens the discovery *cycle*** — the generative-science rigor *within* an
  investigation (hypothesis generation, EIG experiment design, model composition/revision,
  figure critique, Bayesian evidence, four-axis closure, the `DiscoveryAgent` coordinator).
- The **plan restructures the *object model / substrate*** — claims/evidence as first-class
  typed objects, studies that *reference* rather than *own* them, a knowledge-graph layer, a
  relation registry, narrative-as-projection.

They **converge at the evidence/claim object model**: a large part of the plan's WP0/WP2 *is*
the roadmap's Stage-7 typed evidence + the cross-cutting versioned-dependencies item, so it
must be built as **one effort, not two**.

A profile of five real workspaces (viva-genesis, discovery-showcase, v2ecoli, sms-ecoli,
viva-showcase) found **0 cross-investigation shared claims** — so the plan's cross-investigation
KG is a **forward, constructed** capability, outside the engine's within-investigation roadmap.
(An earlier pass reported ~117/59 "shared" claims; that was a flat-loading bug, since fixed.
viva-genesis is the reference structure: 43 `builds-on` + the full relation vocabulary, 23
populated `ModelVersion`s, mostly-assessed verdicts.)

## Fold in — the same work from two framings (do not duplicate)

| Knowledge-layer plan item | Roadmap home |
|---|---|
| **WP0** — Claim/Evidence as first-class typed objects + stable IDs / revisions | Stage 7 **typed `EvidenceRecord` + uncertainty / counterevidence** (⬜ DE-11) and the cross-cutting **immutable versioned dependencies** (⬜); extends the shipped content-addressed `ModelVersion` |
| **WP2** — a study's verdict routes as evidence *about* a referenced claim (`EvidenceStep`) | Stage 7 interpret / validate; builds on `evidence_integrity` (◑) and the post-sim `TestStep` / `ReportCardStep` that already emit the verdict |
| Claims-with-verdicts as first-class | ✅ `Discovery` / `FindingCandidate` (#54) — reuse, do not re-mint |
| Uncertainty / qualification on claims | ◑ cross-cutting "uncertainty + assumptions + counterevidence fields on claims" |

## Net-new — the roadmap lacks these (worth adding)

- **Relation registry + drift guard** — the roadmap never addresses the hyphen/underscore +
  legacy relation-vocabulary fragmentation (`builds-on` vs `builds_on`, etc.). Additive,
  low-cost, stops drift.
- **`knowledge_inputs` / reference-not-own** — studies *referencing* the claims they test is a
  structural decoupling the roadmap does not propose.
- **Narrative Calculus as a projection algebra** — the engine has `narrate()` (a derived ordered
  reading over the argument graph); the plan's Abstract / Compose / Refine / Restrict projection
  operators are new.

## Forward / out of scope — construct, do not claim

- **A knowledge graph spanning investigations + cross-investigation claim sharing.** The roadmap
  is entirely within-investigation; the profile shows no existing sharing. This is a *constructed*
  demonstration (the M1 growth pack), not a near-term fix for existing duplication, and should be
  labelled as such — the paper's "cumulative inquiry" rung stays an honest target.

## Do not rebuild (the 0.6 lesson, applied again)

Reuse the shipped, wired subsystems the roadmap and the 0.6 reconciliation already confirm:
content-addressed `ModelVersion`; the versioned evidence snapshot (grader/resolver versions +
`inputs_hash`); the single `AutonomyPolicy` + per-op `decision_record`; `Discovery` /
`FindingCandidate`; the typed `ExperimentDesign`. The plan's identity and evidence work
**extends** these — it must not re-mint them.

## Sequencing against the roadmap

1. **WP0 as a roadmap fold-in** — the typed Claim/Evidence object model + identity, landed as the
   Stage-7 typed evidence + versioned-dependencies item (one effort). Ship the relation registry
   alongside (additive).
2. **The within-investigation `EvidenceStep` seam** (WP2) — on top of `TestStep` + `evidence_integrity`.
3. **KG-as-layer + reference-not-own** — the structural decoupling, after the object model is typed.
4. **Cross-investigation sharing + Narrative Calculus** — forward, with a constructed demo; not
   claimed from current data.

The engine gaps the roadmap prioritizes (hypothesis generator, EIG experiment design, model
composition, figure critique, the `DiscoveryAgent` coordinator) are **orthogonal** to the
object-model work and proceed in parallel. The knowledge-layer plan neither blocks nor depends on
them — except that a **typed `EvidenceRecord` is the one shared seam** where the two programs meet.
