"""P3 Task 1 — resolve a study's latest-run emitted observables.

Best-effort: no run / unreadable run → ``None``, never an exception.
"""
from discovery_engine.run_observables import study_run_observables



def test_observables_from_synthetic_run(tmp_path, monkeypatch):
    # Synthetic in-repo workspace: one study with a canonical run store. The
    # emitter reader is stubbed, so no external workspace or run data is needed.
    import sys
    import types

    study = tmp_path / "studies" / "demo-study"
    store = study / "canonical-runs" / "comp__RID1__aaa"
    store.mkdir(parents=True)
    (store / "run.db").write_text("x")

    class _FakeReader:
        @staticmethod
        def open(path):
            assert path.endswith("run.db")
            return types.SimpleNamespace(observables=lambda: ["temperature"])

    fake = types.ModuleType("viva_emitters")
    fake.RunReader = _FakeReader
    monkeypatch.setitem(sys.modules, "viva_emitters", fake)

    obs = study_run_observables({"slug": "demo-study"}, tmp_path)
    assert obs == {"temperature"}      # emitted
    assert "pressure" not in obs       # declared elsewhere but never emitted


def test_observables_none_when_no_run(tmp_path):
    assert study_run_observables({"slug": "nope"}, tmp_path) is None


def test_latest_run_store_prefers_registry_run_id(tmp_path, monkeypatch):
    # The registry's latest run must win over a mtime-newer-but-older run on disk.
    import os
    import time

    import discovery_engine.run_observables as ro

    cr = tmp_path / "canonical-runs"
    cr.mkdir()
    matched = cr / "comp__RID123__aaa"
    matched.mkdir()
    (matched / "run.db").write_text("x")
    newer = cr / "comp__OTHER__bbb"
    newer.mkdir()
    (newer / "run.db").write_text("x")
    # Make 'newer' mtime-newer so a pure mtime fallback would wrongly pick it.
    future = time.time() + 1000
    os.utime(newer / "run.db", (future, future))

    monkeypatch.setattr(
        ro, "latest_run", lambda db: {"run_id": "RID123", "emitter_path": None}
    )
    assert ro._latest_run_store(tmp_path) == matched / "run.db"
