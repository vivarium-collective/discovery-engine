"""Resolve a study's latest-run emitted observables.

The study↔composite face reconciliation (P3) checks a study's declared
``contract.face`` against what its simulation actually produces — which is the
set of *observables* the latest run emitted, read via
:class:`viva_emitters.RunReader`.

Best-effort throughout: a study with no run, a registry without an
``emitter_path``, or an unreadable emitter all yield ``None`` rather than
raising, so a report lint never breaks on a missing or malformed run.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Set

from discovery_engine.run_registry import latest_run  # stdlib-only module; import stays light


def _study_dir(study_spec: dict, ws_root) -> Optional[Path]:
    slug = (study_spec or {}).get("slug") or (study_spec or {}).get("name")
    if not slug:
        return None
    try:
        from discovery_engine.workspace_paths import study_dir
        return Path(study_dir(ws_root, slug))
    except Exception:  # noqa: BLE001 - fall back to the conventional layout
        candidate = Path(ws_root) / "workspace" / "studies" / slug
        return candidate if candidate.is_dir() else None


def _latest_run_store(study_dir: Path) -> Optional[Path]:
    """The emitter store of the study's most recent readable run.

    Resolution order: (1) the run registry's ``emitter_path`` when present and on
    disk; (2) the on-disk run artifact whose ``canonical-runs``/``sims`` directory
    name embeds the registry's latest ``run_id`` — so the registry's *latest* run
    wins over a merely mtime-newer one; (3) newest-by-mtime as a last resort (the
    registry's latest artifact may not be on disk). ``None`` when nothing is found.
    """
    run_id = None
    try:
        row = latest_run(study_dir / "runs.db") or {}
        emitter_path = row.get("emitter_path")
        if emitter_path:
            path = Path(emitter_path)
            if not path.is_absolute():
                path = study_dir / path
            if path.exists():
                return path
        run_id = row.get("run_id")
    except Exception:  # noqa: BLE001 - registry missing/broken → fall through
        pass

    dbs = []
    dirs = []
    for parent in ("canonical-runs", "sims"):
        base = study_dir / parent
        if not base.is_dir():
            continue
        dbs.extend(base.glob("*/run.db"))
        dirs.extend(child for child in base.glob("*") if child.is_dir())

    # Prefer the registry's latest run_id when its artifact is on disk — the
    # canonical-runs/sims directory names embed the run_id.
    if run_id:
        for db in dbs:
            if run_id in db.parent.name:
                return db
        for child in dirs:
            if run_id in child.name:
                return child

    if dbs:
        return max(dbs, key=lambda p: p.stat().st_mtime)
    if dirs:
        return max(dirs, key=lambda p: p.stat().st_mtime)
    return None


def study_run_observables(study_spec: dict, ws_root) -> Optional[Set[str]]:
    """The emitted observable names of the study's latest run, or ``None``.

    ``None`` when the study has no resolvable run or the emitter can't be read.
    """
    try:
        from viva_emitters import RunReader

        study_dir = _study_dir(study_spec, ws_root)
        if study_dir is None:
            return None
        store = _latest_run_store(study_dir)
        if store is None:
            return None
        observables = RunReader.open(str(store)).observables()
        # An empty emit list abstains (→ None): a run that produced nothing is far
        # more likely broken than a legitimate "emits no observables", and
        # abstaining avoids flagging every declared port off a degenerate run.
        return set(observables) if observables else None
    except Exception:  # noqa: BLE001 - best-effort: an unreadable run yields None
        return None
