# Discovery-Engine Benchmark Harness (DE-13, slice 1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A deterministic, self-contained benchmark that runs the discovery-engine's closed loop on a synthetic concealed-mechanism scenario under several configurations (full engine vs ablations), and quantifies the difference — so "does the full engine do more useful scientific inference than a degraded one?" becomes a measured number, not an assertion.

**Architecture:** A new `discovery_engine/benchmark/` package layered entirely OVER existing machinery (`run_cycle`/`advance_cycle`, the revision→child→run closed loop, `DiscoveryAgent`/`SkillRegistry`, `discovery_delta`). It owns no new engine behavior: a **scenario** builds a synthetic investigation with a known concealed mechanism + held-out ground truth; an **ablation config** selects which capabilities are enabled (expressed as a `SkillRegistry` subset + engine flags); a **runner** drives the loop under a config with a deterministic oracle filler and a fake runner (no real LLM, no real simulation); **metrics** are pure functions over (final state, ground truth); a **report** renders the config×metrics matrix. The engine stays LLM-free and untouched.

**Tech Stack:** Python 3.12, frozen dataclasses, the existing `discover` API, pytest. No new third-party deps.

**Spec:** The Revised Upgrade Plan §16 (first end-to-end vertical slice) and §17 (benchmark matrix + core evaluation metrics), and `docs/upgrade-plan-0.6-reconciliation.md` DE-13 ("No harness. Needed to justify the whole effort; build alongside the first vertical slice"). The reconciliation doc is the binding authority.

## Global Constraints

- **No AI attribution, ever** (no `Co-Authored-By` / "Generated with" in commits/PR).
- **Additive only.** New `discovery_engine/benchmark/` package + its tests. Do NOT modify engine behavior in `discover/`, `projection/`, `phases/`, `agents/`. If a benchmark genuinely needs an engine hook that doesn't exist, STOP and report it (don't bend the engine to fit).
- **Deterministic + self-contained.** The benchmark must run in CI with no real LLM and no real simulation: a scripted **oracle filler** stands in for the AI across the generation seam, and a **fake runner** (the `test_closed_loop_two_cycle` FakeRunner pattern) stands in for `vivarium-workbench run study`. Two runs of the same config on the same scenario produce identical metrics.
- **The synthetic scenario is NOT a real E. coli model.** It is a minimal engine-native study whose baseline model is wrong on a held-out observable, with a known correct model_version. (A real mechanistic scenario is an explicit follow-up.)
- **Metrics are pure** functions of `(BenchmarkResult, GroundTruth)`; they never run anything.
- **Honesty:** a metric reports what it measured, never a target. `mechanism_recovered` is derived from the engine's own recorded state (a model_version transition + the disagreement closing), not asserted.
- Squash-merge to `main`; one bounded PR; CI green; never auto-merge.

## Review Focus

- **Determinism:** two runs of the same (config, scenario) must yield byte-identical metrics — no wall-clock, no RNG, no dict-ordering leakage into a metric. Tested in the runner task.
- **Ablation actually ablates:** a `no_design` / `no_hypothesize` config must demonstrably change the loop's behavior (fewer child studies / no proposal), not silently run the full loop. A config whose ablation is a no-op is a defect. Tested per ablation.
- **A metric on a degraded run must not crash:** an ablated run that recovers nothing (no child, no delta, empty proposals) must yield well-defined metrics (e.g. `mechanism_recovered=False`, `held_out_error=None`), never a KeyError. Tested with an ablation that recovers nothing.
- **The oracle filler stays an oracle, not a cheat:** it supplies a *proposal* across the real generation seam (a HypothesisProposal/CritiqueReport the engine then validates and records through the real recorders) — it must never write canonical state directly or mark anything verified. Tested by asserting recovery goes through the real revision/apply path.
- **The full config genuinely recovers** the concealed mechanism on the synthetic scenario (end-to-end), else the benchmark proves nothing. Tested as the headline end-to-end.

