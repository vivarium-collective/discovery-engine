# ADR-0001 — Knowledge authority

**Status:** proposed (needs maintainer approval)

**Context:** WP0 / PR1 of the knowledge-layer plan. The plan introduces shared, versioned
Claim / Evidence / Assessment records. Before any shared write, the ecosystem must have **one
logical authority** for committed knowledge, so that a write either is or is not an epistemic
commitment. The rev-2 recommendation put it sharply: *"a successful cache write is not an
epistemic commitment."* This ADR decides the authority; it writes no code.

## The stores today

| Store | What it is | Authoritative for |
|---|---|---|
| `persist_cycle` → `investigation.yaml` (`knowledge` / `argument` / `cycle_state`) | a **regenerable projection cache** (derive-first: throw away and recompute) | nothing — it is a view |
| `events.jsonl` (investigation-contracts) | an **append-only event log**, emit-after-commit; the only true immutable store today | the record of what happened |
| `runs.db` | recorded measurements + execution provenance | measurements |
| `study.yaml` / `investigation.yaml` | authored executable specification + inquiry organization + pinned references | intent / organization |

The ambiguity the ADR resolves: today the derive-first cache, the YAML records, and the event
log coexist with no single answer to *"what is the committed knowledge?"*

## Decision

**Versioned, content-addressed knowledge records are the authoritative committed state;
`events.jsonl` is their append-only transaction journal; projection caches stay disposable.**

- A knowledge transaction — `{transaction_id, base_revision, operation, patch, evidence_refs,
  assessment / authorization, status}` — **appends to `events.jsonl` first** (the journal),
  then materializes or updates the versioned knowledge record.
- **Replay / recovery** reconstructs the versioned records from the journal; an interrupted
  commit recovers without duplicate changes (idempotent by `transaction_id`).
- The `persist_cycle` cache is **never** the authority — a cache write is not a commitment; it
  is recomputed from the records + run outcomes (derive-first stays intact).
- `runs.db` remains authoritative for measurements; knowledge records **reference** runs, never
  copy their numbers.

So there is **one logical authority** (the versioned records), with the journal as its
write-ahead log and the cache as a disposable view.

## Consequences

- `KnowledgeStep` wires at the **journal-append (authority) boundary**, not around the
  projection cache (WP4). The read-only composite wiring already landed (workbench #1799) is an
  interim *view*, not the commit path.
- The transaction is **validated before the append** — types, context, revision pins, evidence
  provenance, permissions; conflicting or stale writes are rejected; retries are idempotent;
  interruption recovery is tested.
- The migration adapter lifts existing findings / claims / verdicts into independently
  identifiable records **without rewriting studies**; **dual-projection parity** on genesis +
  growth fixtures must hold before any legacy reader is removed. Read-only projections +
  additive reference fields may precede the commit path; **shared writes may not**.
- **Do not deduplicate by text** — reconciling two claims to one identity is a separate,
  reviewed operation, not a side effect of a write.

## Open decisions for the maintainer

1. **Where the versioned records live** — a dedicated append-only store (e.g. `.pbg/knowledge/`)
   vs a committed `knowledge_records` block inside `investigation.yaml`.
   *Recommendation: a dedicated store, so the authority is not entangled with the authored intent
   and the derive-first cache in one mutable file.*
2. **Replay scope** — is `events.jsonl` sufficient to reconstruct all committed knowledge, or
   does recovery also need a periodic record snapshot?
   *Recommendation: journal + periodic snapshot, for bounded replay.*
3. **Authorization** — which actors (`human` / `agentic` / `computational`) may commit which
   operations, and where that policy lives (extend `AutonomyPolicy`).

## Alternatives considered

- **`events.jsonl` as the sole authority** (reconstruct all knowledge by replay). Rejected: a log
  is a journal, not a queryable authoritative state; every reader would have to replay.
- **YAML records as the authority** (status quo, knowledge derived). Rejected: it entangles
  authored intent, the derive-first cache, and committed knowledge in one mutable file — exactly
  the ambiguity this ADR exists to remove.

## Relates to
- `docs/knowledge-layer-reconciliation.md` (WP0 fold-in) · `docs/roadmap.md` (Stage 7 + the
  cross-cutting *immutable versioned dependencies* item) · `docs/upgrade-plan-0.6-reconciliation.md`.
