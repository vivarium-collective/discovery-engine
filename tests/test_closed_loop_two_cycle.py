"""Task 14 (Wave-3 exit gate): the loop CLOSES. An S3-style 2D-vs-3D model disagreement ->
revision proposal -> approve+apply (child study) -> advance runs it (real runs_meta row + real
store graded by compute_outcomes, nothing stubbed) -> adjudication -> next cycle names the child
+ the model-version transition and the proposal is `evaluated`.  Decision spec section 4."""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import polars as pl

from discovery_engine import discover
from discovery_engine import discovery_engine as de
from discovery_engine import evaluation as ev
from discovery_engine import run_registry as rr
from discovery_engine.study_io import load_yaml, save_yaml_atomic

C2D = "json.tool.main"                                   # resolves (stdlib): the 2D composite
C3D = "discovery_engine.model_architect.build_catalog"   # resolves: the higher-fidelity composite
PARAMS = {"membrane_bending": 1.0}
BAND = {"op": "range", "low": 0.3, "high": 0.8}


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
        # already run at its current mv -> advance must not re-run the parent
        "runs": [{"name": "r0", "params": dict(PARAMS)}],
        "findings": [{"id": "f-s3", "statement": "2D and 3D membranes disagree",
                      "evidence": {"measure": "obs.val", "from_test": "enc_in_band",
                                   "by_topology": {"2d": 0.12, "3d": 0.71}}}]})
    return inv / "investigation.yaml", sd / "study.yaml"


