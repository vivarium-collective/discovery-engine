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
from discovery_engine.benchmark import recovery_build, recovery_model, recovery_run
from discovery_engine.investigation.import_ import investigations_dir, studies_dir
from discovery_engine.study import evaluator as se

SLUG = "recovery"
MECH = "feedback_inhibition"
BASE = recovery_model.DEFAULT_BASE
TOY_KIND = recovery_model.TOY_KIND


def _mini_recovery_ws(tmp_path, implemented: str):
    """Smallest workspace that (a) declares required_mechanisms:[feedback_inhibition],
    (b) has a catalog entry (study s1) that does NOT provide it, so the gap routes to BUILD,
    (c) a pbg_<ws>/evaluators.py from recovery_model.STORELESS_EVALUATOR_SRC registering the
    store-less toy kind. The study UNDER TEST — whose single behavior-test is the store-less toy
    kind, whose finding links evidence.from_test, and which carries the ``implements:`` the
    evaluator reads — is MATERIALIZED by the build hook (built-<mechanism>), because the engine
    ranks a ``test`` op above a ``build_process`` op: a study present before the build would run
    first (mechanism absent) and be marked seen. Returns the (future) recovered-study dir."""
    se.clear_workspace_evaluator_cache()
    ws = tmp_path
    (ws / "workspace.yaml").write_text("name: recws\n", encoding="utf-8")

    pkg = ws / "pbg_recws"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "evaluators.py").write_text(
        recovery_model.STORELESS_EVALUATOR_SRC(TOY_KIND, BASE), encoding="utf-8")

    inv = investigations_dir(ws) / SLUG
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump({
        "name": SLUG, "slug": SLUG, "studies": ["s1"],
        "required_mechanisms": [MECH],
    }), encoding="utf-8")

    # s1: the gap TRIGGER — a catalog entry whose composite does NOT cover feedback_inhibition
    # (topic "growth", top pkg not importable -> run-ready, no behavior test / finding).
    sd1 = studies_dir(ws) / "s1"
    sd1.mkdir(parents=True)
    (sd1 / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4,
        "name": "s1",
        "topic": "growth",                           # does NOT tokenize to feedback_inhibition
        "required_mechanisms": [MECH],
        "builds": [MECH],                            # derivable gap (accept authorizes the build)
        "baseline": [{"composite": "pkg.mod.GrowthComposite"}],
    }), encoding="utf-8")

    recovered_dir = studies_dir(ws) / recovery_build.built_study_slug(MECH)
    return ws, SLUG, recovered_dir


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
