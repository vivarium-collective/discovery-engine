"""Deterministic discovery-engine benchmark harness (DE-13), slice 1.

Scope of this slice — read before trusting a number here:

- It measures **mechanism PROPOSAL**, not predictive recovery. `mechanism_proposed` is True
  when the full engine authored + recorded (through the real coerce/record seam) a
  ``ModelAcquisitionProposal`` naming the scenario's concealed mechanism, and the
  ``no_acquire_model`` ablation (which drops the ``acquire_model`` skill) did not.
- The ``mechanism == concealed_mechanism`` check is **load-bearing**, proven by the
  ``full_decoy`` negative control: that row keeps the full registry but swaps in a decoy
  oracle that records a well-formed proposal naming the *wrong* mechanism. It has the same
  ``proposal_count`` as ``full`` yet ``mechanism_proposed`` is False — so the metric is
  discriminating on the mechanism string, not merely counting proposals. The default
  (``full``) oracle remains a **perfect** filler (always the correct mechanism); scoring is
  keyed on the real recorded artifact, never the config name.
- Deterministic: no real LLM, no real simulation. A single
  ``run_cycle → propose(oracle) → apply_result`` pass; no ``advance_cycle``/``FakeRunner``.

Deferred follow-ups: the acquire→build→run bridge for true
``mechanism_recovered``/``held_out_error``; a metric that discriminates ``no_critique`` (today a
placeholder row); the real-LLM filler; and the remaining §17 matrix rows.
"""
from discovery_engine.benchmark.scenario import GroundTruth, Scenario, build_scenario

__all__ = ["GroundTruth", "Scenario", "build_scenario"]
