"""Deterministic discovery-engine benchmark harness (DE-13), slice 1.

Scope of this slice — read before trusting a number here:

- It measures **mechanism PROPOSAL**, not predictive recovery. `mechanism_proposed` is True
  when the full engine authored + recorded (through the real coerce/record seam) a
  ``ModelAcquisitionProposal`` naming the scenario's concealed mechanism, and the
  ``no_acquire_model`` ablation (which drops the ``acquire_model`` skill) did not.
- The oracle is a **perfect** filler: it always proposes the correct mechanism, so today the
  ``mechanism == concealed_mechanism`` check cannot fail and effectively reduces to
  ``proposal_count >= 1``. It is honest (keyed on the real recorded artifact, not the config
  name) but the string match only becomes load-bearing once a **decoy-mechanism negative
  control** is added (a follow-up).
- Deterministic: no real LLM, no real simulation. A single
  ``run_cycle → propose(oracle) → apply_result`` pass; no ``advance_cycle``/``FakeRunner``.

Deferred follow-ups: the decoy negative control; the acquire→build→run bridge for true
``mechanism_recovered``/``held_out_error``; a metric that discriminates ``no_critique`` (today a
placeholder row); the real-LLM filler; and the remaining §17 matrix rows.
"""
from discovery_engine.benchmark.scenario import GroundTruth, Scenario, build_scenario

__all__ = ["GroundTruth", "Scenario", "build_scenario"]
