# Automatic Decider-Driven Accept for Model-Acquisition Proposals Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Let an AI `decider` automatically accept (or decline) a `proposed` `model_acquisition_proposal` under autonomy — mirroring how `capability_gap`s and `viz_proposal`s are decider-accepted — so the acquisition→recovery loop can run with **no human in the accept step**.

**Architecture:** Model-acquisition proposals have ZERO frontier wiring today (they are stored at `investigation.yaml::model_acquisition_proposals[]` but never emitted as an actionable item). This adds that wiring, copying the viz-proposal pattern (a proposed item whose accept feeds a downstream loop, with no build hook of its own): (1) a new frontier kind `model_acquisition` (op `accept_model_acquisition`) emitted for `status=="proposed"` proposals; (2) a new `execute_action` branch that, under autonomy + an `AutonomyPolicy` gate, calls the kind-agnostic `decider` and routes approve→`accept_model_acquisition_proposal` / reject→`decline_model_acquisition_proposal` (both already exist in `discover.actions`). Acceptance feeds the mechanism into `required_mechanisms`, and the existing capability-gap loop (also decider-accepted, then built by `advance_cycle`) takes it from there. The decider is injected exactly as today (`--ai-decide` → `_env_decider`, kind-agnostic); no decider/CLI-flag change is needed.

**Tech Stack:** Python 3.12; `discovery_engine/projection/engine.py` (frontier/`_PRIORITY`/`_OP`/`_executor_for`), `discovery_engine/discover/cycle.py` (`execute_action`), `discovery_engine/discover/_policy.py` (`AutonomyPolicy.allows`), `discovery_engine/discover/actions.py` (`accept_model_acquisition_proposal`/`decline_model_acquisition_proposal`). The e2e test reuses the slice-2 recovery helpers in `discovery_engine.benchmark`.

**Spec:** upgrade-plan §16/§17 + `docs/upgrade-plan-0.6-reconciliation.md`; the "deferred follow-up" line in `discovery_engine/benchmark/__init__.py` (an automatic decider-driven accept for proposals — this plan does exactly that). Binding honesty rule (AGENTS.md/CLAUDE.md): auto-accept is a consequential action; it must be decider- AND policy-gated, never blanket. The "runs with no human accept" claim must be EARNED by a test driving the real decider path; if the chain cannot close, report an HONEST OPEN QUESTION.

## Global Constraints

- **No AI attribution, ever.** No `Co-Authored-By` trailer; no "Generated with Claude Code". (Overrides any default.)
- **Gated autonomy — never blanket.** Auto-accept happens ONLY when: `decider is not None` AND `autonomous` AND `pol.allows(consequence="build", target=<ma-id>)`. Any of these absent → `gated-human` (no mutation). Mirror the capability-gap branch's gate exactly (`cycle.py:1058`). A proposal with no decider, outside autonomy, or outside the policy allowlist is NEVER auto-accepted.
- **Only `proposed` is actionable.** The frontier emits a `model_acquisition` item ONLY for `status=="proposed"` proposals (copy the viz-proposal block which emits only `proposed`). An `accepted`/`declined` proposal is terminal and never re-emitted (else an accept loop).
- **Reuse the existing accept logic.** Acceptance/decline MUST call the existing `actions.accept_model_acquisition_proposal` / `actions.decline_model_acquisition_proposal` (do not duplicate the spec-feeding). Stamp `by="discovery-ai"` and `rationale=f"[ai-driven] {why}"` like the viz/gap branches.
- **Additive.** Do not change the viz-proposal or capability-gap frontier/executor/branch behavior. New kind only. NOTE: adding the emission means any investigation with a `proposed` model_acquisition_proposal now shows a new frontier/opportunity item — that is correct (it is now actionable). If an existing frontier/opportunity snapshot test changes, update it to reflect the new actionable item; never suppress the emission to keep a snapshot green.
- **Tests:** `PYTHONPATH=<worktree> /Users/eranagmon/code/discovery-engine/.venv/bin/python -m pytest`.

## Review Focus

- **Gate completeness:** decider None → gated-human; `autonomous=False` → gated-human; `pol.allows(...)` False → gated-human; proposal not `proposed` → gated-human. No path auto-accepts without all of decider+autonomous+policy. (Task 2)
- **Only-proposed emission:** an `accepted` or `declined` proposal is NOT emitted into the frontier (no re-accept loop). (Task 1)
- **Real accept reused:** approve calls `accept_model_acquisition_proposal` (so the mechanism lands in `required_mechanisms`); reject calls `decline_model_acquisition_proposal`; a malformed decider reply (`choice` not in approve/accept/reject, or empty rationale) → gated-human, no mutation. (Task 2)
- **Autonomous loop earned (honesty):** the e2e test drives the real decider path (a decider callable), auto-accepts a proposal with NO human `accept_*` call in the test, and the mechanism then flows to recovery. (Task 3)
- **No regression in existing frontier/opportunity output** beyond the new actionable item. (Task 1)

---

