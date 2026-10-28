from pathlib import Path
from discovery_engine.study_evaluator import _resolve_run_store


def test_resolves_store_path(tmp_path):
    store = tmp_path / "results" / "baseline.zarr"
    store.mkdir(parents=True)
    run = {"name": "baseline", "emitter": "xarray", "store_path": "results/baseline.zarr"}
    # relative to study_dir=tmp_path
    assert _resolve_run_store(run, tmp_path) == str(store)


def test_resolves_generic_store_key(tmp_path):
    store = tmp_path / "s.zarr"
    store.mkdir()
    run = {"name": "b", "store": str(store)}
    assert _resolve_run_store(run, tmp_path) == str(store)


def test_emitter_store_still_preferred(tmp_path):
    good = tmp_path / "emit.zarr"
    good.mkdir()
    other = tmp_path / "sp.zarr"
    other.mkdir()
    run = {"emitter": {"store": str(good)}, "store_path": str(other)}
    assert _resolve_run_store(run, tmp_path) == str(good)


def test_falls_back_to_shared_runs_db_for_a_storeless_entry(tmp_path, monkeypatch):
    # `vivarium-workbench run study` writes into <study_dir>/runs.db but can leave the run
    # entry with no store pointer; resolve to that shared DB when the entry's run_id is in it.
    import discovery_engine.run_registry as rr
    shared = tmp_path / "runs.db"
    shared.write_bytes(b"")
    monkeypatch.setattr(rr, "list_runs", lambda p: [{"run_id": "run-xyz"}])
    assert _resolve_run_store({"name": "run-xyz"}, tmp_path) == str(shared)


def test_no_shared_fallback_when_run_id_absent(tmp_path, monkeypatch):
    import discovery_engine.run_registry as rr
    (tmp_path / "runs.db").write_bytes(b"")
    monkeypatch.setattr(rr, "list_runs", lambda p: [{"run_id": "other"}])
    assert _resolve_run_store({"name": "run-xyz"}, tmp_path) is None


def test_declares_sources_and_required_observables(monkeypatch):
    from discovery_engine import study_evaluator as SE

    @SE.declares_sources("enrichment_factor")
    def _log10(reader, test, ws):
        return 0.0

    assert _log10.source_observables == ("enrichment_factor",)
    monkeypatch.setattr(SE, "load_workspace_derived_scalars", lambda ws: {"log10_enrichment": _log10})
    # a derived-scalar measure resolves to its SOURCE observable
    assert SE.required_observables_for_measure({"kind": "derived", "path": "log10_enrichment"}, "/ws") == {"enrichment_factor"}
    # a direct run-data observable resolves to its own path
    assert SE.required_observables_for_measure({"kind": "range_check_per_generation", "path": "mass"}, "/ws") == {"mass"}
    # config_value reads declared params, not the store -> no emitted observable needed
    assert SE.required_observables_for_measure({"kind": "config_value", "path": "seed"}, "/ws") == set()
