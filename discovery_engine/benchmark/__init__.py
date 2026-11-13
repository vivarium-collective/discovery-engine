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
- A second axis, ``overreach_critiqued``, discriminates ``no_critique``. The scenario's study
  authors a ``gate_status`` that outruns its evidence, so the cycle raises a
  verdict_overreach signal → a ``critique`` request the oracle answers with a CritiqueReport.
  ``full`` records it (True); ``no_critique`` (which drops the ``critique`` skill) cannot
  (False). Keyed on the recorded ``critique_reports`` artifact, same as the mechanism axis.
- Deterministic: no real LLM, no real simulation. A single
  ``run_cycle → propose(oracle) → apply_result`` pass; no ``advance_cycle``/``FakeRunner``.

Deferred follow-ups: the acquire→build→run bridge for true
``mechanism_recovered``/``held_out_error``; the real-LLM filler; and the remaining §17 matrix
rows. ``overreach_critiqued`` keys on the *presence* of a critique, not its substance — a
content-quality check on the critique is a further follow-up.
"""
from discovery_engine.benchmark.scenario import GroundTruth, Scenario, build_scenario

__all__ = ["GroundTruth", "Scenario", "build_scenario"]
