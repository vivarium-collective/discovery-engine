# DiscoveryAgent Coordinator (DE-07) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a bounded `DiscoveryAgent` coordinator that, given an InvestigationState snapshot + typed opportunities, routes each generative act to its skill, captures the agent invocation replayably, validates the produced proposal, and returns a portfolio — so the scientific-reasoning skills (hypothesize, critique) run through one orchestrated loop instead of only one-off manual CLI verbs.

**Architecture:** A new `discovery_engine/agents/` package layered *over* the existing wired phases — it composes `generation_requests` (emission), per-kind proposal validators/recorders (`hypothesis_proposal`, `critique_report`, …), and `AutonomyPolicy`. It is an orchestrator, not a parallel state store: it never writes canonical evidence/verdicts, only routes → captures → validates → hands validated+authorized proposals to the existing `apply_result` writers. The engine stays LLM-free; generation happens across the `fill` seam (a `claude -p` skill behind `$VIVA_FILLER_CMD`), exactly as `run_requests` already does.

**Tech Stack:** Python 3.12, frozen dataclasses, ruamel.yaml (comment-preserving writes via existing `study.io`/`actions` helpers), jsonschema, pytest.

**Spec:** `docs/upgrade-plan-0.6-reconciliation.md` (DE-07, "the central architectural gap") + the Revised Upgrade Plan §9 ("One agent coordinator") and §8 (skill packaging rules). The reconciliation doc is the binding authority; the plan is its argument.

## Global Constraints

- **No AI attribution, ever.** No `Co-Authored-By: Claude` trailer; no "Generated with Claude Code" in commits/PR body/title.
- **Engine stays LLM-free.** The coordinator NEVER calls a model. Generation happens only across the injected `fill` seam (the existing `subprocess_filler`/`$VIVA_FILLER_CMD`). Every new engine function is pure except where it calls `fill` or writes YAML through an existing atomic writer.
- **Orchestrator, not a parallel state store.** The coordinator reads the projected state + opportunities and emits/validates proposals; it MUST NOT author scientific content, mark evidence `verified`, promote a proposal to a Finding/Discovery, or write a verdict. Canonical writes go only through the existing recorders (`record_hypothesis_proposal`, `record_critique_report`, …).
- **Reuse, don't rebuild.** Reuse `generation_requests`, `GenerationRequest`/`GenerationResult`, `run_requests`/`subprocess_filler`, `coerce_hypothesis_proposal`/`validate_hypothesis_proposal`, `coerce_critique_report`/`validate_critique_report`, `record_hypothesis_proposal`/`record_critique_report`, `AutonomyPolicy.allows`, `select_portfolio`. Do not reimplement any of them.
- **Don't relocate working code** (the §4 repackage is deferred). Only ADD the net-new `discovery_engine/agents/` package and small, additive hooks in `generation.py` / `generation_emit.py` / `generation_apply.py` / `cli.py`.
- **Additive, idempotent persistence.** New investigation.yaml block `agent_captures[]` is append-only, idempotent by content id, and never overwrites authored fields.
- Squash-merge to `main`; one bounded PR; CI green before merge; never auto-merge.

## Review Focus

- **A review-gated kind must never auto-apply.** `hypothesize`/`critique` default to `review`; the coordinator and `apply_result` must queue them unless the operator's `GateConfig` explicitly opts them to `auto`. Tested in Task 1 + Task 4.
- **A filler that raises / returns nothing / returns a malformed artifact must become a captured `error`/`queued` outcome, never a silent apply and never a crashed run.** Tested in Task 4.
- **An emitted `hypothesize` request must carry the trigger context the SKILL.md needs** (`study#observable`, observed value, competing predictions) — an empty context yields a proposal the agent can't ground. Tested in Task 1.
- **Capture must be replayable:** a `ProposalCapture` with a missing `filler.model`/`request_id`/`artifact` is incomplete; `record_captures` must reject it rather than persist a half-record. Tested in Task 5.
- **Routing an unknown kind is a no-op skip, not a KeyError.** The registry returns `None` for an unregistered kind and the coordinator skips it with a reason. Tested in Task 2.

---

### Task 1: `hypothesize` + `critique` generation kinds + emission routing

**Files:**
- Modify: `discovery_engine/phases/generation.py` (KINDS, `_DEFAULT_BY_KIND`)
- Modify: `discovery_engine/phases/generation_emit.py` (`_PRODUCES`, `_request_kind`, `_CONTEXT_KEYS`)
- Modify: `discovery_engine/schemas/generation-request.schema.json` (kind enum)
- Test: `tests/test_generation_emit.py`

