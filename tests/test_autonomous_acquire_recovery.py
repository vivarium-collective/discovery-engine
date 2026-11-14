"""Honesty gate (Task 3): the acquisition->recovery loop runs with NO human in the accept step.

``test_acquire_recovery_closes.py`` proved the chain closes when a HUMAN calls
``accept_model_acquisition_proposal`` and ``accept_capability_gap`` directly. THIS test proves
the SAME real engine chain closes when an AI ``decider`` makes BOTH accept decisions through the
autonomy gate -- no human ``accept_*`` call appears anywhere in the test body:

    record ModelAcquisitionProposal (real acquire_model path)
      -> execute_action(accept_model_acquisition, autonomous, decider=approve, policy=allow-build)
             the DECIDER accepts -> mechanism enters required_mechanisms
      -> run_cycle                 (engine mints a BUILD capability_gap)
      -> execute_action(build_process on cg:, autonomous, decider=approve, policy=allow-build)
             the DECIDER accepts the gap (rationale-gated, [ai-driven])
      -> advance_cycle(autonomous, build_hook, runner)  (build -> re-crank -> run -> store-less grade)
      -> finding.evidence.observed in band  (computed by the engine, never written here)

The ``approve`` decider is a plain callable returning ``{"choice": "approve", "rationale": ...}``
-- a legitimate stand-in for an AI decider, NOT a bypass: the gate still requires
``autonomous=True`` AND a policy that allows the ``build`` tier (see ``discover/cycle.py`` branches
``build_process``/``accept_model_acquisition`` and ``_policy.AutonomyPolicy.allows``).

Negative half: the decider's decision MATTERS -- a ``reject`` decider declines the proposal, feeds
NO mechanism, and a subsequent crank mints NO gap, so no recovery follows.

Honesty: every accept is the decider's; the gap, the built study, and ``evidence.observed`` all
come from the real engine chain. Nothing here hand-inserts the gap, the built study, or the
observed value.

Run:
  PYTHONPATH=<worktree> <de-venv>/bin/python -m pytest tests/test_autonomous_acquire_recovery.py -v
"""
from __future__ import annotations

import yaml

from discovery_engine import discover
from discovery_engine.benchmark import recovery_build, recovery_model, recovery_run
from discovery_engine.discover._policy import AUTONOMOUS, AutonomyPolicy
from discovery_engine.discover.actions import record_model_acquisition_proposal
from discovery_engine.discover.cycle import execute_action
from discovery_engine.investigation.import_ import investigations_dir, studies_dir
from discovery_engine.phases.model_acquisition import build_model_acquisition_proposal
from discovery_engine.projection import engine as de
from discovery_engine.study import evaluator as se

MECH = "feedback_inhibition"
BAND = recovery_model.BAND  # {"op": "range", "low": 0.3, "high": 0.8}


def _approve(target, decision):
    return {"choice": "approve", "rationale": f"autonomous recovery of {MECH}"}


def _reject(target, decision):
    return {"choice": "reject", "rationale": "acquisition not warranted right now"}


def _allow_build_policy():
    """Autonomous, allowlist=None -> every consequence tier (incl. ``build``) is permitted. This
    is the same construction ``test_model_acquisition_execute.py`` uses to let the decider act."""
    return AutonomyPolicy(mode=AUTONOMOUS)


def _build_acquire_ws(tmp_path, *, name: str = "rec"):
    """Minimal recovery workspace that starts WITHOUT ``feedback_inhibition`` in ANY
    ``required_mechanisms`` (adapted verbatim from ``test_acquire_recovery_closes._build_acquire_ws``):
    only accepting the model-acquisition proposal may introduce it. The gap-trigger study ``s1``
    declares ``builds: [feedback_inhibition]`` (so a gap is derivable once the mechanism is required)
    while providing none of its capability tokens (topic ``growth``), so the mechanism routes to BUILD."""
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
    (inv / "investigation.yaml").write_text(yaml.safe_dump({
        "name": name, "slug": name, "studies": [study],
    }), encoding="utf-8")

    sd = studies_dir(ws) / study
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4,
        "name": study,
        "topic": "growth",
        "builds": [MECH],
        "baseline": [{"composite": "json.nonexistent_mod.GrowthComposite"}],
    }), encoding="utf-8")
    return ws, name


def _record_proposal(ws, slug):
    """Author + record a ModelAcquisitionProposal naming feedback_inhibition via the real
    acquire_model path (build_model_acquisition_proposal -> record_model_acquisition_proposal)."""
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
    """One fresh engine crank (load_cycle_inputs -> _cycle_with_evidence -> persist_cycle), then
    return the capability_gaps persisted on disk."""
    inv_path, inv, st = discover.load_cycle_inputs(ws, slug)
    res = discover._cycle_with_evidence(ws, slug, inv, st)
    discover.persist_cycle(inv_path, inv, res, studies=st)
    disk = yaml.safe_load(inv_path.read_text(encoding="utf-8"))
    return [g for g in (disk.get("capability_gaps") or []) if isinstance(g, dict)]


def _opportunities(ws, slug):
    """Value-ranked opportunities from the same cycle path the engine drives."""
    _, inv, st = discover.load_cycle_inputs(ws, slug)
    cyc = discover._cycle_with_evidence(ws, slug, inv, st)
    return de.opportunities(cyc, inv)


def _gaps_for_mech(gaps, mech=MECH):
    return [g for g in gaps if g.get("mechanism") == mech and str(g.get("id", "")).startswith("cg:")]


def _load_inv(ws, slug):
    return yaml.safe_load(
        (investigations_dir(ws) / slug / "investigation.yaml").read_text(encoding="utf-8"))


