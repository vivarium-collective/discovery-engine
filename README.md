# discovery-engine

A **derive-first scientific-investigation discovery cycle engine** for the
Vivarium ecosystem. It turns an investigation (an `investigation.yaml` plus its
member `study.yaml` files) into a structured, reproducible view of what is known,
what is unresolved, what is most valuable to do next, whether a study is
materially ready to run, and how to cycle it forward — without performing the
science itself.

It is a standalone library, consumed by:

- **[viva-superpowers](https://github.com/vivarium-collective/viva-superpowers)** — the AI-agent skills that drive the engine and the workbench.
- **[vivarium-workbench](https://github.com/vivarium-collective/vivarium-workbench)** — the UI/server for investigations and studies.

## The cycle

```
run_cycle        → knowledge / argument / narrative / gates / validators / suggestions
frontier/closure → the prioritized unresolved work + the machine-checkable closure test
opportunities    → value-ranked frontier (info·impact·enable·closure − cost)
run_readiness    → is a study materially ready to produce an evaluable, rendered result?
execute_action   → carry out the next move, gated by consequence
advance_cycle    → build-if-needed and run the open questions
persist_cycle    → the slim regenerable cycle_state + persist-time narrative renewal
```

The projection is **pure** (no disk I/O) and **derive-first**: every view is
computed from the studies, then merged over any authored override — authored
wins, an empty authored value never clobbers a derived one. Same inputs always
yield the same bundle.

## Install

```bash
pip install discovery-engine          # or: uv pip install -e .
```

Depends on `viva-emitters` (reads run results) and `viva-workspace` (resolves a
workspace's layout); plus numpy, polars, ruamel.yaml, pyyaml, click.

## Use

```python
import discovery_engine as de

inv = {"studies": ["s1"], "acceptance_criteria": [{"study": "s1", "behavior": "b"}]}
studies = {"s1": {"name": "s1", "behavior_tests": [{"name": "b"}], "runs": []}}

bundle = de.run_cycle(inv, studies)          # pure projection
front  = de.frontier(bundle, inv)            # prioritized unresolved items
clo    = de.closure(bundle, inv)             # {closed, open_items, ...}
opps   = de.opportunities(bundle, inv)       # value-ranked
```

Or drive a workspace on disk via the CLI:

```bash
discover status <investigation>      # project one cycle, print the summary
discover crank <investigation>       # frontier + next action + closure
discover complete <investigation>    # the completion map (+ publishable axis)
discover advance <investigation> [--autonomous]
discover execute <investigation> [--execute] [--autonomous] [--ai-decide]
```

## Design invariants

- **Progress ⊥ verdict** — a failed execution is not a refuted hypothesis.
- **Closure ⊥ frontier** — a settled argument can still carry non-blocking work.
- **Publishable ⊥ closure** — presentation-completeness is its own axis.
- **Declaration ⇒ materialization** — a run is gated unless its composite
  imports, observables are emittable, measures resolve and viz is rendered.
- **Epistemic separation** — a validator's execution status never implies its
  scientific judgment.

## Roadmap

The engine today is strong on *assess → gate-readiness → execute → persist*. The
generative, quantitative, and provenance half of the scientific cycle (hypothesis
and prediction generation, information-value experiment design, a model builder
with immutable versions, figure critique, a quantitative evidence/belief updater,
dependency versioning, autonomy policy, scientific metrics) is tracked in
[`docs/roadmap.md`](docs/roadmap.md).
