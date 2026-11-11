# Engine-native skills

These are **agent-facing contracts**, not engine code. Each `SKILL.md` tells an agent how
to produce a *typed proposal* that the deterministic engine then validates and records.

The division of labor is the engine's guiding principle:

- **The agent proposes science** — the creative, judgement-laden content (a mechanism, a
  discriminating design, a critique). It follows the `SKILL.md` and writes a proposal.
- **Deterministic engine code validates and records it** — the typed contract, the gate,
  the provenance. The engine never authors the content and never promotes a proposal to a
  fact on its own.

A skill is wired to a `discover` verb that surfaces the trigger (where the proposal is
needed) and validates + records what the agent writes. The engine owns the schema in
`discovery_engine/schemas/`; the agent owns what goes in it.

| Skill | Trigger (engine surfaces) | Proposal type | `discover` verb |
|---|---|---|---|
| `hypothesize` | a measured surprise the live hypotheses did not predict | `HypothesisProposal` | `discover hypothesize` |
| `critique` | a verdict/confidence that outruns its evidence | `CritiqueReport` | `discover critique` |

**Hard rules for every skill.** Never mark evidence `verified`, never promote a proposal to
a Finding/Discovery, never introduce a mechanism, parameter, or literature value that was
not provided — record it as a proposal with provenance and let a human/review accept it.
