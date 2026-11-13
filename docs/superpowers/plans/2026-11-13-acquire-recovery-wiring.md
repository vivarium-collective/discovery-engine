# Acquisition→Recovery Wiring (DE-13 shape C) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Close the acquisition loop: accepting a `ModelAcquisitionProposal` feeds its mechanism into the engine's already-wired `CapabilityGap → build → run` machinery, so a proposed mechanism actually gets built and run (recovered) — connecting slice-1 (proposal) to slice-2 (recovery).

**Architecture:** Spec-feeding (the investigated, recommended strategy). A new `accept_model_acquisition_proposal` in `discovery_engine/discover/actions.py` (modeled on the existing `set_hypothesis_proposal_status`, actions.py:258-287) rationale-gates, sets the proposal `status="accepted"` with a history stamp, and — when the mechanism is identifier-shaped (`capability_gaps._is_buildable_mechanism`) — idempotently appends it to the investigation's persisted `required_mechanisms[]` (the same field the feedback compiler writes, compiler.py:428-432, and that `propose_capability_gaps` reads via `required_mechanisms_from_spec`). Everything downstream already exists and stays untouched: the next `run_cycle` → `propose_capability_gaps` mints a content-addressed `cg:` gap; `accept_capability_gap` + the `build_process` dispatch + an injected `build_hook` + `advance_cycle` build and run it; and the recovery read-back (`evidence.observed`) is graded as in slice 2. No new build/run code — only the missing accept→spec link.

**Tech Stack:** Python 3.12; `discovery_engine.discover.actions`, `discovery_engine.phases.model_acquisition`, `discovery_engine.feedback.capability_gaps`, `discovery_engine.discover.cycle` (advance_cycle). The end-to-end test reuses the slice-2 recovery helpers already in `discovery_engine.benchmark` (`recovery_build.build_hook`, `recovery_run.benchmark_runner`).

**Spec:** upgrade-plan §16/§17 + `docs/upgrade-plan-0.6-reconciliation.md`; the "deferred follow-up" line in `discovery_engine/benchmark/__init__.py` (wiring the acquisition-proposal path into the recovery loop — this plan does exactly that). Binding honesty rule (AGENTS.md/CLAUDE.md): provided-mechanisms-only; the loop-closes claim must be EARNED by a real build+run, never faked; if the chain cannot be made to close, report an HONEST OPEN QUESTION.

## Global Constraints

- **No AI attribution, ever.** No `Co-Authored-By` trailer; no "Generated with Claude Code" in commits/PRs. (Overrides any default.)
- **Honesty — earned, not asserted.** The "acquisition path now closes the loop" claim must be proven by a test that drives the real chain end to end (accept → gap emitted by `run_cycle` → gap built by `build_hook` → run by `advance_cycle` → `evidence.observed` in band), reusing the slice-2 recovery helpers. No shortcut that writes the gap, the built model, or the observed value by hand. If the chain cannot be made to close, STOP and report it as an open question — do not fake the link.
- **Reuse, don't rebuild.** No new build/run machinery. The accept function only sets status + feeds `required_mechanisms`. All gap/build/run/grade code already exists and must stay untouched.
- **Additive & safe.** Do not change `record_model_acquisition_proposal`, the slice-1/slice-2 benchmark behavior, or any existing accept/reject function. A core file (`actions.py`) is edited by adding functions, not altering existing ones.
- **Non-buildable mechanisms handled honestly.** A prose `mechanism` (whitespace/punctuation — fails `_is_buildable_mechanism`) must NOT be appended to `required_mechanisms` (it would be dropped by the gate anyway, or worse, pollute the spec). The proposal is still accepted, but a history note records that no buildable gap could be fed (a human must supply a buildable mechanism id). Never silently feed a bad token.
- **Tests:** `PYTHONPATH=<worktree> /Users/eranagmon/code/discovery-engine/.venv/bin/python -m pytest`.

## Review Focus

