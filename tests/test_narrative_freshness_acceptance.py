"""Narrative/verdict freshness acceptance: the discovery-sbml-recovery symptom end to end (engine wave exit)."""
import pytest
import yaml

from discovery_engine import discover as D
from discovery_engine import discovery_engine as de
from discovery_engine.investigation_import import investigations_dir, studies_dir
from discovery_engine.narrative_freshness import EXPIRED, FRESH, UNSTAMPED

RUNS = [{"run_id": "r1", "completed_at": 100.0}]
INV = {"slug": "inv", "studies": ["s1"],
       "executive": {"verdict_status": "planning", "computed_verdict_status": "passing"}}


def _fixture(tmp_path, monkeypatch):
    ws = tmp_path
    inv = investigations_dir(ws) / "inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump(INV))
    sd = studies_dir(ws) / "s1"
    sd.mkdir(parents=True)
    spec = {"name": "s1", "claim": "pop survives", "runs": [{"name": "r1"}],
            "claims": [{"id": "c1", "text": "v1"}], "findings": [{"id": "f1", "statement": "a"}]}
    (sd / "study.yaml").write_text(yaml.safe_dump(spec))
    monkeypatch.setattr(D, "_load_runs_by_study", lambda w, i, s: {k: list(RUNS) for k in s})
    return ws, sd, spec


def _persist(ws):
    inv_path, inv, st = D.load_cycle_inputs(ws, "inv")
    res = D._cycle_with_evidence(ws, "inv", inv, st)
    D.persist_cycle(inv_path, inv, res, studies=st)
    disk = yaml.safe_load(inv_path.read_text())
    return disk, res, inv


def _closure(res, inv):
    blocking = [i for i in de.frontier(res, inv) if i["kind"] in de._CLOSURE_BLOCKING]
    return de.closure(res, inv), blocking


def _assert_non_blocking(disk, res, inv):
    clo, blocking = _closure(res, inv)
    # expiry never contributes a closure-blocking frontier item
    assert not any(i["kind"].startswith(("verdict", "narrative", "decisions", "freshness")) for i in blocking)
    assert clo["closed"] == (not blocking)


def test_narrative_freshness_end_to_end(tmp_path, monkeypatch):
    ws, sd, spec = _fixture(tmp_path, monkeypatch)

    # 1. the symptom: authored planning vs computed passing -> verdict EXPIRED, closure unaffected
    disk, res, inv = _persist(ws)
    fr = disk["cycle_state"]["freshness"]
    assert fr["verdict"]["state"] == EXPIRED
    assert fr["verdict"]["authored"] == "planning" and fr["verdict"]["computed"] == "passing"
    assert fr["any_expired"] is True
    _assert_non_blocking(disk, res, inv)
    closed_expired = de.closure(res, inv)["closed"]
    assert disk["cycle_state"]["closed"] == closed_expired

    # 2. verdict refresh: rationale-gated accept
    for bad in ("", "   "):
        with pytest.raises(ValueError):
            D.accept_verdict(ws, "inv", rationale=bad)
    assert D.load_cycle_inputs(ws, "inv")[1]["executive"]["verdict_status"] == "planning"  # not silently changed
    D.accept_verdict(ws, "inv", rationale="confirmed passing")
    disk, res, inv = _persist(ws)
    ex = disk["executive"]
    assert ex["verdict_status"] == "passing"
    assert any(h.get("rationale") == "confirmed passing" and "planning" in str(h)
               for h in ex["verdict_history"])
    assert disk["cycle_state"]["freshness"]["verdict"]["state"] == FRESH
    _assert_non_blocking(disk, res, inv)

    # 3. narrative basis: unstamped is NOT expired
    assert "narrative_basis" not in ex
    assert disk["cycle_state"]["freshness"]["narrative"]["state"] == UNSTAMPED
    assert disk["cycle_state"]["freshness"]["any_expired"] is False
    for bad in ("", "  "):
        with pytest.raises(ValueError):
            D.refresh_narrative(ws, "inv", rationale=bad)
    D.refresh_narrative(ws, "inv", rationale="reviewed")
    disk, res, inv = _persist(ws)
    assert disk["cycle_state"]["freshness"]["narrative"]["state"] == FRESH
    assert len(disk["executive"]["narrative_history"]) == 1
    stamped = disk["executive"]["narrative_basis"]

    # member study content changes -> EXPIRED; persist does not re-stamp
    spec["findings"] = [{"id": "f1", "statement": "b"}, {"id": "f2", "statement": "c"}]
    spec["claims"] = [{"id": "c1", "text": "v2"}]
    (sd / "study.yaml").write_text(yaml.safe_dump(spec))
    disk, res, inv = _persist(ws)
    assert disk["cycle_state"]["freshness"]["narrative"]["state"] == EXPIRED
    assert disk["executive"]["narrative_basis"] == stamped
    assert len(disk["executive"]["narrative_history"]) == 1
    _assert_non_blocking(disk, res, inv)

    D.refresh_narrative(ws, "inv", rationale="re-read after study change")
    disk, res, inv = _persist(ws)
    assert disk["cycle_state"]["freshness"]["narrative"]["state"] == FRESH
    assert disk["executive"]["narrative_basis"] != stamped
    assert len(disk["executive"]["narrative_history"]) == 2
    _assert_non_blocking(disk, res, inv)
