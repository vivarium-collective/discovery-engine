"""Task 1 (slice-2 de-risking spike): the engine's REAL loop RECOVERS a mechanism.

The gate for the recovery-bridge track: prove the engine can build a model from a
capability gap, run it, and have the recovered observable land in the acceptance band —
honestly (the observed value is COMPUTED by the store-less toy evaluator from the BUILT
model's implemented mechanism, never hardcoded, never read from a config name or the
ground-truth value).

Chain: engine emits a ``capability_gap`` (required mechanism no catalog component provides,
route BUILD) -> accept it (rationale-gated) -> ``advance_cycle`` builds it via the injected
``build_hook`` (coverage-verified), re-cranks, dispatches the study's ``test`` (run) op via
the injected runner -> read-back (compute_outcomes store-less grade -> finding observation).

Faithful half: implemented == declared mechanism -> in band.
Decoy half:    implemented != declared (gap still "built") -> OUT of band — the chain-level
               circularity guard (structural gate can't masquerade as a scientific result).

Run:
  PYTHONPATH=<worktree> <de-venv>/bin/python -m pytest tests/benchmark/test_recovery_chain_closes.py -v
"""
from __future__ import annotations

import yaml

from discovery_engine import discover
from discovery_engine.benchmark import recovery_build, recovery_run
from discovery_engine.benchmark.recovery_scenario import build_recovery_scenario
from discovery_engine.investigation.import_ import investigations_dir, studies_dir

MECH = "feedback_inhibition"


def _mini_recovery_ws(tmp_path, implemented: str):
    sc = build_recovery_scenario(tmp_path)
    recovered_dir = studies_dir(sc.ws_root) / recovery_build.built_study_slug(MECH)
    return sc.ws_root, sc.inv_slug, recovered_dir


def _accept_gap(ws, slug):
    """Crank one cycle so the engine emits + persists the capability_gap, then accept it
    (rationale-gated) so advance_cycle's build_process op will build it."""
    inv_path, inv, st = discover.load_cycle_inputs(ws, slug)
    res = discover._cycle_with_evidence(ws, slug, inv, st)
    discover.persist_cycle(inv_path, inv, res, studies=st)
    disk = yaml.safe_load(inv_path.read_text(encoding="utf-8"))
    gaps = disk.get("capability_gaps") or []
    assert gaps, "engine did not emit a capability_gap for the required mechanism"
    cg = gaps[0]["id"]
    assert gaps[0]["mechanism"] == MECH
    discover.accept_capability_gap(ws, slug, cg, rationale="recover the concealed mechanism")
    return cg


def _load_gap(ws, slug):
    disk = yaml.safe_load((investigations_dir(ws) / slug / "investigation.yaml").read_text())
    return disk["capability_gaps"][0]


def _scalar(observed):
    if isinstance(observed, dict):
        for k in ("value", "mean", "scalar"):
            if k in observed:
                return observed[k]
        # a by_topology-style dict: take the single value
        vals = [v for v in observed.values() if isinstance(v, (int, float))]
        return vals[0] if vals else observed
    return observed


def _run_chain(tmp_path, implemented):
    ws, slug, sd = _mini_recovery_ws(tmp_path, implemented=implemented)
    _accept_gap(ws, slug)
    res = discover.advance_cycle(
        ws, slug, autonomous=True,
        build_hook=recovery_build.build_hook(implemented),
        runner=recovery_run.benchmark_runner(), max_runs=4)
    gap = _load_gap(ws, slug)
    study = yaml.safe_load((sd / "study.yaml").read_text(encoding="utf-8"))
    return res, gap, study


def test_recovery_chain_closes(tmp_path):
    """Faithful: build implements the declared mechanism -> observed computed IN band."""
    res, gap, study = _run_chain(tmp_path, implemented="feedback_inhibition")
    assert gap["status"] == "built", f"coverage gate did not pass: {gap}"
    assert any(r["status"] == "executed" for r in res["runs"]), f"no run executed: {res['runs']}"
    observed = study["findings"][0]["evidence"].get("observed")
    assert observed is not None, f"finding observation not filled: {study['findings'][0]}"
    assert 0.3 <= float(_scalar(observed)) <= 0.8, f"observed out of band: {observed}"


def test_recovery_chain_decoy_out_of_band(tmp_path):
    """Decoy: gap still 'built' (structural gate passes) but the implemented mechanism is
    substrate_depletion, whose genuine formula computes an out-of-band observable."""
    res, gap, study = _run_chain(tmp_path, implemented="substrate_depletion")
    assert gap["status"] == "built", f"structural gate should still pass: {gap}"
    assert any(r["status"] == "executed" for r in res["runs"]), f"no run executed: {res['runs']}"
    observed = study["findings"][0]["evidence"].get("observed")
    assert observed is not None, f"finding observation not filled: {study['findings'][0]}"
    assert not (0.3 <= float(_scalar(observed)) <= 0.8), (
        f"decoy must land OUT of band, got {observed}")
