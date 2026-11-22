"""R1 — the unified cycle controller. `run_discovery_campaign` weaves the existing
primitives (project -> apply authorized frontier actions -> advance/execute -> grade ->
persist) into ONE bounded, recorded call, so a newly-generated study is executed and
graded and the next step reacts to its computed evidence WITHOUT any manual per-phase
command. `test_closed_loop_two_cycle` proves the same end-state is reachable, but only by
driving `revise --approve`, `revise --apply`, `advance_cycle`, and `persist_cycle` by hand;
this pins the controller that does it in one call under a budget.

Section 1 of the 0.7.2 upgrade plan (P0). Fixture + FakeRunner + FakeReader are inlined
(self-contained) so this module never imports `tests.*` — nothing stubbed in the science:
a real runs_meta row and a real store graded by compute_outcomes."""
from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import polars as pl

from discovery_engine import discover
from discovery_engine.discover.budget import Budget
from discovery_engine.study import run_registry as rr
from discovery_engine.study.io import load_yaml, save_yaml_atomic

C2D = "json.tool.main"                                           # resolves (stdlib): the 2D composite
C3D = "discovery_engine.phases.model_architect.build_catalog"   # resolves: higher-fidelity composite
PARAMS = {"membrane_bending": 1.0}
BAND = {"op": "range", "low": 0.3, "high": 0.8}


class _FakeReader:
    def __init__(self, data: dict[str, pl.DataFrame]):
        self._data = data

    def series(self, name: str) -> pl.DataFrame:
        if name not in self._data:
            raise KeyError(name)
        return self._data[name]

    def observables(self) -> list[str]:
        return list(self._data.keys())


def _reader():
    return _FakeReader({"obs.val": pl.DataFrame({
        "generation": [0] * 3, "time": [0.0, 1.0, 2.0], "abs_time": [0.0, 1.0, 2.0],
        "value": [0.4, 0.5, 0.6]})})