### Task 1: Frontier emission for a proposed model-acquisition proposal

**Files:**
- Modify: `discovery_engine/projection/engine.py` (`_PRIORITY` ~:38, `_OP` ~:67, `frontier()` emission near the viz block ~:307-319, `_executor_for` near ~:707/725)
- Test: `tests/test_model_acquisition_frontier.py`

**Interfaces:**
- Produces: a frontier row `{kind:"model_acquisition", target:<ma-id>, priority:~43, op:"accept_model_acquisition", rationale}` for each `inv_spec["model_acquisition_proposals"]` entry with `status=="proposed"`; `opportunities()` then yields `{id:"model_acquisition:<ma-id>", source:<ma-id>, action_type:"accept_model_acquisition", kind:"model_acquisition", …}` with `consequence:"review"` (via `_executor_for`).

- [ ] **Step 1: Write failing tests.**
```python
def test_proposed_proposal_emitted_as_frontier_item():
    # inv_spec with one model_acquisition_proposals entry status=proposed
    rows = frontier(cyc, inv_spec)                       # or the public frontier entrypoint
    ma = [r for r in rows if r["kind"] == "model_acquisition"]
    assert len(ma) == 1 and ma[0]["op"] == "accept_model_acquisition"
    assert ma[0]["target"].startswith("ma:")

def test_accepted_or_declined_proposal_not_emitted():
    # status=accepted / status=declined -> no model_acquisition row
    assert not any(r["kind"] == "model_acquisition" for r in frontier(cyc, inv_spec))

def test_opportunity_consequence_is_review():
    opp = next(o for o in opportunities(cyc, inv_spec) if o["kind"] == "model_acquisition")
    # executed via execute_plan / _executor_for -> consequence review, auto_safe False
    assert opp["action_type"] == "accept_model_acquisition"
```
Read the EXACT viz-proposal emission block (`engine.py:307-319`) and `build_process` executor (`engine.py:725-727`) first and mirror them; match the test harness other `projection` tests use to call `frontier`/`opportunities`.
- [ ] **Step 2–4: run (fail) → implement (add `_PRIORITY["model_acquisition"]`, `_OP["model_acquisition"]="accept_model_acquisition"`, the emission loop, the `_executor_for` branch) → run (pass).** Then run the WHOLE suite; if a frontier/opportunity snapshot test changed because a fixture has a proposed proposal, update it to include the new actionable item (do not suppress emission).
- [ ] **Step 5: Commit** (`feat(projection): emit a proposed model_acquisition_proposal as an accept_model_acquisition frontier item`).

---

### Task 2: `execute_action` decider branch (policy-gated auto-accept)

**Files:**
- Modify: `discovery_engine/discover/cycle.py` (`execute_action`, add a branch after the capability-gap branch ~:1102)
- Test: `tests/test_model_acquisition_execute.py`

**Interfaces:**
- Consumes: `decider(target, detail) -> {"choice": "approve"|"accept"|"reject", "rationale": str}`; `AutonomyPolicy.allows(consequence="build", target=<ma-id>)`; `actions.accept_model_acquisition_proposal` / `actions.decline_model_acquisition_proposal`.
- Behavior: when the top plan item has `action_type=="accept_model_acquisition"` and `source.startswith("ma:")`:
  - `decider is None or not autonomous` → `rec["status"]="gated-human"` (no mutation).
  - load the proposal from `model_acquisition_proposals`; `None` or `status!="proposed"` → `gated-human`.
  - `not pol.allows(consequence="build", target=top["source"])` → `gated-human`.
  - else `choice = decider(top["source"], top["source"])`; `verb=choice["choice"].strip().lower()`, `why=choice["rationale"].strip()`; `verb not in ("approve","accept","reject") or not why` → `gated-human`.
  - `verb=="reject"` → `decline_model_acquisition_proposal(ws_root, inv_slug, top["source"], rationale=f"[ai-driven] {why}", by="discovery-ai")`, `rec["status"]="executed"`, outcome notes declined.
  - else → `accept_model_acquisition_proposal(ws_root, inv_slug, top["source"], rationale=f"[ai-driven] {why}", by="discovery-ai")`, `rec["status"]="executed"`, outcome notes accepted + whether a mechanism was fed.
  Copy the VIZ-proposal branch (`cycle.py:1014-1046`) for structure and ADD the `pol.allows(consequence="build", …)` gate like the gap branch (`cycle.py:1058`).

