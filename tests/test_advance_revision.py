"""Task 13: advance_cycle runs an APPLIED revision's child study, re-runs a model whose
composite changed in place, skips a study already run for its CURRENT model version, and
never runs an unapplied (review-tier) proposal."""
from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import patch

import polars as pl
import yaml

from discovery_engine import discover
from discovery_engine import run_registry as rr
from discovery_engine.model_architect import model_version
from discovery_engine.study_io import load_yaml, save_yaml_atomic

C1 = "json.tool.main"                                   # resolves (stdlib)
C2 = "discovery_engine.model_architect.build_catalog"   # resolves
PARAMS = {"x": 1.0}


def _study(name, composite=C1, runs=None):
    s = {"name": name, "claim": f"{name} holds", "schema_version": 4,
         "baseline": [{"name": "baseline", "composite": composite, "params": dict(PARAMS)}],
         "observables": ["obs.val"],
         "tests": [{"name": "t", "measure": {"kind": "range_check_per_generation", "path": "obs.val",
                                             "window": "full_lineage_from_gen_0"},
                    "pass_if": {"op": "range", "low": 0, "high": 100}}]}
    if runs is not None:
        s["runs"] = runs
    return s


def _ws(tmp_path, studies, proposals=None):
    (tmp_path / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    inv = tmp_path / "investigations" / "og"
    inv.mkdir(parents=True)
    spec = {"name": "og", "studies": list(studies)}
    if proposals:
        spec["revision_proposals"] = proposals
    save_yaml_atomic(inv / "investigation.yaml", spec)
    for slug, st in studies.items():
        d = tmp_path / "studies" / slug
        d.mkdir(parents=True)
        save_yaml_atomic(d / "study.yaml", st)


class FakeRunner:
    """Stands in for `vivarium-workbench run study`: records a runs_meta row and a tiny store
    (dir with history/), but does NOT sync study.yaml (so advance's post-run sync is exercised)."""

    def __init__(self):
        self.calls = []

    def __call__(self, cmd, ws):
        m = re.search(r"run study (\S+)", cmd)
        slug = m.group(1)
        self.calls.append(slug)
        sdir = Path(ws) / "studies" / slug
        n = self.calls.count(slug)
        store = sdir / f"store{n}"
        (store / "history").mkdir(parents=True)
        rr.register_run(sdir / "runs.db", f"run-{slug}-{n}", status="complete", params=dict(PARAMS),
                        emitter_path=str(store))
        return {"ok": True, "returncode": 0, "tail": "done"}


def _reader():
    from tests.test_computed_outcomes import FakeReader
    n = 3
    return FakeReader({"obs.val": pl.DataFrame({
        "generation": [0] * n, "time": [float(i) for i in range(n)],
        "abs_time": [float(i) for i in range(n)], "value": [40.0, 50.0, 60.0]})})


def _advance(ws, runner, **kw):
    with patch("viva_emitters.RunReader") as cls:
        cls.open.return_value = _reader()
        return discover.advance_cycle(ws, "og", autonomous=True, runner=runner, **kw)


def test_advance_runs_applied_revision(tmp_path):
    prop = {"id": "rp:s3:mv:old:ab", "status": "applied",
            "history": [{"event": "applied", "child_study": "s3-rev-ab"}]}
    _ws(tmp_path, {"s3": _study("s3", runs=[{"name": "r0", "params": dict(PARAMS)}]),
                   "s3-rev-ab": _study("s3-rev-ab", composite=C2)}, [prop])
    runner = FakeRunner()
    res = _advance(tmp_path, runner)
    assert runner.calls == ["s3-rev-ab"]
    assert any(r["status"] == "executed" for r in res["runs"])
    runs = load_yaml(tmp_path / "studies" / "s3-rev-ab" / "study.yaml")["runs"]
    assert len(runs) == 1 and runs[0].get("computed_outcomes")        # post-run sync filled it
    assert runs[0]["model_version"] == model_version(C2, PARAMS)["id"]  # stamped with the arm mv


def test_per_mv_skip_and_no_loop(tmp_path):
    cur = model_version(C1, PARAMS)["id"]
    _ws(tmp_path, {
        "same": _study("same", runs=[{"name": "r0", "params": dict(PARAMS)}]),        # run at current mv
        "raised": _study("raised", composite=C2,                                      # composite raised in place
                         runs=[{"name": "r0", "params": dict(PARAMS), "model_version": cur}]),
        "fresh": _study("fresh"),                                                     # revised/new, never run
    })
    runner = FakeRunner()
    _advance(tmp_path, runner)
    assert sorted(runner.calls) == ["fresh", "raised"]          # "same" skipped; stale-mv study re-runs
    # a second pass is a no-op: every study now has a run at its CURRENT mv
    runner.calls.clear()
    _advance(tmp_path, runner)
    assert runner.calls == []
    # a runner that records nothing can't loop: each model runs at most once per pass
    _ws2 = tmp_path / "w2"
    _ws2.mkdir()
    _ws(_ws2, {"only": _study("only")})
    calls = []
    res = _advance(_ws2, lambda c, w: calls.append(c) or {"ok": True, "returncode": 0, "tail": ""},
                   max_runs=5)
    assert len(calls) == 1 and len(res["runs"]) == 1


def test_unapplied_proposal_is_gated(tmp_path):
    # an approved-but-unapplied proposal (child not on disk) next to a runnable never-run study:
    # advance runs only the study, nothing attributable to the proposal.
    prop = {"id": "rp:s3:mv:old:ab", "status": "approved", "approval": "review",
            "source": {"kind": "model_disagreement", "study": "s3"},
            "history": [{"event": "approved", "child_study": "s3-rev-ab"}]}
    _ws(tmp_path, {"fresh": _study("fresh")}, [prop])
    runner = FakeRunner()
    res = _advance(tmp_path, runner)
    assert runner.calls == ["fresh"]
    assert all("rp:" not in r["target"] and "rev" not in r["target"] for r in res["runs"])
    assert not (tmp_path / "studies" / "s3-rev-ab").exists()


def test_resolver_only_names_on_disk_studies():
    assert discover._advance_study_for("claim:kid", {"kid": {}}) == "kid"
    assert discover._advance_study_for("kid", {"kid": {}}) == "kid"
    assert discover._advance_study_for("rp:x", {"kid": {}}) is None
    assert discover._advance_study_for("claim:nope", {"kid": {}}) is None
