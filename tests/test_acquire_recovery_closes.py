"""Honesty gate for the acquisition→recovery wiring (Task 2).

Proves the SINGLE link added in Task 1 — ``accept_model_acquisition_proposal`` appending
the proposal's buildable mechanism to the investigation's ``required_mechanisms`` — is
enough to close the whole acquisition loop through the REAL engine chain:

    record ModelAcquisitionProposal (real acquire_model path)
      -> accept_model_acquisition_proposal  (mechanism enters required_mechanisms)
      -> run_cycle                           (engine mints a BUILD capability_gap)
      -> accept_capability_gap               (rationale-gated; authorizes a build)
      -> advance_cycle(build_hook, runner)   (build -> re-crank -> run -> store-less grade)
      -> finding.evidence.observed in band   (computed by the engine, never written here)

Causality (the negative half): a SEPARATE workspace that does NOT accept the proposal
cranks ``run_cycle`` and mints NO gap for the mechanism — so acceptance is the cause of
the gap, not the pre-existing workspace shape.

CRITICAL vs. the slice-2 ``build_recovery_scenario``: that helper PRE-DECLARES the
mechanism in ``required_mechanisms`` (so the gap fires without any acceptance, defeating
the causal test). This test builds a minimal workspace that starts WITHOUT the mechanism
in ANY ``required_mechanisms`` — only ``accept_model_acquisition_proposal`` introduces it.

Honesty: the observed value and the gap are produced by the real engine chain. Nothing
here hand-inserts the gap, the built study, or ``evidence.observed``.

Run:
  PYTHONPATH=<worktree> <de-venv>/bin/python -m pytest tests/test_acquire_recovery_closes.py -v
"""
from __future__ import annotations

import yaml

from discovery_engine import discover
from discovery_engine.benchmark import recovery_build, recovery_model, recovery_run
from discovery_engine.discover.actions import (
    accept_model_acquisition_proposal, record_model_acquisition_proposal)
from discovery_engine.investigation.import_ import investigations_dir, studies_dir
from discovery_engine.phases.model_acquisition import build_model_acquisition_proposal
from discovery_engine.study import evaluator as se

MECH = "feedback_inhibition"
BAND = recovery_model.BAND  # {"op": "range", "low": 0.3, "high": 0.8}


def _build_acquire_ws(tmp_path, *, name: str = "rec"):
    """A minimal recovery workspace that starts WITHOUT ``feedback_inhibition`` in ANY
    ``required_mechanisms`` (neither investigation nor study). Adapts
    ``recovery_scenario.build_recovery_scenario`` by dropping the pre-declared mechanism:
    only ``accept_model_acquisition_proposal`` may introduce it.

    Everything else is the recovery scenario: the store-less toy evaluator, and a gap-trigger
    study ``s1`` whose composite does not resolve (so it never runs) and which declares
    ``builds: [feedback_inhibition]`` — the catalog signal that makes a future gap derivable —
    while providing NONE of the mechanism's capability tokens (topic ``growth``), so the
    mechanism routes to BUILD once it is required."""
    se.clear_workspace_evaluator_cache()
    ws = tmp_path
    base, kind = recovery_model.DEFAULT_BASE, recovery_model.TOY_KIND
    (ws / "workspace.yaml").write_text(f"name: {name}ws\n", encoding="utf-8")

    pkg = ws / f"pbg_{name}ws"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "evaluators.py").write_text(
        recovery_model.STORELESS_EVALUATOR_SRC(kind, base), encoding="utf-8")

    study = "s1"
    inv = investigations_dir(ws) / name
    inv.mkdir(parents=True, exist_ok=True)
    # NO required_mechanisms here — the whole point of the causal test.
    (inv / "investigation.yaml").write_text(yaml.safe_dump({
        "name": name, "slug": name, "studies": [study],
    }), encoding="utf-8")

    sd = studies_dir(ws) / study
    sd.mkdir(parents=True, exist_ok=True)
    # NO required_mechanisms on the study either; builds: keeps a future gap derivable.
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4,
        "name": study,
        "topic": "growth",                    # does NOT tokenize to feedback_inhibition
        "builds": [MECH],                     # derivable gap once the mechanism is required
        "baseline": [{"composite": "json.nonexistent_mod.GrowthComposite"}],  # unresolvable
    }), encoding="utf-8")
    return ws, name


def _record_proposal(ws, slug):
    """Author + record a ModelAcquisitionProposal naming feedback_inhibition via the real
    acquire_model path (build_model_acquisition_proposal -> record_model_acquisition_proposal),
    exactly as the acquire_model oracle does. Returns its id."""
    inv_path = investigations_dir(ws) / slug / "investigation.yaml"
    proposal = build_model_acquisition_proposal(
        MECH,
        expected_behavioral_test={"behavior_test": "recover", "pass_if": {"low": 0.3, "high": 0.8}},
        change={"kind": "mechanism", "question": "what regulates the product steady state?"},
        provenance={"trigger": "needs_author", "source": "acquire_model", "by": "agent"},
    )
    rec = record_model_acquisition_proposal(inv_path, proposal)
    return rec["id"]