- [ ] **Step 1: Write failing tests** (drive `execute_action` directly with a fake decider + policy):
```python
def _decider(verb, why="because"): return lambda t, d: {"choice": verb, "rationale": why}

def test_autonomous_decider_accepts_and_feeds_mechanism(tmp_path):
    # ws with one proposed proposal (mechanism "feedback_inhibition"); autonomy allows build
    rec = execute_action(ws, slug, autonomous=True, decider=_decider("approve"),
                         target=ma_id, action="accept_model_acquisition", policy=<allow-all>)
    assert rec["status"] == "executed"
    spec = load_yaml_mapping(inv_path)
    assert next(p for p in spec["model_acquisition_proposals"] if p["id"]==ma_id)["status"]=="accepted"
    assert "feedback_inhibition" in spec.get("required_mechanisms", [])

def test_reject_declines(tmp_path):
    rec = execute_action(..., decider=_decider("reject"), ...)
    assert rec["status"] == "executed"
    assert <proposal>["status"] == "declined"
    assert "feedback_inhibition" not in spec.get("required_mechanisms", [])

def test_no_decider_is_gated(tmp_path):
    rec = execute_action(..., autonomous=True, decider=None, ...)
    assert rec["status"] == "gated-human" and <proposal still proposed>

def test_not_autonomous_is_gated(tmp_path):
    rec = execute_action(..., autonomous=False, decider=_decider("approve"), ...)
    assert rec["status"] == "gated-human"

def test_policy_denies_is_gated(tmp_path):
    rec = execute_action(..., autonomous=True, decider=_decider("approve"), policy=<deny build>)
    assert rec["status"] == "gated-human" and <proposal still proposed>

def test_malformed_decider_reply_is_gated(tmp_path):
    rec = execute_action(..., decider=lambda t,d: {"choice":"maybe","rationale":""}, ...)
    assert rec["status"] == "gated-human" and <proposal still proposed>
```
Read `_policy.py:72` (`allows`) to build allow-all and deny policies the way other cycle tests do (match the capability-gap execute tests, e.g. `tests/test_capability_gap_advance.py` / any `execute_action` policy test).
- [ ] **Step 2–3: run (fail) → implement → run (pass).**
- [ ] **Step 4: Commit** (`feat(discover): execute_action auto-accepts a model_acquisition_proposal via the policy-gated decider`).

---

### Task 3: End-to-end — the acquisition→recovery loop runs with NO human accept (honesty gate)

**Files:**
- Test: `tests/test_autonomous_acquire_recovery.py`

**Interfaces:**
- Consumes: Task 1+2 wiring; `execute_action` with a decider; `discover.advance_cycle`; the slice-2 recovery helpers (`recovery_build.build_hook`, `recovery_run.benchmark_runner`, `recovery_model.STORELESS_EVALUATOR_SRC`) and a workspace that starts WITHOUT the mechanism in `required_mechanisms` (adapt the `test_acquire_recovery_closes.py` workspace).

- [ ] **Step 1: Write the e2e test.** With a decider that approves (and an autonomy policy that allows build), drive the chain using NO human `accept_*` call anywhere in the test:
  1. Record a `ModelAcquisitionProposal` (real path) naming `feedback_inhibition`.
  2. Crank the frontier → assert a `model_acquisition:<ma-id>` opportunity exists.
  3. `execute_action(..., autonomous=True, decider=approve, policy=allow)` on that `ma:` target → assert the proposal is `accepted` and `feedback_inhibition` is in `required_mechanisms` — **accepted by the decider, not by a human call**.
  4. Crank again → a `cg:` capability gap now exists; accept it VIA THE DECIDER too (`execute_action` on the `cg:` target with the same decider+policy), then `advance_cycle(..., autonomous=True, build_hook=recovery_build.build_hook("feedback_inhibition"), runner=recovery_run.benchmark_runner(), max_runs=4)`.
  5. Assert the gap reached `built`, a run executed, and the recovered study's `evidence.observed` is in band `[0.3, 0.8]` — the loop closed with the decider making both accept decisions.
  Add a negative half: with `decider=reject` at step 3, the proposal is `declined`, no mechanism is fed, and no gap/recovery follows.
- [ ] **Step 2: Run it.** If the chain cannot close autonomously, use `superpowers:systematic-debugging`; the smallest honest fix is allowed in Task 1/2, never fabricate the accept/gap/observed. If it genuinely cannot close, STOP and report the open question.
- [ ] **Step 3: Commit** (`test(discover): acquisition→recovery runs autonomously — decider accepts the proposal AND the gap, no human`).

---

### Task 4: Docs — the loop can run autonomously

**Files:**
- Modify: `discovery_engine/benchmark/__init__.py` (remove the auto-accept deferred item); `discovery_engine/phases/model_acquisition.py` (docstring: acceptance can now be decider-automated under autonomy).

- [ ] **Step 1:** In `benchmark/__init__.py`, remove "an automatic decider-driven accept for proposals (acceptance is a human/decider step today)" from the deferred-follow-up list (now DONE); the only remaining deferred item is the §17 matrix rows. In `model_acquisition.py`, update the acceptance note: acceptance (in `discover.actions`) is now reachable automatically by an AI `decider` under autonomy + an `AutonomyPolicy` that allows the build (via the `accept_model_acquisition` frontier item + `execute_action`), in addition to a human call — so the full acquisition→recovery loop can run with no human in the accept step. Keep it precise: auto-accept is gated (decider + autonomous + policy); it is not unconditional.
- [ ] **Step 2: Commit** (`docs: model-acquisition proposals can be auto-accepted by the decider under autonomy`).
