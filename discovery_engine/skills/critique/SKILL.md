---
name: critique
summary: Adversarially review a result the evidence does not fully support, and record a CritiqueReport.
proposal_type: CritiqueReport
schema: discovery_engine/schemas/critique-report.schema.json
verb: discover critique
---

# critique

Be the skeptical reviewer. For a result that claims more than its evidence supports, produce
a **CritiqueReport**: the competing explanations that were not ruled out, the claims that
lack backing, and the concrete tests that would settle it. The engine surfaces *where* a
result is overclaimed; you author *why* it should not be accepted. The engine validates and
records your critique — it does not write it, and the critique never changes a verdict.

## Trigger

Run `discover critique <investigation>`. It lists results to challenge, from two canonical,
deterministic signals:

- **`verdict_divergence`** — the authored `gate_status` disagrees with the computed verdict
  (the claimed result outruns the evidence).
- **`confidence_divergence`** — the authored `confidence` badge disagrees with the derived
  confidence (reads as spin).

If nothing is listed, the authored verdicts and confidence match the evidence — do not
manufacture a critique.

## What to produce

A CritiqueReport (YAML/JSON) against the listed `target` study, with **at least one** of:

- `alternative_diagnoses` — competing explanations for the observation that the result did
  not exclude (e.g. "the gain is plain movement-to-resources, not sensing").
- `unsupported_claims` — each `{claim, why}`: a statement the evidence does not back and the
  reason (e.g. `gate_status=passed` with no verified run).
- `suggested_discriminations` — each `{observable, test}`: a concrete experiment or control
  that would settle which diagnosis is right (the natural input to `discover design`).

Plus `provenance: {trigger: "<verdict_divergence|confidence_divergence>", source: "<study>", by: "agent"}`.

A strong critique names a **discriminating next test**, not just an objection — give the
engine something actionable.

## Record it

```
discover critique <investigation> --from critique.yaml
```

The engine validates the contract (a target, provenance, and ≥1 non-empty section) and
records it as `status: open`. A human addresses or dismisses it:

```
discover critique <investigation> --address <cr-id>   # the critique was acted on
discover critique <investigation> --dismiss <cr-id>   # considered and set aside
```

## Hard rules

- **A critique is advisory.** It never sets or overturns a verdict and never marks evidence
  `verified`. It argues; a human decides.
- **No empty critiques.** At least one substantive section — an objection with nothing in it
  is noise.
- **Challenge the evidence, not the author.** Every point ties to a claim, an observation, or
  a missing control.
- **Prefer a discriminating test.** The most useful critique says how to settle the question.
