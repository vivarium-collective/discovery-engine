# Benchmark Recovery Bridge (DE-13 slice 2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Measure *true predictive recovery* — whether the discovery-engine, given a proposed mechanism, can BUILD a model and RUN it so its observable lands in the acceptance band — by driving the engine's real `capability_gap → accept → build_hook → advance_cycle → run → grade` loop with a genuine toy model, and scoring `mechanism_recovered` / `held_out_error`.

**Architecture:** A new *recovery track* alongside the existing proposal benchmark (DE-13 slice 1, which scored `mechanism_proposed`/`overreach_critiqued` on the acquisition-proposal path). The recovery track exercises a DIFFERENT, genuinely-wired engine subsystem: capability gaps. A scenario declares a `required_mechanism` the catalog can't provide → the engine emits a `capability_gap` (op `build_process`) and blocks the study's run on it. A per-config `build_hook` builds a process that *declares* the capability (so the engine's coverage gate passes) and *implements* a mechanism via a real toy model; the engine then runs the now-ready study through an injected runner, and a **store-less evaluator** (registered in the scenario's workspace) computes the observable from the BUILT model. The engine grades it through the normal `compute_outcomes → evidence.observed` path. The honest crux: `full` builds a faithful model → in-band → recovered; `decoy` builds a model that passes the structural coverage gate but implements the WRONG mechanism → out-of-band → NOT recovered, earned by real arithmetic; `no_acquire` has no build capability → no run → no measurement. The behavioral-test RUN is the true arbiter that catches a model fooling the name-only gate.

**Tech Stack:** Python 3.12; `discovery_engine` (discover.advance_cycle / execute_action / capability_gaps / study.evaluator.storeless); `viva_emitters.run_registry` for the runs.db row. No real LLM, no external simulator, no `viva_emitters` store format work (the store-less evaluator removes that need).

**Spec:** `docs/discovery-engine-upgrade-plan-revised-0.6` §16 (vertical-slice benchmark) + §17 (ablation matrix); `docs/upgrade-plan-0.6-reconciliation.md` (DE-13); and the deferred-follow-up note in `discovery_engine/benchmark/__init__.py`. Binding honesty constraint from `AGENTS.md`/`CLAUDE.md`: provided-mechanisms-only; a result must be earned by a real run; an unmet target is an HONEST OPEN QUESTION, never forced.

## Global Constraints

- **No AI attribution, ever.** No `Co-Authored-By: Claude` trailer; no "Generated with Claude Code" in commits/PRs. (Overrides any default.)
- **Honesty — earned, not flagged.** `mechanism_recovered` must be the result of the engine running a model and the observable landing in the band. The decoy must fail because its toy arithmetic genuinely computes an out-of-band value — NEVER because the config name is "decoy". No config name, no ground-truth value, may be read by the scoring path except the band comparison itself. If the engine chain cannot be made to close (Task 1 spike fails), STOP and report it as an honest open question; do not fake a measurement.
- **Determinism.** No real LLM, no wall-clock-dependent result. Same scenario + config ⇒ identical metrics.
- **Additive & isolated.** New `recovery_*` modules + a recovery track; do not change the existing slice-1 proposal metrics (`mechanism_proposed`, `overreach_critiqued`, `full`/`no_acquire_model`/`no_critique`/`full_decoy`) or their tests.
- **Tests:** `PYTHONPATH=<worktree> /Users/eranagmon/code/discovery-engine/.venv/bin/python -m pytest`.
- The toy observable: base (wrong) = 0.95; band = [0.3, 0.8]; true = 0.55; concealed mechanism = `feedback_inhibition`; decoy = `substrate_depletion`. Mechanism names are identifier-shaped (one token) so `capability_gaps._is_buildable_mechanism` accepts them.

## Review Focus