**Interfaces:**
- Consumes: the opportunity dicts from `projection/engine.opportunities` — each `{id, source, action_type, kind, value, competing_predictions, question, …}` where `kind ∈ {anomaly, model_disagreement, revision_proposed, …}`.
- Produces: `generation_requests(...)` now emits `GenerationRequest(kind="hypothesize", …)` for raw-surprise opportunities and `kind="critique"` for over-reaching-verdict opportunities, each with `produces` and a populated `context`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_generation_emit.py
def test_anomaly_opportunity_emits_hypothesize_with_trigger_context():
    opp = {"id": "anomaly:s3:colo", "source": "s3:colo", "action_type": "revise_model",
           "kind": "anomaly", "question": "colo=0.9 outside all bands",
           "competing_predictions": None, "value": {"score": 0.5}}
    reqs = generation_requests([opp])
    assert [r.kind for r in reqs] == ["hypothesize"]
    r = reqs[0]
    assert r.produces == "hypothesis_proposal/v1"
    assert r.target == "s3:colo"
    assert r.gate == "review"                      # branch-point default
    assert r.context.get("kind") == "anomaly"      # trigger carried for the SKILL.md

def test_model_disagreement_opportunity_emits_hypothesize():
    opp = {"id": "model_disagreement:s2:k", "source": "s2:k", "action_type": "revise_model",
           "kind": "model_disagreement", "value": {"score": 0.4}}
    assert [r.kind for r in generation_requests([opp])] == ["hypothesize"]

def test_revision_proposed_still_emits_acquire_model():
    opp = {"id": "revision_proposed:rp1", "source": "rp1", "action_type": "revise_model",
           "kind": "revision_proposed", "value": {"score": 0.6}}
    assert [r.kind for r in generation_requests([opp])] == ["acquire_model"]

def test_overreaching_verdict_opportunity_emits_critique():
    opp = {"id": "verdict_overreach:claim:x", "source": "claim:x", "action_type": "synthesize",
           "kind": "verdict_overreach", "value": {"score": 0.3}}
    assert [r.kind for r in generation_requests([opp])] == ["critique"]
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_generation_emit.py -k "hypothesize or critique or revision_proposed" -v`
Expected: FAIL (`unknown GenerationRequest.kind 'hypothesize'`).

- [ ] **Step 3: Add the kinds + routing**

In `generation.py`: append `"hypothesize"`, `"critique"` to `KINDS`; add `"hypothesize": "review"`, `"critique": "review"` to `_DEFAULT_BY_KIND`.
In `generation-request.schema.json`: add both to the `kind` enum.
In `generation_emit.py`: add `"hypothesize": "hypothesis_proposal/v1"` and `"critique": "critique_report/v1"` to `_PRODUCES`; in `_request_kind(opp)` add, before the `_OP_TO_KIND` fallback:
```python
if opp.get("kind") in ("anomaly", "model_disagreement"):
    return "hypothesize"
if opp.get("kind") == "verdict_overreach":
    return "critique"
```
Add `"competing_predictions"`, `"observed"` to `_CONTEXT_KEYS` if not already carried (so the hypothesize context has the surprise).

> NOTE for implementer: `verdict_overreach` may not yet be an opportunity `kind` emitted by `projection/engine.py`. If a grep shows it is absent, keep the `critique` routing branch (it is correct and tested by a synthetic opp) but add a ledger note that wiring the *trigger* (a frontier kind for "verdict outruns evidence") is a follow-up; do NOT invent a new frontier kind in this task.

- [ ] **Step 4: Run to verify pass** — `pytest tests/test_generation_emit.py -v` → PASS.
- [ ] **Step 5: Commit** — `feat(generation): hypothesize + critique request kinds + emission routing`

---

### Task 2: `SkillRegistry` — kind → skill contract

**Files:**
- Create: `discovery_engine/agents/__init__.py`
- Create: `discovery_engine/agents/skill_registry.py`
- Test: `tests/test_skill_registry.py`

**Interfaces:**
- Produces: `Skill` (frozen dataclass) `{kind, name, proposal_type, skill_md, produces, consequence, coerce, record}` where `coerce(artifact)->dict` is the kind's validator (raises on bad) and `record(inv_path, artifact)->dict` is the kind's canonical writer. `default_registry() -> SkillRegistry`; `SkillRegistry.get(kind) -> Skill | None`; `SkillRegistry.kinds() -> tuple[str,...]`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_skill_registry.py
from discovery_engine.agents.skill_registry import default_registry

def test_registry_routes_hypothesize_to_its_validator_and_recorder():
    sk = default_registry().get("hypothesize")
    assert sk is not None
    assert sk.proposal_type == "HypothesisProposal"
    assert sk.skill_md.endswith("skills/hypothesize/SKILL.md")
    assert sk.consequence == "authoring"
    # coerce is the real honesty gate
    import pytest
    with pytest.raises(ValueError):
        sk.coerce({"statement": "x"})           # no mechanism/prediction → rejected

def test_registry_routes_critique():
    sk = default_registry().get("critique")
    assert sk is not None and sk.proposal_type == "CritiqueReport"

def test_unknown_kind_returns_none():
    assert default_registry().get("not_a_kind") is None
```

