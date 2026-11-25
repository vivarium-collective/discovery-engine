"""Back-compatibility alias for the moved run-registry module.

The run-registry accessors moved to :mod:`discovery_engine.study.run_registry`
(they belong with the study layer). Older workspace run scripts import them from
the historical top-level path ``discovery_engine.run_registry``. This module
re-exports the public surface from the new home so that import keeps working
across the move, letting a workspace bump to a newer discovery-engine without a
code change first.

New code should import from ``discovery_engine.study.run_registry`` directly.
"""
from __future__ import annotations

from discovery_engine.study.run_registry import (
    RUNS_META_DDL,
    backfill_sim_names,
    build_run_manifest,
    capture_environment,
    env_id,
    get_run_params,
    latest_run,
    list_runs,
    read_runlog,
    register_run,
    runs_by_study_from_runlog,
)

__all__ = [
    "RUNS_META_DDL",
    "backfill_sim_names",
    "build_run_manifest",
    "capture_environment",
    "env_id",
    "get_run_params",
    "latest_run",
    "list_runs",
    "read_runlog",
    "register_run",
    "runs_by_study_from_runlog",
]