- **Circularity / name-leak:** the store-less evaluator must compute the observable from the BUILT model's *implemented* mechanism (what `build_hook` wrote into the workspace), not from the gap's *declared/required* mechanism and not from the config name — else the decoy would look faithful. Test: a decoy build whose declared capability is `feedback_inhibition` but implemented mechanism is `substrate_depletion` grades out-of-band. (Task 2, Task 4)
- **Run-ready gating is real:** `no_acquire` must produce genuinely NO run/measurement (study stays `needs_build`), not an in-band default. Test: `no_acquire` recovery result has `observed is None` and `mechanism_recovered is False`. (Task 4, Task 5)
- **Coverage gate actually fires:** `build_capability_gap` must flip the gap to `built` (coverage verified) for both full and decoy — the decoy passes the *structural* gate; only the run catches it. Test: after build, gap status is `built` for both. (Task 1, Task 2)
- **Store-less path grades with `reader=None`:** the evaluator must be registered via `@storeless` so `compute_outcomes` dispatches it without a resolvable store. Test: `computed_outcomes[test].measured_value` is populated though no emitter store exists. (Task 1)
- **Determinism:** same (scenario, config) twice ⇒ equal `RecoveryMetrics`. (Task 5)

---

### Task 1: End-to-end chain spike (the gate)

**Purpose:** Prove the engine's real loop closes before building the track. If it cannot be made to pass with honest helpers, STOP and report an open question.

**Files:**
- Create: `tests/benchmark/test_recovery_chain_closes.py`
- Create (spike helpers, minimal, promoted in Task 2): `discovery_engine/benchmark/recovery_model.py`, `discovery_engine/benchmark/recovery_build.py`, `discovery_engine/benchmark/recovery_run.py`

