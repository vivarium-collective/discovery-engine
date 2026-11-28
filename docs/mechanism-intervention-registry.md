# Mechanism interventions and the mechanism-variant registry

**Status:** Draft — for review (Eran). **Authors:** cross-session design (growth-rate-control/ppGpp worked example from the Maya session; shape + gates from the discovery-engine session). **Scope:** a new study-intervention kind plus a promotion target that lives in the simulator's variant framework (v2ecoli) and is *cited* by the discovery engine.

> This note proposes making "runtime monkeypatch as a model intervention" a first-class, reproducible, promotable part of the discovery cycle, and defines the artifact shapes and the gates that keep it honest. It is grounded in a real worked example (the ppGpp growth-rate-control investigation), which is used throughout as the seed registry — and which, at the time of writing, is itself a live demonstration of the experiment→reconfirm gap the gates exist to enforce.

---

## 1. Motivation

A large fraction of model-building is **mechanism-level iteration**: "what if this one kinetic term behaved differently?" Today that is done one of two ways:

1. **Edit source + rebuild.** Correct and reproducible, but slow — a full rebuild (and, for whole-cell models, a ParCa cache rebuild) per hypothesis. It does not fit the inner loop of a discovery cycle.
2. **Runtime monkeypatch** — wrap the real simulator function, alter one mechanism, run against a *fixed* baseline cache, no rebuild. Fast (because the patched function is outside the cache's `INPUT_FILES`, so the patch takes effect without invalidating the cache), and each patch is a small, readable statement of one hypothesis. In the ppGpp investigation this turned ~8 distinct mechanistic hypotheses in a day into a quantified root cause and a candidate fix.

The problem is that (2) is an **untracked scratchpad**: the mutation is not a declared input, so the run is not reproducible, the A/B is matched only by the author's discipline, and a validated result has no defined path into the production model. This note formalizes (2) so it keeps its speed while becoming **declared, reproducible, and promotable** — and defines the promotion target so a validated mechanism lands as a durable, switchable, cited object rather than an opaque merge.

The central claim: **formalizing the monkeypatch does not weaken the reproducible-rerun contract — it strengthens it.** The contract is "rerun from *declared* inputs." A scratchpad patch cuts against it because the mutation is undeclared; a declared patch (text + target symbol + pinned baseline) makes `rerun = re-apply(patch, pinned baseline)`, which is exactly reproducible.

---

## 2. Three objects

The lifecycle produces three distinct artifacts. The first is the experiment; the second and third are the promotion target.

### 2.1 `mechanism_patch` — the experiment (a study intervention)

A new **intervention kind** on a study (today's `interventions:` are parameter / knockdown / textual; this adds code/mechanism level), applied by the study runner as a runtime overlay after composite construction and before simulate.

```yaml
interventions:
  - name: synth-cap-0.040
    kind: mechanism_patch
    target: <fully-qualified symbol>          # the runner resolves + wraps; prefer the FINEST
                                              #   symbol that isolates the mechanism (see §4)
    hypothesis: "<the mechanism change, in words>"
    fidelity: clean_wrap | replicates_internals   # see §4
    patch: |                                  # wrapper source = the executable hypothesis
      def wrap(orig): ...
    baseline:
      composite: <id>@<commit>                # pin the COMMIT, not just the id (G3)
      parca_cache: {condition -> hash}        # a PANEL, not one cache (cross-condition claims)
    validated_in: [spot_maintenance]          # the patch/toggle config this ran ON TOP of (§ B)
    provenance:
      status: experiment                      # experiment | requires_seam_refactor
                                              #   | reconfirmed_source | promoted
      study: grc-05
      finding: "basal variance 2.02x lower, >90uM excursions 0.0%"
```

The patch **is** the hypothesis in executable form — a mechanism-level diff of the model. Because the baseline cache and seeds are fixed, every A/B against it is **matched by construction**.

### 2.2 `mechanism_toggle` — the promotion target (NOT a merge)

A validated patch graduates to a **named, default-off, versioned toggle** in the simulator's variant registry — *not* a one-way merge into source. Default-off means the all-off configuration reproduces the faithful upstream (wcEcoli) baseline, so the original is never lost: the baseline is always one flag away.

```yaml
# registry object (lives in the simulator's variant framework — see §6)
toggles:
  - name: rela_synthesis_cap
    impl_version: <commit/semver>             # pin — an old study's "after" arm must not drift (G3)
    default: off                              # off = faithful wcEcoli
    param: <value | {condition -> value} | declared function>   # may be condition-dependent (§ D)
    validated_in: {spot_maintenance: on}      # the config it was CERTIFIED against — not blanket (§3)
    provenance: {study: grc-05, before_after: "...", status: experiment}
```

A toggle's verdict is **capped at experiment-grade until a source-build reconfirm run exists** (G2).

### 2.3 `mechanism_profile` — the citable current-best config

A profile is a **co-validated set of toggles** with explicit acceptance criteria. It is the thing studies and composites actually *select*. Crucially, a profile is **investigation-scoped, not study-scoped** — its criteria aggregate across member studies — and it is **co-validated, never assembled from winning atoms**.

```yaml
# investigation/registry-level object
profiles:
  - name: faithful-wcecoli
    on_set: []                                # all-off = upstream fidelity
  - name: best-2026-11
    on_set: [spot_maintenance, rela_synthesis_cap]
    certified_against:                        # what the profile CLAIMS (selection of existing gates)
      - grc-01: ppgpp-setpoint-stable-over-lineage    # bounded setpoint over >=8 gens
      - grc-03: ribosome-fraction-linear              # growth law preserved vs vEcoli (Scott-Hwa)
      - grc-05: restoring-balance                     # ensemble variance <= target (epsilon declared)
    provenance:
      reconfirmed_source: false
      co_validated_run: {criterion -> run-id}   # a run-SET keyed by criterion (§ F), from source
```

"Profile certified" ≡ **the union of its selected gates is green on the co-validated run-set.** No new assertion language — `certified_against` is a *selection of existing* `expected_behavior` names across member studies.

---

## 3. Why the registry beats a bare merge (reproducibility)

A merge makes before/after reproducible only by checking out two commits, and it mutates the baseline, so upstream fidelity is lost. A default-off toggle makes before/after the **same binary**, flag off vs on. It wins on all three:

- **before/after from source** — the A/B is flag off/on, no checkout-old-commit.
- **matched-null from source** — same cache, same seeds, one mechanism changed is matched by construction. (Gate the comparison on the *ensemble statistic* with a declared epsilon, never a flagship seed.)
- **baseline never lost** — the faithful wcEcoli baseline is the all-off corner, always one flag away.

The one thing a merge gets for free is a single coherent model (no combinatorics). The registry must buy that back explicitly — see §3's interaction rule and profiles.

**Interaction is not hypothetical.** In the ppGpp case the mechanisms are a nested stack, not independent: `spot_maintenance` is the base; both `ultrasensitive hydrolysis` and `rela_synthesis_cap` were validated *on top of* maintain-SpoT. There is no "all-off plus one" result for `rela_synthesis_cap`. So each toggle's provenance **must** record the config it was validated in (`validated_in`), and a toggle is certified only for that config. And because `rela_synthesis_cap` beats `ultrasensitive` over the same base, whether a profile of `{spot_maintenance, rela_synthesis_cap, ultrasensitive}` helps or double-counts is an **un-run question** — you cannot assemble the best profile from winning atoms; you must run the candidate on-set (which is exactly what G2's profile-level reconfirm forces).

---

## 4. Fidelity and the seam-refactor sub-step

Patches are not always clean wraps, and this changes promotion cost sharply.

- **`clean_wrap`** — the patch calls `orig` and adjusts its result (e.g. `spot_maintenance` floors a value). Cheap to promote: the toggle is a thin guard around the existing symbol.
- **`replicates_internals`** — the quantity the patch intercepts is a *mid-function intermediate*, so the patch **copies** source internals to reach it (synth-cap replicates ~40 lines of the `frac_rela` cooperative computation to cap it, then scales `k_RelA` and delegates). Two consequences: (1) the patch ships a **copy of source that is not the hypothesis** and goes stale *silently* if the real internals drift — G3's commit-pin catches the composite drifting but **not** the patch's own copied logic, so for a `replicates_internals` patch **G2 (reconfirm from source) is the only gate that catches it**; (2) a clean toggle cannot exist until the source grows a **seam** (extract the intermediate as its own symbol).

So `requires_seam_refactor` is an explicit status between `experiment` and `reconfirmed_source`, and the `target` field should prefer the finest symbol that isolates the mechanism (e.g. `frac_rela`, not the whole function) to minimize how much a patch must replicate.

---

## 5. The three enforced gates

These are **runner/evaluator-enforced**, not honor-system notes.

- **G1 — cache-boundary assert (fail-loud).** Before applying a `mechanism_patch`, the runner asserts `target ∉ baseline.parca_cache.INPUT_FILES` and **hard-fails** otherwise. This is the only thing that makes cache-reuse valid, so it cannot be a trusted flag.
- **G2 — reconfirm from source, at the PROFILE level.** A validated patch is never the production artifact. Promotion requires a real source build whose run reproduces the result; for a profile, the *whole on-set* is run from source as a unit (stacked monkeypatches are not a build, and interaction is exactly what a per-toggle reconfirm misses). The reconfirm run is "earned" — it must actually run and match before the verdict graduates.
- **G3 — pins.** `baseline.composite@commit` + toggle `impl_version` + profile `co_validated_run`-set. Without the commit/version pins a rerun months later re-applies a patch or re-selects a toggle onto drifted source and silently diverges.

---

## 6. Ownership

The registry lives in the **simulator's variant framework (v2ecoli)**; the discovery engine **cites** it. The toggles' knobs are the simulator's code, and — per §4 — ownership includes the **seam-refactor architecture** that makes a mechanism cleanly toggleable, whose cost varies per mechanism (cheap for `spot_maintenance`, a real source refactor for `synth-cap`). So "v2ecoli owns the registry" is bigger than adding flags: it owns the architecture that makes mechanisms switchable.

The discovery engine's study schema gains only: the `mechanism_patch` intervention kind, and a profile/toggle *selector* on a study arm. `certified_against` reuses the existing study gate machinery (§2.3) — no new assertion language.

---

## 7. Worked example — the ppGpp seed registry (a live experiment→reconfirm demonstration)

The growth-rate-control investigation produced three mechanisms. **None is a finished toggle** — all are still patch-level, which is the point: the seed registry demonstrates the gaps the gates enforce.

| mechanism | study | status | fidelity | result |
|---|---|---|---|---|
| `spot_maintenance` (floor SpoT so it cannot decay) | grc-02 | experiment | clean_wrap | cures the runaway; the base of the stack |
| `ppgpp_hydrolysis: ultrasensitive` (super-linear v_deg, n=4) | grc-04 | experiment | — | basal variance 1.85x, >90uM excursions 6.0% |
| `rela_synthesis_cap` (cap v_rela per uncharged tRNA) | grc-05 | experiment / requires_seam_refactor | replicates_internals | basal variance 2.02x, >90uM excursions 0.0% — but see below |

### 7.1 `synth-cap` is a missing-mechanism discovery, not a tuned hack

The naive cap (`v_rela/uncharged ≤ 0.040`) was selected over 0.045 (neutral) and 0.037 (setpoint sagged to 68) by a tuning sweep. But the growth-law gate showed **0.040 does not hold universally** — it is per-condition:

| condition | setpoint | note |
|---|---|---|
| with_aa | 22.8 (±3.5) | tight, correct low |
| basal | 62.3 (±6.7) | bounded — the 2.02x win |
| succinate | 72.1 (±10.3) | ok |
| **acetate** | **108.7 (±17.9)** | **fails — not controlled** |

Nutrient ordering is preserved (23 < 62 < 72 < 109) but acetate runs high and noisy. The mechanism: the cap is a **ratio** to uncharged tRNA, which is *high* in poor carbon (~12–16 vs basal ~9.5), so the cap is *loose* exactly where it should tighten — the condition-adaptivity backfires. (The growth-law R² extractor erroring to `None` is a tooling artifact, not a result; the acetate setpoint is the clear signal.)

Investigating *why* surfaced the real finding: **the whole-cell reconstruction has RelA's ppGpp *accelerator* but omits the counterbalancing *brake*.**

- **Accelerator (present, and genuine biology):** ppGpp inhibits elongation → ribosomes stall on uncharged tRNA → RelA fires more (Shyp 2012, positive allosteric feedback). So grc-05's "positive feedback" is *real biology*, not a model bug — we are not fixing real biology, we are adding its missing counterweight.
- **Brake (absent, code-verified):** ppGpp-driven GTP-substrate depletion (Kriel 2012; Wang 2020, Mol Cell). ppGpp lowers GTP both by consuming it and by inhibiting GTP-biosynthesis enzymes (Gmk/GuaB); GTP is RelA's substrate, so high ppGpp starves RelA → synthesis self-limits. Verified missing in the model: RelA's rate has no GTP term (`v_rela = k_RelA·rela·frac_rela`), and the ppGpp→metabolism coupling is growth-objective only, with no ppGpp regulation of Gmk/GuaB (which exist, unregulated).
- **Poor-carbon-aware by construction:** the brake keys on `[ppGpp]` (the product), not uncharged tRNA. The ratio-cap failed on acetate precisely because it keyed on the *wrong* variable.

The fix — recorded in investigation `growth-rate-control`, study
`grc-05-divergence-onset-positive-feedback`, as finding **grc05-f4** (the *cure*)
against discovery **grc05-f3** (the *missing brake*); the earlier degradation-side
`ultrasensitive` approach (grc-04) was folded in as the compared arm it supersedes —
adds the brake as a real self-limitation: **self-limiting via ppGpp → GTP-depletion
→ RelA-substrate-limitation** (Kriel 2012; cooperative Gmk inhibition, Wang 2020; the
accelerator is genuine biology, Shyp 2012). Its `param:` is therefore **not** a
per-condition table but **one coupling constant `KI = 90`**, keyed on `[ppGpp]` —
condition-aware by construction. `requires_seam_refactor` is concrete: two composite
changes — (a) model ppGpp inhibition of Gmk/GuaB in metabolism, (b) add GTP-substrate
saturation to the RelA rate.

**Validated as a mechanism** — status still `experiment` (a runtime mechanism-patch,
pending a source-build reconfirm + a multi-seed panel): the brake bounds `[ppGpp]`
across media, preserves the set-point ordering, **and** preserves the Scott-Hwa growth
law the grc-03 gate requires — R² = 0.999 with acetate correctly slowest (vs the
earlier `k_RelA ×0.6` interim, which *regressed* the law by wrongly speeding acetate
up). Because it keys on the product `[ppGpp]` rather than uncharged tRNA, it self-scales
per condition without a per-condition knob, exactly where the ratio-cap failed.
Residuals kept for honesty: acetate is still noisy (std 20), and the poor-carbon
growth-rate range is compressed vs vEcoli — a separate metabolism axis, not the ppGpp
loop. (References: `Kriel2012`, `Shyp2012`, `Wang2020ppGppGmk` in the workspace
`references/papers.bib`.)

This is the worked example at full strength: a **missing-mechanism discovery**
(accelerator without brake) that yielded a **biologically-interpretable, condition-aware
fix** passing the growth-law gate — the experiment → mechanism → reconfirm arc the
registry is for. It still carries its honest gaps (experiment→reconfirm,
requires-seam-refactor), but the "universal scalar param" problem is now *resolved by
the mechanism*: the single `KI` is condition-aware by construction, not a tuned table.

---

## 8. Open questions (decisions for review)

1. **Registry ownership boundary.** This note takes "v2ecoli owns the registry + seam architecture, DE cites it." Confirm — and confirm that the per-mechanism seam-refactor cost is acceptable as part of that ownership.
2. **Profile certification reuses the gate machinery.** This note takes `certified_against` = a selection of existing `expected_behavior` names, with "certified" = that union green on the co-validated run-set. Confirm no new assertion language is wanted.
3. **Where the `mechanism_patch` intervention schema lands** in the DE study spec, and whether the profile selector on a study arm is a new field or an extension of the existing variant selector.

## 9. Relationship to existing work

- **Reproducible-rerun contract** — §1/§3: this strengthens it (declared, pinned, re-appliable inputs; baseline one flag away).
- **Run-earned pattern** — G2's reconfirm run is earned (must actually run and match).
- **reviewer_clarity** — a mechanism still at `experiment` status is not machine-reconfirmed; the "not yet source-validated" gap is honest and surfaced, not hidden.
- **Matched-null controls** — fall out by construction, gated on the ensemble statistic with a declared epsilon.
