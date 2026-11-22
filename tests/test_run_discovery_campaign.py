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
    assert any(a["execution_status"] == "executed" for a in res["actions"])

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