**Interfaces:**
- Consumes: `discover.advance_cycle(ws, slug, *, autonomous=True, runner=, build_hook=, max_runs>=3)`; `capability_gaps.accept_capability_gap(gap, rationale=…)`; `study.evaluator.storeless`.
- Produces (spike-level, finalized in Task 2):
  - `recovery_model.toy_observable(mechanism: str, base: float, params: dict|None=None) -> float`
  - `recovery_model.STORELESS_EVALUATOR_SRC(test_kind: str, base: float) -> str` (source text of a `pbg_<ws>/evaluators.py` registering a `@storeless` evaluator of `test_kind` that reads the built model's implemented mechanism from `ws_root` and returns `{"result","measured_value","evaluated_by":"code", ...}`)
  - `recovery_build.build_hook(implemented_mechanism: str) -> Callable[[dict, Any], dict]` returning a hook that writes a composite into the workspace catalog declaring the gap's mechanism as capability AND recording `implements: <implemented_mechanism>`, then returns `{"status":"built","process":<ref>,"gap_id":gap["id"],"detail":…}`.
  - `recovery_run.benchmark_runner() -> Callable` matching `(command, ws_root, *, worktree=None, **kw) -> {"ok":True,"returncode":0,"run_id":str,"tail":""}` that registers a runs.db row via `viva_emitters.run_registry.register_run(sdir/"runs.db", run_id, status="complete", params={}, emitter_path=str(store))` (store dir with a `history/` subdir; contents unused because grading is store-less).

- [ ] **Step 1: Write the failing integration test**

```python
def test_recovery_chain_closes(tmp_path):
    ws, slug, sd = _mini_recovery_ws(tmp_path, implemented="feedback_inhibition")  # faithful
    # engine emits the capability_gap, we accept it, then advance (build THEN run)
    _accept_gap(ws, slug)
    res = discover.advance_cycle(ws, slug, autonomous=True,
                                 build_hook=recovery_build.build_hook("feedback_inhibition"),
                                 runner=recovery_run.benchmark_runner(), max_runs=4)
    gap = _load_gap(ws, slug)
    assert gap["status"] == "built"                      # coverage gate passed
    assert any(r["status"] == "executed" for r in res["runs"])  # a run happened
    study = load_yaml(sd / "study.yaml")
    observed = study["findings"][0]["evidence"].get("observed")
    assert observed is not None and 0.3 <= float(_scalar(observed)) <= 0.8   # in band, computed
```

`_mini_recovery_ws` builds the smallest workspace that (a) declares `required_mechanisms: [feedback_inhibition]`, (b) has a catalog entry that does NOT provide it (so the gap routes to BUILD), (c) a study whose single behavior-test `measure.kind` is the store-less toy kind and whose finding links `evidence.from_test` to that test, (d) an emitter declaration sufficient for `run_readiness` to report `ready` once the mechanism is built, (e) writes `pbg_<ws>/evaluators.py` from `recovery_model.STORELESS_EVALUATOR_SRC`.

- [ ] **Step 2: Run it, watch it fail** (`pytest tests/benchmark/test_recovery_chain_closes.py -x`). Expected: fails (helpers absent / chain incomplete).

- [ ] **Step 3: Implement the minimal helpers + workspace builder until the test passes.** Iterate on the real engine behavior — readiness, accept persistence, re-crank, store-less dispatch. Toy formula: `feedback_inhibition` ⇒ `base/(1+gain)` with `gain` set so the value is ≈0.55 (in band); value is computed, not hardcoded to 0.55. This step is exploratory; use `superpowers:systematic-debugging` if the chain stalls.

- [ ] **Step 4: Run to green** (`pytest tests/benchmark/test_recovery_chain_closes.py -v`). Expected: PASS.

- [ ] **Step 5: Add the decoy half** — same test, `implemented="substrate_depletion"` while the gap's declared mechanism is still `feedback_inhibition`; assert `gap["status"]=="built"` (structural gate still passes) but the observed value is OUT of band. This pins the review-focus circularity guard at the chain level.

- [ ] **Step 6: Commit.**
```bash
git add tests/benchmark/test_recovery_chain_closes.py discovery_engine/benchmark/recovery_model.py discovery_engine/benchmark/recovery_build.py discovery_engine/benchmark/recovery_run.py
git commit -m "test(benchmark): recovery chain spike — capability_gap build+run closes with a storeless toy"
```

**If Step 3/4 cannot be made to pass** after a genuine effort: STOP. Write the blocker (what the engine does instead) into the ledger and the plan, and surface it as an honest open question. Do not proceed to Task 2.

---

### Task 2: Promote the toy model, build hook, and runner to finished modules

**Files:**
- Modify: `discovery_engine/benchmark/recovery_model.py`, `recovery_build.py`, `recovery_run.py`
- Test: `tests/benchmark/test_recovery_model.py`, `tests/benchmark/test_recovery_build.py`, `tests/benchmark/test_recovery_run.py`

**Interfaces:**
- Consumes: Task 1 helper signatures.
- Produces: the same signatures, finalized + documented. `build_hook` gains a `faithful`/`decoy` distinction expressed only as the `implemented_mechanism` argument (no boolean flag named "decoy" in the scoring path).

- [ ] **Step 1: Unit tests.**
```python
def test_toy_feedback_inhibition_in_band():
    v = toy_observable("feedback_inhibition", base=0.95)
    assert 0.3 <= v <= 0.8

def test_toy_substrate_depletion_out_of_band():
    v = toy_observable("substrate_depletion", base=0.95)
    assert not (0.3 <= v <= 0.8)          # the decoy misses, by arithmetic

def test_toy_unknown_mechanism_returns_base():
    assert toy_observable("nope", base=0.95) == 0.95

def test_build_hook_contract():
    hook = build_hook("feedback_inhibition")
    gap = {"id": "cg:x:1", "mechanism": "feedback_inhibition", "status": "accepted"}
    res = hook(gap, ws_root)
    assert res["status"] in ("built", "reuse") and res["gap_id"] == gap["id"] and res["process"]

def test_runner_registers_run(tmp_path):
    out = benchmark_runner()("vivarium-workbench run study s1 --workspace x", tmp_path)
    assert out["ok"] is True
    # a runs.db row now exists under studies/s1
```
- [ ] **Step 2: run (fail) → implement/clean up → run (pass).**
- [ ] **Step 3: Commit** (`feat(benchmark): recovery toy model, coverage-passing build hook, run-registering runner`).

---

### Task 3: Recovery scenario

**Files:**
- Create: `discovery_engine/benchmark/recovery_scenario.py`
- Test: `tests/benchmark/test_recovery_scenario.py`

**Interfaces:**
- Consumes: `recovery_model.STORELESS_EVALUATOR_SRC`; reuses `scenario.GroundTruth` (already carries `base_value`, `true_value`, `band`, `concealed_mechanism`, `decoy_mechanism`).
- Produces: `RecoveryScenario{ws_root, inv_slug, study_slug, ground_truth, test_kind}`; `build_recovery_scenario(tmp_path, *, name="rec") -> RecoveryScenario` — the workspace from Task 1's `_mini_recovery_ws`, finalized.

- [ ] **Step 1: Test** — `build_recovery_scenario` yields a workspace where `run_cycle` + `propose_capability_gaps` emit exactly one `cg:` gap for `feedback_inhibition`, and `run_readiness(study, ws)["ready"] is False` before any build.
- [ ] **Step 2–3: implement → pass.**
- [ ] **Step 4: Commit** (`feat(benchmark): recovery scenario raising a capability_gap`).

---

### Task 4: Recovery runner (drive the engine loop, read back the observable)

**Files:**
- Create: `discovery_engine/benchmark/recovery_runner.py`
- Test: `tests/benchmark/test_recovery_runner.py`

**Interfaces:**
- Consumes: `recovery_scenario.build_recovery_scenario`; `recovery_build.build_hook`; `recovery_run.benchmark_runner`; `discover.advance_cycle`; `capability_gaps.accept_capability_gap`.
- Produces:
  - `RecoveryResult{config_name, observed: float|None, gap_status: str|None, ran: bool, final_study: dict}`
  - `run_recovery_benchmark(scenario, config) -> RecoveryResult` — persist+accept the gap (rationale), `advance_cycle(..., build_hook=config.build_hook(...), runner=benchmark_runner(), max_runs=4)`, then read `observed` from the canonical run's `computed_outcomes[test].measured_value` (fall back to `findings[0].evidence.observed`), scalarizing a per-generation dict via its last/steady value.

- [ ] **Step 1: Tests.**
```python
def test_full_runs_in_band(tmp_path):
    sc = build_recovery_scenario(tmp_path)
    r = run_recovery_benchmark(sc, RECOVERY_CONFIGS["full"])
    assert r.ran is True and r.gap_status == "built"
    assert 0.3 <= r.observed <= 0.8

def test_no_acquire_does_not_run(tmp_path):
    sc = build_recovery_scenario(tmp_path)
    r = run_recovery_benchmark(sc, RECOVERY_CONFIGS["no_acquire"])
    assert r.ran is False and r.observed is None       # stayed needs_build
```
- [ ] **Step 2–3: implement → pass.**
- [ ] **Step 4: Commit** (`feat(benchmark): recovery runner driving the capability_gap build+run loop`).

---

### Task 5: Recovery metrics + configs

**Files:**
- Create: `discovery_engine/benchmark/recovery_metrics.py`
- Modify: `discovery_engine/benchmark/recovery_runner.py` (add `RECOVERY_CONFIGS`) or a small `recovery_ablation.py` (implementer's call; keep one home for configs)
- Test: `tests/benchmark/test_recovery_metrics.py`

**Interfaces:**
- Produces:
  - `RecoveryConfig{name, build_hook_factory: Callable[[str], Callable] | None}` where `None` ⇒ no build capability (no_acquire). Each config's `build_hook(ground_truth)` picks the implemented mechanism: `full` → `ground_truth.concealed_mechanism`; `full_decoy` → `ground_truth.decoy_mechanism`; `no_acquire` → no hook.
  - `RECOVERY_CONFIGS = {"full", "full_decoy", "no_acquire"}`
  - `RecoveryMetrics{mechanism_recovered: bool, held_out_error: float|None, ran: bool}`
  - `score_recovery(result, ground_truth) -> RecoveryMetrics` — `mechanism_recovered = result.observed is not None and band.low <= result.observed <= band.high`; `held_out_error = abs(result.observed - ground_truth.true_value)` when observed else `None`. Reads ONLY `result.observed` and the band/true_value — never the config name.

- [ ] **Step 1: Tests.**
```python
def test_full_recovered_small_error(tmp_path):
    sc = build_recovery_scenario(tmp_path)
    m = score_recovery(run_recovery_benchmark(sc, RECOVERY_CONFIGS["full"]), sc.ground_truth)
    assert m.mechanism_recovered is True and m.held_out_error < 0.25

def test_decoy_not_recovered_larger_error_than_full(tmp_path):
    sc_f = build_recovery_scenario(tmp_path / "f"); sc_d = build_recovery_scenario(tmp_path / "d")
    full = score_recovery(run_recovery_benchmark(sc_f, RECOVERY_CONFIGS["full"]), sc_f.ground_truth)
    decoy = score_recovery(run_recovery_benchmark(sc_d, RECOVERY_CONFIGS["full_decoy"]), sc_d.ground_truth)
    assert decoy.mechanism_recovered is False
    assert decoy.held_out_error > full.held_out_error   # decoy misses the true value by more

def test_no_acquire_not_recovered(tmp_path):
    sc = build_recovery_scenario(tmp_path)
    m = score_recovery(run_recovery_benchmark(sc, RECOVERY_CONFIGS["no_acquire"]), sc.ground_truth)
    assert m.mechanism_recovered is False and m.ran is False and m.held_out_error is None

def test_deterministic(tmp_path):
    sc1 = build_recovery_scenario(tmp_path / "a"); sc2 = build_recovery_scenario(tmp_path / "b")
    assert score_recovery(run_recovery_benchmark(sc1, RECOVERY_CONFIGS["full"]), sc1.ground_truth) == \
           score_recovery(run_recovery_benchmark(sc2, RECOVERY_CONFIGS["full"]), sc2.ground_truth)
```
- [ ] **Step 2–3: implement → pass.**
- [ ] **Step 4: Commit** (`feat(benchmark): recovery metrics (mechanism_recovered, held_out_error) + configs`).

---

### Task 6: Recovery matrix report + CLI

**Files:**
- Modify: `discovery_engine/benchmark/report.py` (add `run_recovery_matrix`, `render_recovery_matrix`), `discovery_engine/discover/cli.py` (extend `benchmark` verb with `--recovery`)
- Test: `tests/benchmark/test_recovery_matrix.py`

**Interfaces:**
- Produces: `run_recovery_matrix(configs=RECOVERY_CONFIGS, *, tmp_path_factory) -> dict[str, RecoveryMetrics]`; `render_recovery_matrix(matrix) -> str`; `discover benchmark --recovery [--json]` (read-only, temp dir, like slice 1).

- [ ] **Step 1: Tests.**
```python
def test_recovery_matrix_discriminates(tmp_path_factory):
    m = run_recovery_matrix(tmp_path_factory=tmp_path_factory)
    assert m["full"].mechanism_recovered is True
    assert m["full_decoy"].mechanism_recovered is False
    assert m["no_acquire"].mechanism_recovered is False
    assert m["full_decoy"].held_out_error > m["full"].held_out_error   # decoy misses by more

def test_cli_recovery_json(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert cli.main(["benchmark", "--recovery", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["full"]["mechanism_recovered"] is True
    assert data["full_decoy"]["mechanism_recovered"] is False
    assert os.listdir(tmp_path) == []
```
- [ ] **Step 2–3: implement → pass.**
- [ ] **Step 4: Commit** (`feat(benchmark): recovery matrix + discover benchmark --recovery`).

---

### Task 7: Docs — document slice 2, reconcile the follow-up list

**Files:**
- Modify: `discovery_engine/benchmark/__init__.py` (docstring)

- [ ] **Step 1:** Add a "slice 2 — recovery track" paragraph: it drives the real `capability_gap → build_hook → advance_cycle → run → storeless-grade` loop; `mechanism_recovered`/`held_out_error` are EARNED by a run; the decoy passes the structural coverage gate but fails the behavioral-test run (the run is the arbiter); `no_acquire` produces no run. State the honest scope: toy model (not a real simulator), the oracle/build_hook is the model-builder analog (the engine never builds itself, by design), and the one remaining deferred follow-up is wiring the *acquisition-proposal* path to this capability-gap loop (shape C) + the real-LLM filler.
- [ ] **Step 2: Commit** (`docs(benchmark): document the recovery track (DE-13 slice 2)`).
