"""P3 Task 2 — reconcile a study's declared contract.face against run observables."""
import discovery_engine.study_observable_lint as mod
from discovery_engine.study_observable_lint import (
    observable_gaps,
    reconcile_study_observables,
)


def test_gaps_flags_declared_but_unemitted():
    face = {"inputs": {"temperature": {}, "pressure": {}}, "outputs": {"formate": {}}}
    gaps = observable_gaps(face, {"temperature", "formate"})
    assert any("pressure" in g for g in gaps)
    assert not any("temperature" in g for g in gaps)
    assert not any("formate" in g for g in gaps)


def test_reconcile_silent_without_face():
    assert reconcile_study_observables({"name": "x"}, "/ws") == []


def test_reconcile_silent_without_run(monkeypatch):
    monkeypatch.setattr(mod, "study_run_observables", lambda *a, **k: None)
    assert reconcile_study_observables(
        {"contract": {"face": {"outputs": {"m": {}}}}}, "/ws"
    ) == []


def test_reconcile_warns_on_gap(monkeypatch):
    monkeypatch.setattr(mod, "study_run_observables", lambda *a, **k: {"temperature"})
    out = reconcile_study_observables(
        {"contract": {"face": {"outputs": {"heat_flux": {}}}}}, "/ws"
    )
    assert len(out) == 1
    assert out[0]["check"] == "contract-observable"
    assert out[0]["severity"] == "warning"
    assert "heat_flux" in out[0]["message"]


def test_reconcile_silent_when_all_present(monkeypatch):
    monkeypatch.setattr(mod, "study_run_observables", lambda *a, **k: {"temperature", "heat_flux"})
    out = reconcile_study_observables(
        {"contract": {"face": {"inputs": {"temperature": {}}, "outputs": {"heat_flux": {}}}}},
        "/ws",
    )
    assert out == []