---

### Task 1: `GroundTruth` + the synthetic concealed-mechanism scenario

**Files:**
- Create: `discovery_engine/benchmark/__init__.py`
- Create: `discovery_engine/benchmark/scenario.py`
- Test: `tests/benchmark/test_scenario.py`

**CONTROLLING RULING (the discriminating-ablation design — read first).** The benchmark headline is "the full engine recovers the concealed mechanism; an ablation does not." For that to hold, recovery must DEPEND on the generative (hypothesize) step, not the deterministic revision pipeline. So the scenario's primary signal is an **anomaly** — a measured observable outside every live hypothesis band — that has NO revision proposal standing behind it, so the loop can only progress by: crank → a `hypothesize` request → (the oracle proposes the concealed mechanism as a rival hypothesis) → the hypothesis makes a study `discriminable` → a discriminating child study is authored + run → recovery. Removing the `hypothesize` skill breaks the chain at step one. (If, empirically, the current engine does NOT chain anomaly→hypothesize→discriminating-study→recovery, the scenario builder must report that in its report file so the controller can adjudicate — e.g. fall back to a `model_disagreement` scenario + a different discriminating ablation, or weaken the headline to a measured metric difference.)

**Interfaces:**
- Produces: `GroundTruth` (frozen dataclass) `{observable, base_value, true_value, band, concealed_mechanism, correct_composite}`; `build_scenario(tmp_path, *, name="bench") -> Scenario` where `Scenario` is `{ws_root, inv_slug, ground_truth}`. The scenario writes a real workspace on disk (investigation.yaml + one study) whose baseline model is WRONG on `observable` such that `run_cycle`'s `signals["anomalies"]` carries exactly one anomaly for `observable` (observed == `base_value`, outside `band`), and NO `revision_proposals`, so the frontier raises an `anomaly` item (reuse the `_anoms`/hypotheses-band machinery; model the finding on `test_closed_loop_two_cycle._ws` but with an anomaly, not a standing revision proposal).

- [ ] **Step 1: Write the failing test** — `build_scenario` writes a workspace; `discover.load_cycle_inputs` loads it; `run_cycle(...)` has `signals["anomalies"]` of length 1 for `ground_truth.observable`, and `de.frontier(...)` yields an `anomaly` item (and NO `revision_proposed`); `ground_truth.true_value` is in `band` and `base_value` is not.
- [ ] **Step 2: Run to verify fail** — `pytest tests/benchmark/test_scenario.py -v` → FAIL (module missing).
- [ ] **Step 3: Implement** — adapt the `_ws` fixture from `tests/test_closed_loop_two_cycle.py` into a reusable `build_scenario` (stdlib-resolvable composites, a numeric finding whose observed value is out-of-band with `evidence.from_test` so `_anoms` flags it, a `pass_if` band, a pre-run parent). Record the ground truth. Minimal + deterministic. In the report file, note the exact frontier signal the scenario raises.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** — `feat(benchmark): synthetic concealed-mechanism (anomaly) scenario + GroundTruth`

---

### Task 2: Ablation configs

**Files:**
- Create: `discovery_engine/benchmark/ablation.py`
- Test: `tests/benchmark/test_ablation.py`

**Interfaces:**
- Consumes: `discovery_engine.agents.skill_registry.default_registry` / `SkillRegistry`.
- Produces: `BenchConfig` (frozen) `{name, skills: frozenset, enable_design: bool, enable_critique: bool}` and `registry() -> SkillRegistry` (the default registry subset to `skills`). `CONFIGS` = `{full, no_hypothesize, no_critique, no_design}`. `full` enables everything; `no_hypothesize` drops the `hypothesize` skill from the registry; `no_critique` drops `critique`; `no_design` sets `enable_design=False`. Provide `BenchConfig.registry()` returning a `SkillRegistry` holding only the config's skills.