class FakeRunner:
    """Stands in for `vivarium-workbench run study`: a real runs_meta row + a store dir."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, ws, **_kw):
        slug = re.search(r"run study (\S+)", cmd).group(1)
        self.calls.append(slug)
        sdir = Path(ws) / "studies" / slug
        store = sdir / f"store{len(self.calls)}"
        (store / "history").mkdir(parents=True)
        rr.register_run(sdir / "runs.db", f"run-{slug}-{len(self.calls)}", status="complete",
                        params=dict(PARAMS), emitter_path=str(store))
        return {"ok": True, "returncode": 0, "tail": "done"}


def _ws(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    inv = tmp_path / "investigations" / "og"
    inv.mkdir(parents=True)
    save_yaml_atomic(inv / "investigation.yaml", {
        "name": "og", "studies": ["s3"],
        "hypotheses": [{"id": "h-s3", "study": "s3", "status": "open"}]})
    sd = tmp_path / "studies" / "s3"
    sd.mkdir(parents=True)
    save_yaml_atomic(sd / "study.yaml", {
        "name": "s3", "investigation": "og", "claim": "enclosure is a formalism artefact",
        "schema_version": 4,
        "baseline": [{"name": "baseline", "composite": C2D, "params": dict(PARAMS)}],
        "observables": ["obs.val"],
        "behavior_tests": [{"name": "enc_in_band",
                   "measure": {"kind": "range_check_per_generation", "path": "obs.val",
                               "window": "full_lineage_from_gen_0"},
                   "pass_if": dict(BAND)}],
        "runs": [{"name": "r0", "params": dict(PARAMS)}],
        "findings": [{"id": "f-s3", "statement": "2D and 3D membranes disagree",
                      "evidence": {"measure": "obs.val", "from_test": "enc_in_band",
                                   "by_topology": {"2d": 0.12, "3d": 0.71}}}]})
    return inv / "investigation.yaml", sd / "study.yaml"


def _run_campaign(tmp_path, **kw):
    runner = FakeRunner()
    with patch("viva_emitters.RunReader") as cls:
        cls.open.return_value = _reader()
        res = discover.run_discovery_campaign(
            tmp_path, "og", autonomous=True, runner=runner,
            budget=Budget(simulation_runs=4, model_builds=2, generation_calls=0),
            max_iterations=4, **kw)
    return runner, res


def test_campaign_drives_revision_to_graded_child_in_one_call(tmp_path):
    ip, sp = _ws(tmp_path)
    runner, res = _run_campaign(tmp_path)

    # ONE call auto-applied the model-revision proposal — no manual revise --approve/--apply
    spec = load_yaml(ip)
    prop = spec["revision_proposals"][0]
    assert prop["status"] in ("applied", "evaluated")
    child_slug = prop["history"][-1]["child_study"]

    # ...and auto-advanced: the child RAN (real runs_meta row) and was GRADED by
    # compute_outcomes — no manual advance_cycle call.
    assert runner.calls == [child_slug]
    child = load_yaml(tmp_path / "studies" / child_slug / "study.yaml")
    (run,) = child["runs"]
    assert run["computed_outcomes"]
    assert run["model_version"] == prop["history"][-1]["model_version"]

    # a durable ActionRecord trail: every consequential action carries a typed execution
    # status, and the run was never marked measured before runs.db confirmed it.
    assert res["actions"], "campaign must return a durable ActionRecord per action"
    assert all("execution_status" in a for a in res["actions"])
    assert any(a["execution_status"] == "completed" for a in res["actions"])

    # the cycle was persisted (no manual persist_cycle), and the run ends with a typed stop.
    assert load_yaml(ip).get("cycle_state")
    assert res["stop_reason"]


def test_campaign_is_idempotent_on_resume(tmp_path):
    """Re-running the campaign (a crash-and-resume) does not duplicate studies, runs, or
    proposals — the ActionRecord ledger makes each consequential action replay-safe."""
    ip, sp = _ws(tmp_path)
    runner1, _ = _run_campaign(tmp_path)
    studies_after_1 = sorted(p.name for p in (tmp_path / "studies").iterdir())
    props_after_1 = [p["id"] for p in load_yaml(ip)["revision_proposals"]]

    runner2, res2 = _run_campaign(tmp_path)
    studies_after_2 = sorted(p.name for p in (tmp_path / "studies").iterdir())
    props_after_2 = [p["id"] for p in load_yaml(ip)["revision_proposals"]]

    assert studies_after_2 == studies_after_1        # no duplicate child study
    assert props_after_2 == props_after_1            # no duplicate proposal
    assert runner2.calls == []                       # nothing re-run (already graded)


# --- §1 acceptance: feedback YAML reopens a closed investigation, no manual per-phase command ---

def _staged_reopen(fid="fb1"):
    return {"id": fid, "summary": {},
            "feedback": {"human_feedback_to_engine": "reconsider the verdict"},
            "actions": [{"type": "reopen", "status": "open", "verdict_status": "reopened",
                         "reason": "a new counter-result refutes the closed verdict"}]}


def _reopen_ws(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    inv = tmp_path / "investigations" / "inv"
    inv.mkdir(parents=True)
    save_yaml_atomic(inv / "investigation.yaml", {
        "name": "inv", "studies": ["s1"], "status": "closed",
        "executive": {"verdict_status": "supported"},
        "pending_feedback": [_staged_reopen()]})
    sd = tmp_path / "studies" / "s1"
    sd.mkdir(parents=True)
    save_yaml_atomic(sd / "study.yaml", {
        "schema_version": 4, "name": "s1", "investigation": "inv", "claim": "X holds"})
    return inv / "investigation.yaml"


def _noop_runner(cmd, ws, **_kw):
    return {"ok": True, "returncode": 0, "run_id": "stub", "tail": ""}


def test_campaign_applies_staged_feedback_to_reopen_closed_investigation(tmp_path):
    ip = _reopen_ws(tmp_path)
    res = discover.run_discovery_campaign(
        tmp_path, "inv", autonomous=True, runner=_noop_runner,
        budget=Budget(simulation_runs=4, model_builds=2, generation_calls=0), max_iterations=3)

    # ONE call applied the staged feedback — no manual `ingest-feedback` or `advance`.
    disk = load_yaml(ip)
    assert disk["status"] == "open"                                   # reopened
    assert disk["executive"]["verdict_status"] == "reopened"
    assert any(a.get("feedback") == "fb1" for a in disk.get("applied_feedback") or [])

    # the reopen is a typed action in the ledger, not mislabelled as a run.
    assert any(a["kind"] == "reopen" and a["execution_status"] == "executed"
               for a in res["actions"])


def test_campaign_does_not_re_apply_feedback_on_resume(tmp_path):
    ip = _reopen_ws(tmp_path)
    kw = dict(autonomous=True, runner=_noop_runner, max_iterations=3,
              budget=Budget(simulation_runs=4, model_builds=2, generation_calls=0))
    discover.run_discovery_campaign(tmp_path, "inv", **kw)
    discover.run_discovery_campaign(tmp_path, "inv", **kw)   # resume
    disk = load_yaml(ip)
    assert len([a for a in disk.get("applied_feedback") or [] if a.get("feedback") == "fb1"]) == 1


# --- the generation half: emit auto-gated GenerationRequests, fill, apply, in the same loop ---

def _gen_ws(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    inv = tmp_path / "investigations" / "demo"
    inv.mkdir(parents=True)
    save_yaml_atomic(inv / "investigation.yaml", {
        "name": "demo", "studies": ["s1"],
        "hypotheses": [{"id": "h", "study": "s1", "status": "open"}]})
    sd = tmp_path / "studies" / "s1"
    sd.mkdir(parents=True)
    save_yaml_atomic(sd / "study.yaml", {
        "schema_version": 4, "name": "s1", "investigation": "demo", "claim": "X",
        "baseline": [{"name": "b", "composite": "json.tool.main", "params": {}}],
        "observables": ["obs.val"],
        "behavior_tests": [{"name": "t",
            "measure": {"kind": "range_check_per_generation", "path": "obs.val",
                        "window": "full_lineage_from_gen_0"},
            "pass_if": {"op": "range", "low": 0.3, "high": 0.8}}],
        "runs": [{"name": "r0", "params": {}, "computed_outcomes": {"t": {"status": "within_tol"}}}],
        "findings": [{"id": "f1", "statement": None,
                      "evidence": {"from_test": "t", "observed": 0.5, "measure": "obs.val"}}]})
    return inv / "investigation.yaml"


def _fake_fill(req):
    """The injected filler seam. Authors a behavior test for an auto-gated author_tests
    request; declines everything else (so nothing is fabricated)."""
    from discovery_engine.phases.generation import GenerationResult
    if req.kind == "author_tests":
        return GenerationResult(request_id=req.id, artifact={
            "study": "s1", "tests": [
                {"name": "val_in_band", "measure": {"kind": "range_check", "path": "obs.val"},
                 "pass_if": {"low": 0.1, "high": 0.9}}]})
    return None


def test_campaign_weaves_generation_authored_tests_in_one_call(tmp_path):
    _gen_ws(tmp_path)
    res = discover.run_discovery_campaign(
        tmp_path, "demo", autonomous=True, runner=_noop_runner, fill=_fake_fill,
        budget=Budget(simulation_runs=2, model_builds=1, generation_calls=3), max_iterations=2)

    # the auto-gated author_tests request was filled + applied in the campaign loop —
    # no manual run_requests / `discover investigate`.
    s = load_yaml(tmp_path / "studies" / "s1" / "study.yaml")
    assert "val_in_band" in [t["name"] for t in s.get("behavior_tests") or []]
    assert any(a["kind"] == "generate:author_tests" and a["execution_status"] == "applied"
               for a in res["actions"])


def test_campaign_without_filler_skips_generation(tmp_path):
    """With no filler the campaign is a safe projection — it emits/advances but applies no
    generated content (the engine stays pure; generation only runs behind an injected fill)."""
    _gen_ws(tmp_path)
    res = discover.run_discovery_campaign(
        tmp_path, "demo", autonomous=True, runner=_noop_runner,
        budget=Budget(simulation_runs=2, model_builds=1, generation_calls=3), max_iterations=2)
    s = load_yaml(tmp_path / "studies" / "s1" / "study.yaml")
    assert "val_in_band" not in [t["name"] for t in s.get("behavior_tests") or []]
    assert not [a for a in res["actions"] if a["kind"].startswith("generate:")]


# --- the durable ActionRecord ledger: resume-after-crash survives a process restart ---

def _ledger(ip):
    lf = Path(ip).parent / "campaign_ledger.jsonl"
    return [json.loads(line) for line in lf.read_text().splitlines() if line.strip()]


def test_campaign_writes_a_durable_action_ledger(tmp_path):
    ip, sp = _ws(tmp_path)
    runner, res = _run_campaign(tmp_path)
    lf = Path(res["ledger_path"])
    assert lf.exists() and lf.name == "campaign_ledger.jsonl"
    recs = _ledger(ip)
    # every record carries the plan's §1 fields, durably on disk
    fields = {"id", "kind", "target", "cycle_index", "policy_decision",
              "execution_status", "input_hash", "artifacts", "depends_on"}
    assert recs and all(fields <= set(r) for r in recs)
    assert any(r["kind"] == "revision_apply" and r["execution_status"] == "applied" for r in recs)
    assert any(r["kind"] == "run" and r["execution_status"] == "completed" for r in recs)


def test_campaign_ledger_is_append_only_and_deduped_on_resume(tmp_path):
    """A resume (a fresh campaign call, as after a crash) reconstructs the budget charge from
    the ledger and appends no duplicate action — each logical action id appears exactly once."""
    ip, sp = _ws(tmp_path)
    _run_campaign(tmp_path)
    ids_after_1 = [r["id"] for r in _ledger(ip)]

    _run_campaign(tmp_path)   # resume from the durable ledger
    recs2 = _ledger(ip)
    ids_after_2 = [r["id"] for r in recs2]

    assert len(ids_after_2) == len(set(ids_after_2)), f"duplicate ids in ledger: {ids_after_2}"
    assert set(ids_after_1) == set(ids_after_2)          # resume added no new consequential action
    # the budget charge was reconstructed, not recounted from zero
    assert sum(1 for r in recs2 if r["kind"] == "run"
               and r["execution_status"] in ("completed", "refuted", "inconclusive")) == 1


# --- §1 bullet 4: the typed execution-state taxonomy (execution separated from verdict) ---

def _reader_vals(vals):
    n = len(vals)
    return _FakeReader({"obs.val": pl.DataFrame({
        "generation": [0] * n, "time": [float(i) for i in range(n)],
        "abs_time": [float(i) for i in range(n)], "value": vals})})


def _run_campaign_vals(tmp_path, vals):
    runner = FakeRunner()
    with patch("viva_emitters.RunReader") as cls:
        cls.open.return_value = _reader_vals(vals)
        res = discover.run_discovery_campaign(
            tmp_path, "og", autonomous=True, runner=runner,
            budget=Budget(simulation_runs=4, model_builds=2, generation_calls=0),
            max_iterations=4)
    return runner, res


def test_campaign_run_state_completed_when_graded_pass(tmp_path):
    _ws(tmp_path)
    _, res = _run_campaign_vals(tmp_path, [0.4, 0.5, 0.6])          # measured in-band -> PASS
    runs = [a for a in res["actions"] if a["kind"] == "run"]
    assert runs and all(a["execution_status"] == "completed" for a in runs)


def test_campaign_run_state_refuted_not_failed_when_graded_fail(tmp_path):
    _ws(tmp_path)
    _, res = _run_campaign_vals(tmp_path, [0.05, 0.1, 0.05])        # measured out-of-band -> FAIL
    runs = [a for a in res["actions"] if a["kind"] == "run"]
    # a wrong prediction is SCIENTIFICALLY REFUTED (the run succeeded) — never a failed
    # execution, and never a completion.
    assert runs and all(a["execution_status"] == "refuted" for a in runs)
    assert not any(a["execution_status"] in ("completed", "failed_execution") for a in runs)


def test_zero_simulation_budget_is_projection_only_not_exhausted(tmp_path):
    """Friction fix: a zero simulation budget is projection-only, not an exhausted one. The
    campaign still applies revision/generation work, skips advance (runs nothing), and must NOT
    report `budget_exhausted` (which falsely signals a budget ran out)."""
    ip, sp = _ws(tmp_path)
    runner = FakeRunner()
    with patch("viva_emitters.RunReader") as cls:
        cls.open.return_value = _reader()
        res = discover.run_discovery_campaign(
            tmp_path, "og", autonomous=True, runner=runner,
            budget=Budget(simulation_runs=0, model_builds=2, generation_calls=0), max_iterations=3)
    assert runner.calls == []                 # no simulation ran
    assert res["runs"] == 0
    assert res["stop_reason"] != "budget_exhausted"   # projection-only, nothing was exhausted
    assert res["applied"] >= 1                # the revision still applied (no sim budget needed)


def test_campaign_does_not_report_closed_over_provisional_evidence(tmp_path):
    """The closure-coupling friction (viva-island-biogeography): an investigation whose only claim
    rests on provisional-only evidence — a numeric finding with no registered run — must NOT stop
    `closed`, even though engine.closure reads closed (no unmet criterion / gap / challenge).
    Reporting it closed hid a live, unresolved frontier. The five-state coupling keeps the
    controller honest: a provisional-only claim is not scientifically resolved."""
    inv = tmp_path / "investigations" / "og"
    (inv / "studies" / "s1").mkdir(parents=True)
    save_yaml_atomic(inv / "studies" / "s1" / "study.yaml", {
        "name": "s1", "investigation": "og", "question": "q", "claim": "c",
        # numeric observed value but NO registered run → reconcile classifies it provisional
        "findings": [{"id": "f1", "statement": "a numeric result",
                      "evidence": {"measure": "obs.val", "observed": 0.5}}]})
    save_yaml_atomic(inv / "investigation.yaml", {
        "name": "og", "studies": ["s1"], "status": "running"})   # no criteria / gaps / challenges

    res = discover.run_discovery_campaign(
        tmp_path, "og", autonomous=True,
        budget=Budget(simulation_runs=0, model_builds=0, generation_calls=0), max_iterations=2)

    assert res["stop_reason"] != "closed"            # provisional evidence => not a closed stop
    spec = load_yaml(inv / "investigation.yaml")
    st = (spec.get("cycle_state") or {}).get("closure_states") or {}
    assert st.get("evidence_sufficient") is False
    assert st.get("scientifically_resolved") is False   # the coupling: not resolved on provisional evidence


def test_campaign_forwards_gates_to_generation(tmp_path):
    """Regression: run_discovery_campaign accepted `gates` but never forwarded it to
    generation_requests — so --free-run (and any gate policy) never reached generation, and a
    review-gated frontier (e.g. interpret_finding) was dropped before the filler (the campaign's
    0-filler-calls stall). A review GateConfig must now suppress the auto-fill the campaign would
    otherwise do on author_tests (which is auto by default); the old, gate-ignoring code filled it."""
    from discovery_engine.phases.generation import GateConfig
    _gen_ws(tmp_path)
    res = discover.run_discovery_campaign(
        tmp_path, "demo", autonomous=True, runner=_noop_runner, fill=_fake_fill,
        gates=GateConfig(default="review"),                     # forces author_tests to review
        budget=Budget(simulation_runs=2, model_builds=1, generation_calls=3), max_iterations=2)
    s = load_yaml(tmp_path / "studies" / "s1" / "study.yaml")
    assert "val_in_band" not in [t["name"] for t in s.get("behavior_tests") or []]   # gated => not filled
    assert not [a for a in res["actions"] if a["kind"].startswith("generate:")]


def test_campaign_summary_counts_generation_applies(tmp_path):
    # Honest summary: a run that applied generated artifacts (but no model revisions) must not
    # report applied:0 (which reads as "nothing happened"). `applied` stays revision-only;
    # applied_generations + applied_total surface what actually landed.
    _gen_ws(tmp_path)
    res = discover.run_discovery_campaign(
        tmp_path, "demo", autonomous=True, runner=_noop_runner, fill=_fake_fill,
        budget=Budget(simulation_runs=2, model_builds=1, generation_calls=3), max_iterations=2)
    gen_applied = [a for a in res["actions"]
                   if a["kind"].startswith("generate:") and a["execution_status"] == "applied"]
    assert res["applied_generations"] == len(gen_applied) >= 1
    assert res["applied_total"] == res["applied"] + res["applied_generations"]


def _challenge_ws(tmp_path):
    """A workspace with a PERSISTING open_challenge (a `refutes` edge), so the interpret_finding
    opportunity re-emits every iteration — interpreting a finding never retires the challenge."""
    (tmp_path / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    inv = tmp_path / "investigations" / "demo"
    inv.mkdir(parents=True)
    save_yaml_atomic(inv / "investigation.yaml", {
        "name": "demo", "studies": ["s1", "s2"],
        "hypotheses": [{"id": "h", "study": "s1", "status": "open"}]})
    for slug, parents in (("s1", []), ("s2", [{"study": "s1", "relation": "refutes"}])):
        sd = tmp_path / "studies" / slug
        sd.mkdir(parents=True)
        spec = {"schema_version": 4, "name": slug, "investigation": "demo", "claim": "X",
                "baseline": [{"name": "b", "composite": "json.tool.main", "params": {}}],
                "observables": ["obs.val"]}
        if parents:
            spec["parent_studies"] = parents
        if slug == "s1":
            spec["findings"] = [{"id": "f1", "statement": "", "evidence": {}}]
        save_yaml_atomic(sd / "study.yaml", spec)
    return inv / "investigation.yaml"


def _interpret_fill(req):
    from discovery_engine.phases.generation import GenerationResult
    if req.kind == "interpret_finding" and req.target == "claim:s1/f1":
        return GenerationResult(request_id=req.id, artifact={
            "study": "s1", "finding": "f1", "statement": "adjudicated", "explanation": "because"})
    return None


def test_campaign_applies_a_persisting_generation_target_once(tmp_path):
    # Idempotency/convergence: an applied interpretation does NOT retire the open_challenge it
    # answers (interpretation is not evidence), so the same interpret_finding re-emits each
    # iteration. The controller must apply each generation target ONCE and converge — not
    # re-interpret the same target every iteration until the budget / max_iterations runs out.
    from collections import Counter
    from discovery_engine.phases.generation import GateConfig
    _challenge_ws(tmp_path)
    res = discover.run_discovery_campaign(
        tmp_path, "demo", autonomous=True, runner=_noop_runner, fill=_interpret_fill,
        gates=GateConfig.preset("free_run"),
        budget=Budget(simulation_runs=0, model_builds=0, generation_calls=50), max_iterations=4)
    applied = [a["target"] for a in res["actions"]
               if a["kind"] == "generate:interpret_finding" and a["execution_status"] == "applied"]
    assert applied, "interpret_finding never applied — the drive did not fire"
    assert all(c == 1 for c in Counter(applied).values()), \
        f"a generation target was re-applied across iterations: {Counter(applied)}"


def test_failed_run_actionrecord_carries_the_failure_reason(tmp_path):
    # Legibility: a failed run must record WHY it failed (the runner's stderr tail), so a
    # failed_execution ActionRecord is self-diagnosing instead of a dead end for the operator.
    _ws(tmp_path)

    class FailRunner:
        def __init__(self):
            self.calls = []

        def __call__(self, cmd, ws, **_kw):
            self.calls.append(cmd)
            return {"ok": False, "returncode": 1, "run_id": None,
                    "tail": "ModuleNotFoundError: No module named 'viva_island_biogeography'"}

    res = discover.run_discovery_campaign(
        tmp_path, "og", autonomous=True, runner=FailRunner(),
        budget=Budget(simulation_runs=4, model_builds=2, generation_calls=0), max_iterations=2)
    failed = [a for a in res["actions"]
              if a["kind"] == "run" and a["execution_status"] == "failed_execution"]
    assert failed, "no failed_execution run recorded"
    assert all(a.get("detail") and "ModuleNotFoundError" in a["detail"] for a in failed)


def test_campaign_does_not_re_attempt_a_failed_run(tmp_path):
    # Converge the execute half: a run that FAILED is not re-attempted in a later iteration (even
    # while other work — the revision apply — keeps the campaign going). A transient failure is
    # retried only by re-running the campaign.
    _ws(tmp_path)

    class FailRunner:
        def __init__(self):
            self.calls = []

        def __call__(self, cmd, ws, **_kw):
            self.calls.append(cmd)
            return {"ok": False, "returncode": 1, "run_id": None, "tail": "boom"}

    runner = FailRunner()
    res = discover.run_discovery_campaign(
        tmp_path, "og", autonomous=True, runner=runner,
        budget=Budget(simulation_runs=4, model_builds=2, generation_calls=0), max_iterations=3)
    assert [a for a in res["actions"]
            if a["kind"] == "run" and a["execution_status"] == "failed_execution"]  # a run did fail
    assert len(runner.calls) == len(set(runner.calls)), \
        f"a failed run was re-attempted across iterations: {runner.calls}"