class FakeRunner:
    """Stands in for `vivarium-workbench run study`: a real runs_meta row + a store dir."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, ws):
        slug = re.search(r"run study (\S+)", cmd).group(1)
        self.calls.append(slug)
        sdir = Path(ws) / "studies" / slug
        store = sdir / f"store{len(self.calls)}"
        (store / "history").mkdir(parents=True)
        rr.register_run(sdir / "runs.db", f"run-{slug}-{len(self.calls)}", status="complete",
                        params=dict(PARAMS), emitter_path=str(store))
        return {"ok": True, "returncode": 0, "tail": "done"}


def _reader():
    from tests.test_computed_outcomes import FakeReader
    return FakeReader({"obs.val": pl.DataFrame({
        "generation": [0] * 3, "time": [0.0, 1.0, 2.0], "abs_time": [0.0, 1.0, 2.0],
        "value": [0.4, 0.5, 0.6]})})


def _cycle(ws):
    from discovery_engine import evidence_integrity as ei
    ip, inv, studies = discover.load_cycle_inputs(ws, "og")
    evs = ei.reconcile_evidence_state(inv, studies, discover._load_runs_by_study(ws, "og", studies))
    return ip, inv, studies, evs, discover.run_cycle(inv, studies, evidence_state=evs)


def _through_advance(tmp_path):
    ip, sp = _ws(tmp_path)

    # ---- cycle 1: the disagreement yields exactly one revision proposal on the frontier
    ip, inv, studies, evs, cyc = _cycle(tmp_path)
    (item,) = [i for i in de.frontier(cyc, inv) if i["kind"] == "revision_proposed"]
    pid = item["target"]
    discover.persist_cycle(ip, inv, cyc, studies=studies, evidence_state=evs)
    prev = load_yaml(ip)["cycle_state"]["evaluation_snapshot"]
    assert load_yaml(ip)["revision_proposals"][0]["status"] == "proposed"

    # ---- approve, then apply (the real CLI)
    ws = ["--workspace", str(tmp_path)]
    assert discover.main([*ws, "revise", "og", pid, "--approve", "--rationale", "test the 3D arm"]) == 0
    assert discover.main([*ws, "revise", "og", pid, "--apply"]) == 0
    spec = load_yaml(ip)
    prop = spec["revision_proposals"][0]
    assert prop["status"] == "applied"
    child_slug = prop["history"][-1]["child_study"]
    new_mv = prop["history"][-1]["model_version"]
    child_path = tmp_path / "studies" / child_slug / "study.yaml"
    child = load_yaml(child_path)
    assert child_slug in spec["studies"] and discover.run_readiness(child, tmp_path)["ready"]
    hyps = [h for h in spec["hypotheses"] if h.get("study") == child_slug]
    assert len(hyps) == 2 and all(h["origin"] == {"kind": "revision", "ref": pid} for h in hyps)
    assert all(h["statement"] is None for h in hyps)
    assert ev.discriminating_hypotheses(spec)[child_slug]["discriminable"]
    (stub,) = child["findings"]
    assert stub["statement"] is None and stub["evidence"]["from_test"] == "enc_in_band"

    # ---- advance --autonomous: the child runs, outcomes + finding slots fill (nothing stubbed)
    runner = FakeRunner()
    with patch("viva_emitters.RunReader") as cls:
        cls.open.return_value = _reader()
        res = discover.advance_cycle(tmp_path, "og", autonomous=True, runner=runner)
    assert runner.calls == [child_slug]
    assert any(r["status"] == "executed" for r in res["runs"])
    child = load_yaml(child_path)
    (run,) = child["runs"]
    assert run["computed_outcomes"] and run["model_version"] == new_mv
    stub = child["findings"][0]
    assert stub["statement"] is None                                  # prose stays human-authored
    assert stub["evidence"].get("observed") is not None               # quantitative slot filled
    assert stub["provenance"]["run_ids"] and stub["provenance"]["model_version"] == new_mv

    return dict(ip=ip, sp=sp, ws=ws, prev=prev, child_slug=child_slug, new_mv=new_mv, run=run, stub=stub)


def _adjudicate(tmp_path, k):
    sp, ws, child_slug, run, stub = k["sp"], k["ws"], k["child_slug"], k["run"], k["stub"]
    # ---- explicit adjudication: the child's graded run settles the 2D/3D gap (authored acts)
    parent = load_yaml(sp)
    pev = parent["findings"][0]["evidence"]
    pev["observed"] = stub["evidence"]["observed"]
    pev["superseded"] = {"observed": 0.12, "source": "2d",
                         "adjudicated_by": f"{child_slug}:{run['name']}"}
    save_yaml_atomic(sp, parent)
    assert discover.main([*ws, "resolve", "og", "open_challenge", f"claim:{child_slug}",
                          "--rationale", "child run lands in the 3D band; gap was a formalism artefact",
                          "--by", f"{child_slug}#{stub['id']}"]) == 0


def test_closed_loop_two_cycle(tmp_path):
    k = _through_advance(tmp_path)
    ip, prev, child_slug, new_mv = k["ip"], k["prev"], k["child_slug"], k["new_mv"]
    # ---- the alternative-to challenge edge does NOT self-clear: it still blocks closure
    _, inv, studies, evs, cyc = _cycle(tmp_path)
    assert de.closure(cyc, inv)["open_challenges"] >= 1 and not de.closure(cyc, inv)["closed"]

    _adjudicate(tmp_path, k)

    # ---- cycle 2
    ip, inv, studies, evs, cyc = _cycle(tmp_path)
    discover.persist_cycle(ip, inv, cyc, studies=studies, evidence_state=evs)
    inv = load_yaml(ip)
    delta = ev.discovery_delta(inv, studies, evs, prev=prev)
    assert delta["proposals"]["evaluated"] == [load_yaml(ip)["revision_proposals"][0]["id"]]
    named = {c["study"]: c for c in delta["changed"]}
    assert child_slug in named and named[child_slug]["model_version_to"] == new_mv
    assert new_mv in delta["new_model_versions"] and not delta["unchanged"]
    assert not [d for d in delta["model_disagreements"] if d["study"] == "s3"]

    discover.persist_cycle(ip, inv, cyc, studies=studies, evidence_state=evs)
    prop = load_yaml(ip)["revision_proposals"][0]
    assert prop["status"] == "evaluated" and prop["evaluated"]["outcome"] == "resolved"
    assert prop["evaluated"]["cycle_fingerprint"]
    assert prop["history"][-1]["event"] == "evaluated"

    # spec section 4 end state: the disagreement is closed, no new proposal is raised
    inv = load_yaml(ip)
    fr = de.frontier(cyc, inv)
    assert not [i for i in fr if i["kind"] in ("revision_proposed", "open_challenge",
                                               "model_disagreement")]
    assert de.closure(cyc, inv)["open_challenges"] == 0
    assert len(inv["revision_proposals"]) == 1

    # persisting again is a no-op for the evaluated row and still schema-valid
    ip, inv, studies, evs, cyc = _cycle(tmp_path)
    discover.persist_cycle(ip, inv, cyc, studies=studies, evidence_state=evs)
    assert [p["status"] for p in load_yaml(ip)["revision_proposals"]] == ["evaluated"]




def test_persist_between_advance_and_adjudication_not_terminally_failed(tmp_path):
    """Normal operator flow: a cycle is persisted after the graded run but BEFORE the authored
    adjudication. The still-live gap must NOT be recorded as `persists`; the proposal stays
    `applied` and ends `evaluated:resolved` once adjudicated."""
    k = _through_advance(tmp_path)
    ip = k["ip"]
    _, inv, studies, evs, cyc = _cycle(tmp_path)
    discover.persist_cycle(ip, inv, cyc, studies=studies, evidence_state=evs)
    mid = load_yaml(ip)["revision_proposals"][0]
    assert mid["status"] == "applied" and not mid.get("evaluated")
    _adjudicate(tmp_path, k)
    _, inv, studies, evs, cyc = _cycle(tmp_path)
    discover.persist_cycle(ip, inv, cyc, studies=studies, evidence_state=evs)
    end = load_yaml(ip)["revision_proposals"][0]
    assert end["status"] == "evaluated" and end["evaluated"]["outcome"] == "resolved"


def test_evaluated_persists_only_when_run_reproduces_gap():
    studies = {"s3": {"name": "s3", "findings": [{"id": "f", "evidence": {
                   "measure": "obs.val", "by_topology": {"2d": 0.12, "3d": 0.71}}}]},
               "kid": {"name": "kid", "runs": [{"name": "r", "computed_outcomes": {"t": {}}}],
                       "findings": [{"evidence": {"from_test": "t", "observed": 0.1}}]},
               "kid2": {"name": "kid2", "runs": [{"name": "r", "computed_outcomes": {"t": {}}}],
                        "findings": [{"evidence": {"from_test": "t", "observed": 0.5}}]}}
    band = {"low": 0.3, "high": 0.8}
    inv = {"revision_proposals": [
        {"id": "rp:a", "status": "applied", "source": {"study": "s3", "observable": "obs.val"},
         "discriminating_test": {"pass_if": band},
         "history": [{"event": "applied", "child_study": "kid"}]},
        {"id": "rp:b", "status": "applied", "source": {"study": "s3", "observable": "obs.val"},
         "history": [{"event": "applied", "child_study": "unrun"}]},
        {"id": "rp:c", "status": "applied", "source": {"study": "s3", "observable": "obs.val"},
         "discriminating_test": {"pass_if": band},
         "history": [{"event": "applied", "child_study": "kid2"}]}]}
    assert discover._evaluate_applied_proposals(inv, studies, "fp") == ["rp:a"]
    a, b, c = inv["revision_proposals"]
    assert c["status"] == "applied"        # graded, gap live, in-band result: undecided, not failed
    assert a["status"] == "evaluated" and a["evaluated"]["outcome"] == "persists"
    assert b["status"] == "applied" and not b.get("evaluated")       # no graded run yet -> untouched