- [ ] **Step 1: Write the failing test** — `CONFIGS["full"].registry().get("hypothesize")` is not None; `CONFIGS["no_hypothesize"].registry().get("hypothesize")` is None but `get("critique")` is not; `no_design.enable_design is False` while its registry still has both skills; `full.enable_design is True`.
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** — build `SkillRegistry` subsets by filtering `default_registry()`’s skills. (If `SkillRegistry` has no constructor-from-dict, add a tiny pure helper in benchmark that wraps the filtered dict in a `SkillRegistry` — do NOT modify the agents package; construct the existing `SkillRegistry` type with the filtered mapping.)
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** — `feat(benchmark): ablation configs as skill-registry subsets`

---

### Task 3: The oracle filler + deterministic runner

**Files:**
- Create: `discovery_engine/benchmark/oracle.py`
- Create: `discovery_engine/benchmark/runner.py`
- Test: `tests/benchmark/test_runner.py`

**Interfaces:**
- Consumes: Task 1 `Scenario`, Task 2 `BenchConfig`, `discover.advance_cycle`/`run_cycle`/`revise`, the closed-loop path, and a FakeRunner (reuse the `test_closed_loop_two_cycle` FakeRunner + FakeReader pattern — factor a small `_fake_runner()` into `runner.py`).
- Produces: `oracle_filler(ground_truth) -> fill` — a deterministic `fill(request) -> GenerationResult | None` that, for a `hypothesize` request on the scenario, returns a `HypothesisProposal` whose rival hypothesis names the concealed mechanism and whose prediction's band CONTAINS `true_value` (so the study becomes `discriminable` and a discriminating child is authored toward the truth), and `None` for any kind not in the config; `BenchmarkResult` (frozen) `{config, trace: dict, final_inv: dict, delta: dict, runs: list, proposals: list, agent_calls: int}`; `run_benchmark(scenario, config, *, max_cycles=3) -> BenchmarkResult` that drives the loop THROUGH THE COORDINATOR: repeatedly (up to `max_cycles`) `run_cycle` → `opportunities` → `DiscoveryAgent(registry=config.registry()).propose(opps, fill=oracle)` → apply the validated proposals (via the real recorders / `set_hypothesis_proposal_status` accept so the hypothesis is live) → `advance_cycle(..., autonomous=True, runner=<fake>)` (which, per DE-10, autonomously authors + runs the discriminating study when `config.enable_design`) → persist → re-cycle; capturing the trace. No real LLM, no real sim.