def _proposal(ws, slug, ma_id):
    return next(p for p in _load_inv(ws, slug).get("model_acquisition_proposals") or []
                if p.get("id") == ma_id)


def _gap(ws, slug, gid):
    return next(g for g in _load_inv(ws, slug).get("capability_gaps") or [] if g.get("id") == gid)


def _scalar(observed):
    if isinstance(observed, dict):
        for k in ("value", "mean", "scalar"):
            if k in observed:
                return observed[k]
        vals = [v for v in observed.values() if isinstance(v, (int, float))]
        return vals[0] if vals else observed
    return observed


def test_acquisition_recovery_runs_autonomously(tmp_path):
    """The DECIDER (not a human) accepts the proposal AND the resulting gap; the real engine chain
    then builds + runs the recovered study to an in-band observed value. No direct
    ``accept_model_acquisition_proposal`` / ``accept_capability_gap`` call appears below."""
    ws, slug = _build_acquire_ws(tmp_path)
    ma_id = _record_proposal(ws, slug)

    # (2) Crank the frontier -> the proposed acquisition surfaces as a model_acquisition opportunity.
    opps = _opportunities(ws, slug)
    ma_opp = next((o for o in opps if o.get("id") == f"model_acquisition:{ma_id}"), None)
    assert ma_opp is not None, f"no model_acquisition opportunity for {ma_id}: {[o.get('id') for o in opps]}"
    assert ma_opp["action_type"] == "accept_model_acquisition" and ma_opp["source"] == ma_id

    # Pre-accept: mechanism not yet required, so a fresh crank mints NO gap (causal baseline).
    assert not _gaps_for_mech(_crank(ws, slug)), "a gap appeared BEFORE the decider accepted"

    # (3) The DECIDER accepts the proposal through the autonomy gate -- NOT a human accept call.
    rec = execute_action(ws, slug, autonomous=True, decider=_approve,
                         target=ma_id, action="accept_model_acquisition",
                         policy=_allow_build_policy())
    assert rec["status"] == "executed", f"decider accept did not execute: {rec}"
    assert rec["decision"]["actor"] == "discovery-ai", f"accept was not AI-driven: {rec['decision']}"
    assert _proposal(ws, slug, ma_id)["status"] == "accepted", "proposal not accepted by the decider"
    assert MECH in (_load_inv(ws, slug).get("required_mechanisms") or []), \
        "decider accept did not feed the mechanism into required_mechanisms"

    # (4) Fresh crank -> the engine mints + persists a BUILD capability_gap for the mechanism.
    gaps = _gaps_for_mech(_crank(ws, slug))
    assert gaps, "engine did not mint a capability_gap after the decider accepted"
    gap = gaps[0]
    assert gap["mechanism"] == MECH and gap["status"] == "proposed"

    #     The DECIDER accepts THAT gap too (rationale-gated) -- again NO human accept call.
    grec = execute_action(ws, slug, autonomous=True, decider=_approve,
                         target=gap["id"], action="build_process",
                         policy=_allow_build_policy())
    assert grec["status"] == "executed", f"decider gap-accept did not execute: {grec}"
    assert grec["decision"]["actor"] == "discovery-ai", f"gap accept was not AI-driven: {grec['decision']}"
    accepted_gap = _gap(ws, slug, gap["id"])
    assert accepted_gap["status"] == "accepted", f"gap not accepted by the decider: {accepted_gap}"
    assert "[ai-driven]" in (accepted_gap.get("accept") or {}).get("rationale", ""), \
        f"gap accept rationale not AI-driven: {accepted_gap.get('accept')}"

    # (5) Advance: build via the injected hook, re-crank, run via the injected runner, store-less grade.
    res = discover.advance_cycle(
        ws, slug, autonomous=True,
        build_hook=recovery_build.build_hook(MECH),
        runner=recovery_run.benchmark_runner(), max_runs=4)

    built_gap = _gap(ws, slug, gap["id"])
    assert built_gap["status"] == "built", f"gap did not reach built: {built_gap}"
    assert any(r["status"] == "executed" for r in res["runs"]), f"no run executed: {res['runs']}"

    recovered = studies_dir(ws) / recovery_build.built_study_slug(MECH)
    study = yaml.safe_load((recovered / "study.yaml").read_text(encoding="utf-8"))
    observed = study["findings"][0]["evidence"].get("observed")
    assert observed is not None, f"finding observation not filled by the engine: {study['findings'][0]}"
    assert BAND["low"] <= float(_scalar(observed)) <= BAND["high"], \
        f"engine-computed observed out of band {BAND}: {observed}"


def test_decider_reject_blocks_recovery(tmp_path):
    """The decider's decision matters: a ``reject`` declines the proposal, feeds NO mechanism, and a
    subsequent crank mints NO gap -- no recovery follows. No human accept call anywhere."""
    ws, slug = _build_acquire_ws(tmp_path)
    ma_id = _record_proposal(ws, slug)

    rec = execute_action(ws, slug, autonomous=True, decider=_reject,
                         target=ma_id, action="accept_model_acquisition",
                         policy=_allow_build_policy())
    assert rec["status"] == "executed", f"decider reject did not execute: {rec}"
    assert _proposal(ws, slug, ma_id)["status"] == "declined", "reject did not decline the proposal"
    assert MECH not in (_load_inv(ws, slug).get("required_mechanisms") or []), \
        "a declined proposal still fed the mechanism"

    gaps = _crank(ws, slug)
    assert not _gaps_for_mech(gaps), f"a gap was minted despite the decider rejecting: {gaps}"
