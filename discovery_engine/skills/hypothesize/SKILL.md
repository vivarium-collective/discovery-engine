---
name: hypothesize
summary: Author a competing HypothesisProposal when the engine surfaces a measured surprise the live hypotheses did not predict.
proposal_type: HypothesisProposal
schema: discovery_engine/schemas/hypothesis-proposal.schema.json
verb: discover hypothesize
---

# hypothesize

Produce **≥2 plausible, competing explanations** for a surprise the current hypotheses did
not anticipate, each with an explicit, falsifiable prediction. The engine surfaces *where*
a hypothesis is needed; you author *what* it is. The engine validates and records your
proposal — it does not write it.

## Trigger

Run `discover hypothesize <investigation>`. It lists **unexplained anomalies** — a measured
value (`study#observable = observed`) that satisfies none of the live hypotheses' predicted
bands. Each listed trigger is a gap to answer with a proposal. If nothing is listed, the
live hypotheses already account for the evidence — do not invent a hypothesis.

## What to produce

For a trigger, author **two or more** `HypothesisProposal`s that make *different*,
*discriminating* predictions on the same observable (so a later experiment can tell them
apart). Each proposal is a YAML/JSON file with:

- `statement` — one sentence: the claim.
- `mechanism` — the proposed **cause**, not a restatement of the statement. Why would this
  produce the observation?
- `assumptions` — what must hold for the mechanism to apply.
- `predictions` — **≥1**, each `{observable, expected, falsifier}`:
  - `observable` — a measured quantity the workspace can produce.
  - `expected` — a band in the same grammar the evaluator reads: `{op: "<", value: X}`,
    `{op: ">=", value: X}`, or `{low: A, high: B}`.
  - `falsifier` — the concrete result that would **overturn** this hypothesis.
- `alternatives` — competing explanations you are setting this one against (at least the
  null / area-controlled case where relevant).
- `provenance` — `{trigger: "unexplained_anomaly", source: "<study#observable>", by: "agent"}`.

Make the competing proposals' `expected` bands **disjoint** on a shared observable where you
can — that is what makes a discriminating experiment possible downstream (`discover design`).

## Record it

```
discover hypothesize <investigation> --from proposal-1.yaml
discover hypothesize <investigation> --from proposal-2.yaml
```

The engine validates the contract (statement, mechanism, ≥1 falsifiable prediction,
provenance) and records each as `status: proposed`. A human or reviewer accepts one with
`discover hypothesize <investigation> --accept <hp-id>`, which promotes it into the authored
`hypotheses[]` the evaluator scores.

## Hard rules

- **Do not fabricate evidence.** A proposal is a question, not a result. Never set a verdict,
  never mark anything `verified`.
- **Do not smuggle in un-provided inputs.** If a mechanism or parameter you need was not
  provided by the expert, say so in `assumptions` — the proposal makes the assumption
  explicit for review; it does not sanction it.
- **Every prediction must be falsifiable.** No `falsifier`, not a hypothesis.
- **Propose competitors, not a single favored story.** The point is a discriminating set.