- [ ] **Step 1: Write the failing tests** — `run_benchmark(scenario, CONFIGS["full"])` returns a `BenchmarkResult` whose `final_inv` shows a discriminating child study authored + run and the anomaly's study moved toward the truth band (recovery via the real coordinator→design→advance path); `agent_calls > 0`; two runs give equal results (determinism); `CONFIGS["no_hypothesize"]` makes zero hypothesize fills (`agent_calls == 0` for hypothesize) and authors no discriminating child (recovers nothing).
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** — `oracle.py` builds the proposal artifacts from the ground truth; `runner.py` orchestrates `run_cycle → propose(oracle) → apply/accept → advance_cycle(fake runner) → persist`, looped, and records the trace. Keep the fill seam real (proposals go through the engine's validators/recorders + the accept-to-promote path); the oracle only authors proposal content. Reuse the `test_closed_loop_two_cycle` FakeRunner + FakeReader (factor a `_fake_runner()`/`_fake_reader()` into `runner.py`). If the full config does NOT recover through this chain, STOP and report BLOCKED with the trace so the controller can adjudicate the scenario/ablation.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** — `feat(benchmark): deterministic oracle filler + loop runner`

---

### Task 4: Metrics (pure)

**Files:**
- Create: `discovery_engine/benchmark/metrics.py`
- Test: `tests/benchmark/test_metrics.py`

**Interfaces:**
- Consumes: `BenchmarkResult` (Task 3), `GroundTruth` (Task 1).
- Produces: `score(result, ground_truth) -> Metrics` (frozen dataclass), all pure: `mechanism_recovered: bool` (the correct model_version was adopted AND the disagreement/challenge closed in `final_inv`/`delta`), `held_out_error: float | None` (|final adjudicated observable − true_value|, None if never adjudicated), `converged: bool` (no `model_disagreement`/`revision_proposed`/`open_challenge` left on the final frontier), `unsupported_claim_rate: float` (findings reaching `supported`/Discovery without VERIFIED evidence ÷ claims), `false_discovery_rate: float` (Discoveries whose claim the ground truth marks wrong ÷ Discoveries; 0 when none), `run_count: int`, `agent_calls: int`.

- [ ] **Step 1: Write the failing tests** — on a synthetic `BenchmarkResult` that recovered (adopted the correct mv + closed the gap), `mechanism_recovered is True`, `held_out_error` ≈ 0, `converged is True`, `false_discovery_rate == 0`; on a degraded result (no child, empty proposals, no delta), `mechanism_recovered is False`, `held_out_error is None`, `converged is False`, and NO KeyError; a fabricated unsupported Discovery yields `unsupported_claim_rate > 0`.
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** — pure readers over `result.final_inv`/`result.delta` and `ground_truth`. Defensive on missing keys (a degraded run has none). `mechanism_recovered` reads the recorded model_version transition + closure, never re-derives science.
- [ ] **Step 4: Run to verify pass.**
- [ ] **Step 5: Commit** — `feat(benchmark): pure metrics over (result, ground_truth)`

---

### Task 5: The benchmark matrix + end-to-end + `discover benchmark`

**Files:**
- Create: `discovery_engine/benchmark/report.py`
- Modify: `discovery_engine/discover/cli.py` (a read-only `benchmark` verb)
- Test: `tests/benchmark/test_benchmark_matrix.py`

**Interfaces:**
- Consumes: Tasks 1–4.
- Produces: `run_matrix(scenario, configs=CONFIGS, *, max_cycles=3) -> dict[str, Metrics]`; `render_matrix(matrix) -> str` (a config×metric table); a `discover benchmark [--json]` CLI verb that builds the synthetic scenario in a tmp dir, runs the matrix, and prints the table (read-only; writes nothing into the user's workspace).

- [ ] **Step 1: Write the failing tests** — `run_matrix` over the default configs returns a `Metrics` per config; the **headline assertion**: `matrix["full"].mechanism_recovered is True` while `matrix["no_hypothesize"].mechanism_recovered is False` (the full engine recovers the concealed mechanism; the ablation that removes the generative step does not) — i.e. the benchmark discriminates; `render_matrix` contains each config name and the `mechanism_recovered` column; `discover benchmark --json` exits 0 and emits a config→metrics object and writes nothing to cwd.
- [ ] **Step 2: Run to verify fail.**
- [ ] **Step 3: Implement** — `run_matrix` loops configs via Task 3; `render_matrix` formats; the CLI verb wires it on a tmp scenario. Keep the verb read-only.
- [ ] **Step 4: Run to verify pass.** Then run the FULL not-slow suite to confirm no regression.
- [ ] **Step 5: Commit** — `feat(discover): 'benchmark' verb — full-vs-ablation matrix on a synthetic scenario`

---

## Deferred (NOT in this PR — follow-ups)

- A realistic mechanistic scenario (the §16 incomplete-E. coli-style model) in place of the synthetic engine-native disagreement.
- The real-LLM filler (`$VIVA_FILLER_CMD` / `claude -p`) as an alternative to the oracle, for a non-deterministic "can the AI actually recover it" run.
- The rest of the §17 matrix rows (current-0.6.0 baseline cycle; a general coding/research agent) and the full metric set (calibration, counterevidence strength, replay-determinism across processes).
- `no_critique` / `no_design` discrimination beyond presence (measuring the *value* of critique/design, not just that they ran).