def _crank(ws, slug):
    """One fresh engine crank via the same cycle path the recovery test uses
    (load_cycle_inputs -> _cycle_with_evidence -> persist_cycle), then return the
    capability_gaps persisted on disk."""
    inv_path, inv, st = discover.load_cycle_inputs(ws, slug)
    res = discover._cycle_with_evidence(ws, slug, inv, st)
    discover.persist_cycle(inv_path, inv, res, studies=st)
    disk = yaml.safe_load(inv_path.read_text(encoding="utf-8"))
    return [g for g in (disk.get("capability_gaps") or []) if isinstance(g, dict)]


def _gaps_for_mech(gaps, mech=MECH):
    return [g for g in gaps if g.get("mechanism") == mech and str(g.get("id", "")).startswith("cg:")]


def _load_inv(ws, slug):
    return yaml.safe_load(
        (investigations_dir(ws) / slug / "investigation.yaml").read_text(encoding="utf-8"))


def _scalar(observed):
    if isinstance(observed, dict):
        for k in ("value", "mean", "scalar"):
            if k in observed:
                return observed[k]
        vals = [v for v in observed.values() if isinstance(v, (int, float))]
        return vals[0] if vals else observed
    return observed


def test_acquisition_loop_closes_end_to_end(tmp_path):
    """Accept a model-acquisition proposal -> the engine mints a BUILD gap -> accept it ->
    advance builds + runs the recovered study -> observed computed IN band. Every step is
    the real engine chain; nothing downstream is hand-written."""
    ws, slug = _build_acquire_ws(tmp_path)
    ma_id = _record_proposal(ws, slug)

    # Pre-accept: no mechanism required, so a fresh crank mints NO gap for it (causal link).
    assert not _gaps_for_mech(_crank(ws, slug)), "a gap appeared BEFORE acceptance"

    # (2) Accept the proposal -> the mechanism enters the investigation's required_mechanisms.
    prop = accept_model_acquisition_proposal(ws, slug, ma_id, rationale="build it")
    assert prop["status"] == "accepted"
    assert MECH in (_load_inv(ws, slug).get("required_mechanisms") or []), \
        "accept did not feed the mechanism into required_mechanisms"

    # (3) Fresh crank -> the engine now emits + persists a BUILD capability_gap for it.
    gaps = _gaps_for_mech(_crank(ws, slug))
    assert gaps, "engine did not mint a capability_gap after acceptance"
    gap = gaps[0]
    assert gap["mechanism"] == MECH
    assert gap["status"] == "proposed"

    # (4) Accept the gap (rationale-gated) and advance: build via the injected hook, re-crank,
    #     run via the injected runner, store-less grade.
    discover.accept_capability_gap(ws, slug, gap["id"], rationale="recover the acquired mechanism")
    res = discover.advance_cycle(
        ws, slug, autonomous=True,
        build_hook=recovery_build.build_hook(MECH),
        runner=recovery_run.benchmark_runner(), max_runs=4)

    # (5) The gap reached built, a run executed, and the engine-graded observed is in band.
    built_gap = next(g for g in _load_inv(ws, slug)["capability_gaps"] if g["id"] == gap["id"])
    assert built_gap["status"] == "built", f"gap did not reach built: {built_gap}"
    assert any(r["status"] == "executed" for r in res["runs"]), f"no run executed: {res['runs']}"

    recovered = studies_dir(ws) / recovery_build.built_study_slug(MECH)
    study = yaml.safe_load((recovered / "study.yaml").read_text(encoding="utf-8"))
    observed = study["findings"][0]["evidence"].get("observed")
    assert observed is not None, f"finding observation not filled by the engine: {study['findings'][0]}"
    assert BAND["low"] <= float(_scalar(observed)) <= BAND["high"], \
        f"engine-computed observed out of band {BAND}: {observed}"


def test_without_acceptance_no_gap(tmp_path):
    """Causality: a fresh workspace where the proposal is recorded but NEVER accepted mints
    NO capability_gap for the mechanism. Acceptance is the cause of the gap."""
    ws, slug = _build_acquire_ws(tmp_path)
    _record_proposal(ws, slug)  # recorded, deliberately NOT accepted

    gaps = _crank(ws, slug)
    assert not _gaps_for_mech(gaps), \
        f"a gap was minted WITHOUT acceptance (mechanism absent from required_mechanisms): {gaps}"
    # And the mechanism never entered required_mechanisms.
    assert MECH not in (_load_inv(ws, slug).get("required_mechanisms") or [])
