"""advance's post-run sync must link each run's findings back to the study
(provenance.run_ids) by invoking populate_finding_observations — the write-back
that turns an executed run into a defensible finding the evidence ledger can
classify, instead of leaving it 'provisional — no resolvable registered run'."""
from pathlib import Path

from discovery_engine import discover as D
from discovery_engine import finding_observations, study_outcomes


def test_post_run_sync_populates_finding_observations(tmp_path, monkeypatch):
    sdir = tmp_path / "s-demo"
    sdir.mkdir()
    (sdir / "study.yaml").write_text(
        "name: s-demo\nstatus: planned\nruns: []\nfindings: []\n", encoding="utf-8")

    # isolate the wiring: the outcome sync is exercised elsewhere, so no-op it here.
    monkeypatch.setattr(study_outcomes, "sync", lambda *_a, **_k: {})
    called = {}
    monkeypatch.setattr(finding_observations, "populate_finding_observations",
                        lambda d: called.setdefault("dir", str(d)))

    D._post_run_sync(sdir, before_runs=set())

    assert called.get("dir") == str(sdir)  # the finding-linkage hop ran
