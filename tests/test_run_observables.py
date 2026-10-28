"""P3 Task 1 — resolve a study's latest-run emitted observables.

Best-effort: no run / unreadable run → ``None``, never an exception.
"""
import os

import pytest

from discovery_engine.run_observables import study_run_observables

GENESIS = "/Users/eranagmon/code/viva-genesis"


@pytest.mark.skipif(
    not os.path.isdir(f"{GENESIS}/workspace/studies/og-00-vent-reactor-baseline"),
    reason="viva-genesis og-00 run fixture not present",
)
def test_observables_from_real_run():
    obs = study_run_observables({"slug": "og-00-vent-reactor-baseline"}, GENESIS)
    assert obs is not None
    assert "temperature" in obs        # declared AND emitted
    assert "pressure" not in obs       # declared in contract.face but never emitted


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