- [ ] **Step 2: Run to verify fail** — `pytest tests/test_skill_registry.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement `skill_registry.py`**

Define the `Skill` dataclass and `default_registry()` seeding two skills, wrapping the EXISTING functions (import inside the builder to avoid import cycles):
- `hypothesize` → `coerce=coerce_hypothesis_proposal`, `record=record_hypothesis_proposal`, `proposal_type="HypothesisProposal"`, `consequence="authoring"`, `skill_md="discovery_engine/skills/hypothesize/SKILL.md"`, `produces="hypothesis_proposal/v1"`.
- `critique` → `coerce=coerce_critique_report`, `record=record_critique_report`, `proposal_type="CritiqueReport"`, `consequence="authoring"`, `skill_md="discovery_engine/skills/critique/SKILL.md"`, `produces="critique_report/v1"`.
`SkillRegistry` is a thin frozen wrapper over `dict[str, Skill]` with `get`/`kinds`.

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** — `feat(agents): SkillRegistry routing kind -> validator/recorder`

---

### Task 3: Route `apply_result` through the registry (close the 2-of-N gap)

**Files:**
- Modify: `discovery_engine/phases/generation_apply.py` (`apply_result`)
- Test: `tests/test_generation_apply.py`

**Interfaces:**
- Consumes: `SkillRegistry` from Task 2; the existing `GenerationRequest`/`GenerationResult`.
- Produces: `apply_result` now applies `hypothesize`/`critique` by calling the registry Skill's `record(inv_path, artifact)`; unregistered/unwired kinds keep the existing `queued_for_review` behavior.

- [ ] **Step 1: Write the failing test** — build a tmp investigation.yaml, a `hypothesize` request + a valid `HypothesisProposal` artifact result, assert `apply_result` returns `{status: applied, applied_to: [".../hypothesis_proposals"]}` and the proposal is in the file; a malformed artifact → `ValueError`; an unregistered kind → `queued_for_review`.
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** — after the existing `resolve_decision`/`interpret_finding` branches, add a registry lookup: `sk = default_registry().get(kind)`; if present, `art = sk.coerce(result.artifact)` then `sk.record(_inv_path(ws_root, inv_slug), art)`, returning `applied`. Keep the final `queued_for_review` fallthrough.
- [ ] **Step 4: Run to verify pass** — include the existing apply tests (no regression on the 2 original kinds).
- [ ] **Step 5: Commit** — `feat(generation): apply_result routes hypothesize/critique via SkillRegistry`

---

### Task 4: `DiscoveryAgent.propose` + `ProposalCapture`

**Files:**
- Create: `discovery_engine/agents/capture.py` (`ProposalCapture`)
- Create: `discovery_engine/agents/discovery_agent.py` (`DiscoveryAgent`, `ProposalPortfolio`)
- Test: `tests/test_discovery_agent.py`

**Interfaces:**
- Consumes: opportunities (Task 1 emission), `SkillRegistry` (Task 2), an injected `fill(request) -> GenerationResult | None` (the existing seam; a stub in tests).
- Produces: `ProposalCapture` `{request_id, kind, target, skill, filler:{kind,id,model}, context, artifact, validation:{ok, errors}, outcome, at}`; `ProposalPortfolio{captures, proposals, skipped}`; `DiscoveryAgent(registry=None).propose(opportunities, *, fill, gates=None) -> ProposalPortfolio`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_discovery_agent.py
def test_propose_routes_fills_captures_and_validates(tmp_path):
    opp = {"id":"anomaly:s3:c","source":"s3:c","action_type":"revise_model",
           "kind":"anomaly","value":{"score":0.5}}
    good = {"statement":"...","mechanism":"...",
            "predictions":[{"observable":"c","expected":{"op":"<","value":0.2},
                            "falsifier":"c>=0.2"}],
            "provenance":{"trigger":"unexplained_anomaly"}}
    def fill(req):
        return GenerationResult(request_id=req.id, artifact=good,
                                filler={"kind":"ai","id":"stub","model":"m"})
    port = DiscoveryAgent().propose([opp], fill=fill)
    assert [c.kind for c in port.captures] == ["hypothesize"]
    assert port.captures[0].validation["ok"] is True
    assert port.captures[0].filler["model"] == "m"
    assert len(port.proposals) == 1

def test_filler_raising_becomes_error_capture_not_crash():
    opp = {"id":"anomaly:s:o","source":"s:o","action_type":"revise_model","kind":"anomaly"}
    def fill(req): raise RuntimeError("boom")
    port = DiscoveryAgent().propose([opp], fill=lambda r: (_ for _ in ()).throw(RuntimeError()))
    assert port.captures[0].outcome == "error"
    assert port.proposals == []

def test_bad_artifact_is_captured_invalid_not_proposed():
    opp = {"id":"anomaly:s:o","source":"s:o","action_type":"revise_model","kind":"anomaly"}
    def fill(req): return GenerationResult(request_id=req.id, artifact={"statement":"x"},
                                           filler={"kind":"ai","id":"s","model":"m"})
    port = DiscoveryAgent().propose([opp], fill=fill)
    assert port.captures[0].validation["ok"] is False
    assert port.proposals == []
```

- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement `propose`** — pure except for calling `fill`:
  1. `reqs = generation_requests(opportunities, gates=gates)`.
  2. For each req: `sk = registry.get(req.kind)`; if `None`, append a `skipped` entry and continue.
  3. Call `fill(req)` inside try/except → on raise, a `ProposalCapture(outcome="error")`; on `None`, `outcome="empty"`.
  4. Validate `result.artifact` via `sk.coerce` in try/except → `validation={ok, errors}`.
  5. Build a `ProposalCapture` (always, success or not) recording `filler`, `context=req.context`, `artifact`, `outcome ∈ {proposed, error, empty, invalid}`.
  6. Only `ok` captures contribute to `port.proposals` (the validated artifact dict).
  `propose` does NOT write anything — capture + proposals are returned; persistence + apply are Task 5.

- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** — `feat(agents): DiscoveryAgent.propose + replayable ProposalCapture`

---

### Task 5: Persist captures + `discover propose` verb (apply under policy)

**Files:**
- Modify: `discovery_engine/discover/actions.py` (`record_captures`)
- Modify: `discovery_engine/discover/cli.py` (`propose` subcommand)
- Test: `tests/test_discover_propose_cli.py`, `tests/test_record_captures.py`

**Interfaces:**
- Consumes: `ProposalPortfolio` (Task 4), `apply_result` (Task 3), `AutonomyPolicy`.
- Produces: `record_captures(inv_path, captures) -> list[dict]` (append `agent_captures[]`, idempotent by capture id, reject incomplete per Review Focus); `discover propose <inv>` (dry-run: print the portfolio of requests+skills+gates the coordinator would route; `--fill` runs the injected `subprocess_filler`, records captures, and applies auto-gated validated proposals under `AutonomyPolicy`, queuing review-gated ones).

- [ ] **Step 1: Write the failing tests** — (a) `record_captures` appends, is idempotent, and raises on a capture missing `request_id`/`filler.model`/`artifact`; (b) `discover propose <inv>` dry-run lists the routed requests with their skill + gate and writes nothing; (c) `--fill` with a stub `VIVA_FILLER_CMD` records a capture and leaves a `review`-gated hypothesize proposal queued (not promoted into `hypotheses[]`).
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** `record_captures` (mirror `record_hypothesis_proposal`'s atomic, idempotent pattern) and the `propose` CLI verb (mirror the `requests`/`requests --fill` verb; route via `DiscoveryAgent` + registry; gate with the resolved `AutonomyPolicy`).
- [ ] **Step 4: Run to verify pass.** Then run the FULL not-slow suite + any workspace lint.
- [ ] **Step 5: Commit** — `feat(discover): 'propose' verb — coordinator routes skills, captures, applies under policy`

---

## Deferred (NOT in this PR — follow-ups)

- A `verdict_overreach` frontier kind (the critique *trigger*); this PR routes it if present but does not add it to `projection/engine.py`.
- Bounded tool runtime (tool allow-list, step/token caps) inside `fill` — the first PR treats `fill` as the bound.
- Routing `propose_study`/`acquire_model`/`author_tests`/`design` through the registry + coordinator (this PR seeds only the two scientific skills).
- Replacing `requests --fill` with the coordinator (kept separate to limit collision with the live generation seam).
- `DiscoveryAgent` feeding `advance_cycle` autonomously (kept to the explicit `discover propose` verb for now).
