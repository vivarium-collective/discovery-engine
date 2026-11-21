# Discovery Engine glossary

The canonical public vocabulary for the Discovery Engine. Every surface — the
[interactive cycle map](cycle-map.html), the [PDF map](discovery-engine-cycle-map.pdf),
the CLI help, the `/viva:*` skills, and the manuscript — maps to the terms below.

The machine-readable source of truth is [`cycle-spec.json`](cycle-spec.json); the
interactive map renders from it and [`tests/test_cycle_spec_drift.py`](../tests/test_cycle_spec_drift.py)
fails when it drifts from the engine. **This is public-facing language. Serialized
schema fields (`study.yaml`, `investigation.yaml`), the Python API, and the A–F
execution phases are unchanged** — renaming those is a later, audited phase.

## The discovery cycle — six stages

A scientist-readable backbone, not an enforced linear itinerary (evaluation may
return directly to modeling; a new discrepancy may lead directly to hypothesis
generation).

| Stage | Philosophical | Meaning | Existing components |
|---|---|---|---|
| **Question** | Problematize | Identify anomalies, discrepancies, gaps, and unresolved phenomena. | `run_cycle`, `frontier`, `diagnose`, `load_cycle_inputs` |
| **Hypothesize** | Conjecture | Generate competing mechanistic explanations. | `generation_requests`, `subprocess_filler`, `apply_result` |
| **Model** | Formalize | Represent explanations so they yield explicit, testable consequences. | `build_catalog`, `bind_model_versions`, acquire_model |
| **Experiment** | Interrogate | Design and execute informative computational tests. | `plan_candidate_actions`, `design_discriminating_study`, `advance_cycle`, `compute_outcomes` |
| **Evaluate** | Evaluate | Compare predicted and observed; assess reliability and support. | `reconcile_evidence_state`, `compare_hypotheses`, `detect_finding_candidates` |
| **Synthesize** | Integrate | Revise the knowledge state, integrate evidence, expose new questions. | `review_findings`, `suggest_studies`, `refine_study`, `reopen` |

## The four engine services

Capabilities that operate *across* the six stages — infrastructure controlling the
cycle, not stages themselves. (Architectural labels, not a proposal to rename every
Python module.)

| Service | Was | Responsibilities |
|---|---|---|
| **Orchestrator** | Orchestration | Coordinates the cycle, dependencies, permission gates, execution (`run_cycle`, `execute_plan`, `advance_cycle`, `AutonomyPolicy`). |
| **Reasoner** | Automation | Scientific proposal generation and typed AI interaction (`generation_requests`, `subprocess_filler`, `apply_result`). Deterministic inference is also reasoning; it is **not** the LLM alone. |
| **Strategist** | Optimization | Ranks opportunities, discriminating studies, and costs by **heuristic** ranking (`opportunities`, `next_action`, `select_portfolio`) — not a demonstrably optimal or learned policy. |
| **Knowledge Base** | Memory | Preserves investigations, studies, model lineage, observations, candidate discoveries, decisions. |

## Scientific objects

The progression from a measured result to a reviewed contribution:

> **Observation** → **Finding** → **Discovery Candidate** → **Discovery**
> (recorded measurement) → (interpretation supported/challenged by evidence) → (proposed novel conclusion) → (reviewed, promoted knowledge)

Keep **evidence, interpretations, and conclusions distinct**: a successful program
run is not a successful hypothesis test, and a candidate finding is not automatically
a discovery. Proposed public names (current → public): `finding` → *Observation /
Finding*; `FindingCandidate` → *Discovery Candidate*; `CandidateAction` → *Inquiry
Action*; `EvidenceUpdate` → *Revision Signal*; `DiscoveryDelta` → *Knowledge Change*;
`frontier` → *Inquiry Frontier*; `closure` → *Completion Status*; `resolution` →
*Decision Record*. `Investigation`, `Study`, `Hypothesis`, `Model Version`,
`Experiment Design`, and `Discovery` keep their names. **No mechanical schema rename**
— these are display/conceptual names; a canonical field migration is a later phase.

## Two semantic separations

**Scientific vs operational frontier.** Both belong to the Inquiry Frontier, but
opportunity scores should distinguish *epistemic value* from *workflow urgency* — a
high-priority lint fix is not a scientifically informative experiment.
- *Scientific:* unresolved biological questions, model disagreement, missing mechanisms, untested explanations, unexplained observations.
- *Operational:* incomplete tests, stale validation, unreadable artifacts, absent visualizations, review decisions, report maintenance.

**Completion vs scientific progress.** `closure` is a workflow completion condition,
not a statement that a scientific question is resolved. Four independent dimensions:
Completion Status (obligations addressed?), Evidence Status (claims supported by
verified observations?), Discovery Progress (what understanding changed?), Publication
Readiness (reproducible and communicated?).

**Generation vs validation.** The outward-facing lifecycle is **Request → Generate →
Validate → Record**: the first two ask for and produce scientific content; the last
two enforce contract shape, authority, permissions, and provenance. Validation here
does **not** certify that a scientific explanation is true.
