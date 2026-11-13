"""Recovery-bridge spike (Task 1): the injected benchmark runner.

``advance_cycle`` dispatches a study's run by calling the injected ``runner``.  The runner
stands in for ``vivarium-workbench run study <slug>``: it registers a real ``runs_meta`` row
(so ``record_runs`` merges the run into study.yaml and ``compute_outcomes`` grades it) and a
store dir with a ``history/`` subdir.  The store contents are unused — grading is store-less
(the ``@storeless`` evaluator re-derives its value from the built model), but the row and the
openable store make the read-back chain run end to end.

Note: the engine reads runs via ``discovery_engine.study.run_registry`` (there is no
``viva_emitters.run_registry`` in this environment), so the row is registered there — the
same module the engine's ``record_runs`` lists from.

Minimal-but-real spike helper; Task 2 finalizes it.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from discovery_engine.study import run_registry as rr


def benchmark_runner() -> Callable:
    """Return a ``runner(command, ws_root, *, worktree=None, **kw) -> dict`` matching the
    injected-runner contract advance_cycle expects."""
    counter = {"n": 0}

    def runner(command, ws_root, *, worktree=None, **kw) -> dict:
        m = re.search(r"run study (\S+)", str(command))
        slug = m.group(1) if m else "unknown"
        counter["n"] += 1
        n = counter["n"]
        sdir = Path(ws_root) / "studies" / slug
        run_id = f"run-{slug}-{n}"
        store = sdir / f"store{n}"
        (store / "history").mkdir(parents=True, exist_ok=True)
        rr.register_run(sdir / "runs.db", run_id, status="complete",
                        params={}, emitter_path=str(store))
        return {"ok": True, "returncode": 0, "run_id": run_id, "tail": ""}

    return runner
