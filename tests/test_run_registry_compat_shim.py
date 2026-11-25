"""The historical top-level import path keeps working after the module move."""
def test_top_level_run_registry_reexports_the_moved_module():
    import discovery_engine.run_registry as shim
    import discovery_engine.study.run_registry as home

    # v2ecoli's exact import still resolves, to the same object.
    from discovery_engine.run_registry import register_run
    assert register_run is home.register_run
    for name in ("register_run", "build_run_manifest", "latest_run", "list_runs",
                 "capture_environment", "env_id", "backfill_sim_names",
                 "get_run_params", "read_runlog", "runs_by_study_from_runlog",
                 "RUNS_META_DDL"):
        assert getattr(shim, name) is getattr(home, name), name