- **Rationale gate:** `accept_model_acquisition_proposal` with an empty rationale must raise, like `accept_capability_gap` (actions.py:521-529). (Task 1)
- **Idempotency:** accepting the same proposal twice, or two proposals naming the same mechanism, appends the mechanism to `required_mechanisms` at most once (mirror the compiler's idempotent append). (Task 1)
- **Status guard:** only a `proposed` proposal can be accepted/declined; a re-accept of an already-accepted one is a no-op or a clear error, not a second append. (Task 1)
- **Non-buildable mechanism:** a prose mechanism is accepted but NOT fed to `required_mechanisms`; the history/return says why. (Task 1)
- **The loop actually closes (honesty crux):** the end-to-end test proves accept → `run_cycle` emits a `cg:` gap for the mechanism → gap built via `build_hook` → `advance_cycle` runs → `evidence.observed` lands in band. The observed value comes from the engine's grade, not the test. (Task 2)

---

### Task 1: `accept_model_acquisition_proposal` (+ decline) with spec-feeding

**Files:**
- Modify: `discovery_engine/discover/actions.py` (add functions near the other accept/reject wrappers, ~:521-538; and near `set_hypothesis_proposal_status` ~:258-287 as the structural template)
- Test: `tests/test_model_acquisition_accept.py`

**Interfaces:**
- Consumes: `model_acquisition.PROPOSED/ACCEPTED/DECLINED`; `capability_gaps._is_buildable_mechanism`; `study.io.load_yaml_mapping`/`save_yaml_atomic` (match how `accept_capability_gap`/`set_hypothesis_proposal_status` load+save).
- Produces:
  - `accept_model_acquisition_proposal(ws_root, inv_slug, ma_id, *, rationale, by="human") -> dict` — returns the updated proposal. Rationale-gated. Looks up the proposal in `investigation.yaml::model_acquisition_proposals[]` by `id` (raise if unknown; raise/no-op if not `proposed`). Sets `status="accepted"`, appends a `history` entry `{at, event:"accepted", by, rationale}` (add a `history` list if the shape lacks one — follow the viz/gap stamp). Then, IFF `_is_buildable_mechanism(proposal["mechanism"])`, idempotently append `proposal["mechanism"]` to the investigation spec's `required_mechanisms` list (create the list if absent; do not duplicate). If NOT buildable, do not append; add a history note `{event:"accepted_no_gap", reason:"mechanism is not identifier-shaped; a buildable mechanism id is needed to raise a capability gap"}`. Atomic-save the spec.
  - `decline_model_acquisition_proposal(ws_root, inv_slug, ma_id, *, rationale, by="human") -> dict` — rationale-gated; sets `status="declined"` + history; no spec-feeding.

- [ ] **Step 1: Write the failing tests.**
```python
def test_accept_requires_rationale(tmp_path): ...            # empty rationale -> ValueError
def test_accept_sets_status_and_feeds_required_mechanisms(tmp_path):
    # proposal mechanism "feedback_inhibition" (buildable)
    p = accept_model_acquisition_proposal(ws, slug, ma_id, rationale="build it")
    assert p["status"] == "accepted"
    spec = load_yaml_mapping(inv_path)
    assert spec["required_mechanisms"].count("feedback_inhibition") == 1
def test_accept_is_idempotent_on_required_mechanisms(tmp_path):
    accept_model_acquisition_proposal(...); # re-accept or a 2nd proposal same mechanism
    assert spec["required_mechanisms"].count("feedback_inhibition") == 1
def test_accept_prose_mechanism_not_fed(tmp_path):
    # mechanism "Distance-limited dispersal kernel." (prose) -> accepted, not appended
    p = accept_model_acquisition_proposal(...)
    assert p["status"] == "accepted"
    assert "required_mechanisms" not in spec or <prose> not in spec["required_mechanisms"]
    assert any(h.get("event") == "accepted_no_gap" for h in p["history"])
def test_decline_sets_status(tmp_path): ...
def test_accept_unknown_id_raises(tmp_path): ...
def test_accept_non_proposed_rejected(tmp_path): ...         # already accepted -> no 2nd append
```
- [ ] **Step 2: run (fail) → implement → run (pass).**
- [ ] **Step 3: Commit** (`feat(discover): accept_model_acquisition_proposal feeds the mechanism into required_mechanisms`).

---

### Task 2: End-to-end — the acquisition path closes the loop (honesty gate)

**Files:**
- Test: `tests/test_acquire_recovery_closes.py`

**Interfaces:**
- Consumes: Task 1's `accept_model_acquisition_proposal`; `discover.run_cycle`/`load_cycle_inputs`/`advance_cycle`; `capability_gaps.accept_capability_gap` (+ the actions.py wrapper); `discovery_engine.benchmark.recovery_build.build_hook`, `recovery_run.benchmark_runner`, and `recovery_scenario`/`recovery_model` helpers for a workspace + storeless toy evaluator.

**CRITICAL for the causal (negative-half) test:** the slice-2 `build_recovery_scenario` PRE-DECLARES the mechanism in the study's `required_mechanisms`, which makes `run_cycle` emit the gap *without* any acceptance. For this test the workspace must START WITHOUT the mechanism in any `required_mechanisms` (so the gap is absent pre-accept), and ONLY `accept_model_acquisition_proposal` introduces it. Adapt the helpers (or build a fresh minimal workspace reusing `recovery_model.STORELESS_EVALUATOR_SRC` + `recovery_build`/`recovery_run`) accordingly — do not reuse `build_recovery_scenario` unmodified.

- [ ] **Step 1: Write the end-to-end test.** Build a workspace (adapt the slice-2 recovery helpers per the note above) in which a study has an anomaly whose resolution is an `acquire_model` mechanism, but with NO pre-declared `required_mechanisms`. Then drive the REAL chain:
  1. Author + record a `ModelAcquisitionProposal` naming `feedback_inhibition` (via the acquire_model record path, as the oracle does).
  2. `accept_model_acquisition_proposal(...)` → assert the mechanism is now in the investigation's `required_mechanisms`.
  3. `run_cycle` (fresh crank) → assert a `cg:` capability gap for `feedback_inhibition` is now emitted/persisted (it was NOT before acceptance — assert that too, pinning the causal link).
  4. Accept the gap (`accept_capability_gap`, rationale) and `advance_cycle(ws, slug, autonomous=True, build_hook=recovery_build.build_hook("feedback_inhibition"), runner=recovery_run.benchmark_runner(), max_runs=4)`.
  5. Assert the gap reached `built`, a run executed, and the build-materialized study's `evidence.observed` (from the engine grade) is in the acceptance band `[0.3, 0.8]`.
  Add the negative half: WITHOUT the accept step, `run_cycle` emits NO gap for that mechanism (proving acceptance is the cause).

- [ ] **Step 2: Run it.** Expected: PASS — the loop closes. If the chain does NOT close (e.g. an investigation-level `required_mechanism` with no owning study does not mint a gap, or the materialized-study run does not grade), use `superpowers:systematic-debugging`; the smallest honest fix is allowed in Task-1's accept function or this test's workspace shape, but NEVER fabricate the gap/observed value. If it genuinely cannot close, STOP and report the open question.

- [ ] **Step 3: Commit** (`test(discover): acquisition proposal → accept → capability gap → build → run closes end to end`).

---

### Task 3: Docs — the acquisition path now closes the loop

**Files:**
- Modify: `discovery_engine/benchmark/__init__.py` (deferred-follow-up list)
- Modify: `discovery_engine/phases/model_acquisition.py` (module docstring: note that acceptance now feeds a capability gap) OR `discovery_engine/skills/acquire_model/SKILL.md` if that is the natural home — implementer's call, one home.

- [ ] **Step 1:** In `benchmark/__init__.py`, remove "wiring the *acquisition-proposal* path (slice 1) to this capability-gap recovery loop" from the deferred-follow-up list (now DONE) and note the loop closes via `accept_model_acquisition_proposal`. The remaining deferred follow-up is the real-LLM filler. In `model_acquisition.py`, update the line "A proposal is never built, promoted, or marked verified by this code." to stay accurate: this module still only types+validates; acceptance (in `discover.actions`) now promotes an accepted proposal's mechanism into `required_mechanisms`, which the capability-gap loop builds+runs. Keep it precise — do not overclaim autonomy (acceptance is still a decider/human step; there is no auto-accept for proposals yet — that is a noted follow-up).
- [ ] **Step 2: Commit** (`docs: acceptance of a model-acquisition proposal now feeds the capability-gap recovery loop`).
