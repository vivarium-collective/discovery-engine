---
name: acquire_model
summary: Author a ModelAcquisitionProposal when the engine surfaces a needs_author model mechanism.
proposal_type: ModelAcquisitionProposal
verb: discover acquire_model
---

# acquire_model

Propose the **mechanism or model to acquire** when the frontier surfaces a `model_revision`
candidate change `{kind: mechanism, needs_author: true, question}` — a mechanism a human or
AI must author. The engine surfaces *where* a mechanism is needed; you author *what* it is.
The engine validates and records your proposal; it does not build the model.

## Trigger

A `model_revision` change with `needs_author: true` and a `question`. If none is surfaced,
do not invent a model.

## What to produce

A YAML/JSON `ModelAcquisitionProposal` with:

- `mechanism` — the proposed **cause or capability** the model must add, not a restatement
  of the question.
- `existing_model_ref` — the model being extended, if any.
- `change` — the structural change (`{kind: mechanism, question: ...}`).
- `expected_behavioral_test` — **required**: `{behavior_test: <name>, pass_if: <band>}`,
  what the acquired model must satisfy. This makes the proposal falsifiable.
- `assumptions` — what must hold for the mechanism to apply.
- `provenance` — `{trigger: "needs_author", source: "<question/candidate id>", by: "agent"}`.

## Record it

```
discover acquire_model <investigation> --from proposal.yaml
```

The engine validates the contract (mechanism, expected_behavioral_test, provenance.trigger)
and records it as `status: proposed` in `model_acquisition_proposals[]`. A human or reviewer
decides whether it is accepted and built.

## Hard rules

- **Do not fabricate evidence.** A proposal is a question, not a result. Never mark anything
  `verified`.
- **Do not smuggle in un-provided mechanisms or parameters.** Anything not provided by the
  expert goes in `assumptions`, explicit for review.
- **Every proposal must be falsifiable.** No `expected_behavioral_test`, no proposal.
