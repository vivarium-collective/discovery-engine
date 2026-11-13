from discovery_engine.benchmark.recovery_run import benchmark_runner


def test_runner_registers_run(tmp_path):
    out = benchmark_runner()("vivarium-workbench run study s1 --workspace x", tmp_path)
    assert out["ok"] is True
    sdir = tmp_path / "studies" / "s1"
    assert (sdir / "runs.db").exists()
    assert (sdir / "store1" / "history").is_dir()
    from discovery_engine.study import run_registry as rr
    assert [r["run_id"] for r in rr.list_runs(sdir / "runs.db")] == [out["run_id"]]
