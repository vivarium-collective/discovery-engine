"""Engine wiring of narrative freshness: summary carries per-tab state; persist is
mechanical-only; refresh_narrative / accept_verdict are rationale-gated."""
from __future__ import annotations

import pytest
import yaml

from discovery_engine import discover as D
from discovery_engine.narrative_freshness import EXPIRED, FRESH, UNSTAMPED
from discovery_engine.investigation_import import investigations_dir, studies_dir

INV = {"name": "inv", "studies": ["s5"],
       "executive": {"verdict_status": "planning", "computed_verdict_status": "passing",
                     "what_is_this": "x"}}
STUDY = {"name": "s5", "claim": "c", "findings": [{"id": "f1"}], "runs": [{"name": "r1"}]}


def _ws(tmp_path, monkeypatch):
    inv = investigations_dir(tmp_path) / "inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump(INV))
    sd = studies_dir(tmp_path) / "s5"
    sd.mkdir(parents=True)
    (sd / "study.yaml").write_text(yaml.safe_dump(STUDY))
    monkeypatch.setattr(D, "_load_runs_by_study", lambda w, i, s: {k: [] for k in s})
    return inv / "investigation.yaml"


def _read(p):
    return yaml.safe_load(p.read_text())


def _persist(tmp_path):
    inv_path, inv, st = D.load_cycle_inputs(tmp_path, "inv")
    res = D._cycle_with_evidence(tmp_path, "inv", inv, st)
    D.persist_cycle(inv_path, inv, res, studies=st)
    return _read(inv_path)


def test_summary_carries_per_tab_freshness(tmp_path, monkeypatch):
    _ws(tmp_path, monkeypatch)
    fr = _persist(tmp_path)["cycle_state"]["freshness"]
    assert fr["verdict"]["state"] == EXPIRED and fr["any_expired"] is True
    assert fr["narrative"]["state"] == UNSTAMPED


def test_persist_never_stamps_narrative(tmp_path, monkeypatch):
    p = _ws(tmp_path, monkeypatch)
    out = _persist(tmp_path)
    assert "narrative_basis" not in out["executive"]
    D.refresh_narrative(tmp_path, "inv", rationale="re-read")
    basis = _read(p)["executive"]["narrative_basis"]
    assert _persist(tmp_path)["executive"]["narrative_basis"] == basis


def test_refresh_narrative_stamps_and_audits(tmp_path, monkeypatch):
    p = _ws(tmp_path, monkeypatch)
    D.refresh_narrative(tmp_path, "inv", rationale="re-read prose", by="eran")
    ex = _read(p)["executive"]
    h = ex["narrative_history"][-1]
    assert h["basis"] == ex["narrative_basis"] and h["rationale"] == "re-read prose"
    assert h["by"] == "eran" and h["at"]
    assert _persist(tmp_path)["cycle_state"]["freshness"]["narrative"]["state"] == FRESH


@pytest.mark.parametrize("bad", ["", "   ", None])
def test_refresh_and_accept_refuse_blank_rationale(tmp_path, monkeypatch, bad):
    p = _ws(tmp_path, monkeypatch)
    before = p.read_text()
    with pytest.raises(ValueError):
        D.refresh_narrative(tmp_path, "inv", rationale=bad)
    with pytest.raises(ValueError):
        D.accept_verdict(tmp_path, "inv", rationale=bad)
    assert p.read_text() == before


def test_accept_verdict_accepts_computed_and_audits(tmp_path, monkeypatch):
    p = _ws(tmp_path, monkeypatch)
    D.accept_verdict(tmp_path, "inv", rationale="agree", by="eran")
    ex = _read(p)["executive"]
    assert ex["verdict_status"] == "passing"
    assert ex["verdict_history"][-1]["rationale"] == "agree"
    assert _persist(tmp_path)["cycle_state"]["freshness"]["verdict"]["state"] == FRESH
