"""Read/write accessors over the per-study runs.db `runs_meta` table — the
authoritative record of which runs belong to a study and which is latest.

`runs_meta.params_json` is the run-config provenance slot: a runner records
the decision-relevant knobs (applied perturbations, cache fingerprint, seed,
generations, ...) via :func:`register_run`, and any downstream consumer reads
them back with :func:`get_run_params`. The run_id is the run's
``experiment_id`` (the top parquet partition key), so a figure that carries
its run_id can always be traced back to the exact config that produced it.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

# Minimal DDL for tests + first-time creation. Real DBs are migrated by the
# dashboard's composite_runs connect()/_migrate_runs_meta which ALTERs in
# nullable columns (incl. emitter_path, added in the dashboard phase;
# manifest_json, added in reproducible-rerun-spine Task 1 to unify this
# writer's bespoke `run-script` runs with the dashboard's on the same replay
# manifest). A runs.db touched only by this module (never the dashboard) is
# tolerated too: register_run/latest_run only read/write columns present in
# `PRAGMA table_info`, so a pre-existing table missing manifest_json simply
# omits it rather than raising.
RUNS_META_DDL = """
CREATE TABLE IF NOT EXISTS runs_meta (
    run_id        TEXT PRIMARY KEY,
    spec_id       TEXT NOT NULL,
    label         TEXT,
    params_json   TEXT,
    started_at    REAL NOT NULL,
    completed_at  REAL,
    n_steps       INTEGER,
    status        TEXT NOT NULL,
    sim_name      TEXT,
    generation_id TEXT,
    emitter_path  TEXT,
    manifest_json TEXT
);
"""

_COLS = ("run_id", "spec_id", "started_at", "completed_at", "status",
         "generation_id", "emitter_path", "n_steps", "manifest_json")


def latest_run(runs_db: Path) -> dict | None:
    """Newest run row by COALESCE(completed_at, started_at), or None.

    Tolerates older DBs missing the generation_id / emitter_path columns
    (those keys are simply omitted from the returned dict)."""
    runs_db = Path(runs_db)
    if not runs_db.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{runs_db}?mode=ro", uri=True, timeout=1.0)
        try:
            have = {r[1] for r in conn.execute("PRAGMA table_info(runs_meta)")}
            cols = [c for c in _COLS if c in have]
            row = conn.execute(
                f"SELECT {', '.join(cols)} FROM runs_meta "
                "ORDER BY COALESCE(completed_at, started_at) DESC LIMIT 1"
            ).fetchone()
        finally:
            conn.close()
        return dict(zip(cols, row)) if row else None
    except sqlite3.Error:
        return None


def build_run_manifest(*, spec_id, params, n_steps, emitter=None,
                       emit_paths=None, runtime=None, origin="bespoke",
                       study=None, pkg=None, generation_id=None,
                       ws_root=None) -> dict:
    """Assemble the replay manifest for a run recorded via :func:`register_run`.

    Ports the shape of ``vivarium_workbench.lib.composite_runs.build_run_manifest``
    (schema ``version: 2``) so bespoke ``run-script`` runs — which never go
    through the dashboard's ``save_metadata`` — carry the same manifest as
    dashboard-launched runs (reproducible-rerun-spine Task 1: one shared
    manifest schema across both run-record writers). ``discovery_engine`` is a
    dependency *of* vivarium-workbench, not the reverse, so this is a
    standalone port rather than an import — keep the two in sync by hand
    (see ``test_build_run_manifest_schema_matches_documented_v2_key_set``,
    which pins the exact top-level key set so a future divergence fails a
    test rather than silently drifting).

    ``code_version`` is best-effort, mirroring the workbench writer exactly:
    a git-HEAD lookup on ``ws_root`` and a ``pkg`` version lookup, each
    independently wrapped so a failure (no git repo, package not installed,
    no ``ws_root``/``pkg`` given, ...) degrades to ``None`` rather than
    raising — this must never block a run from being recorded.

    ``env``, ``seed``, ``fingerprint_fields``, ``result_fingerprint`` are v2
    keys filled in by later tasks (env capture, result fingerprinting,
    first-class seed threading) — present as ``null`` here, not computed.
    """
    git_sha = None
    if ws_root is not None:
        try:
            import subprocess
            out = subprocess.run(
                ["git", "-C", str(ws_root), "rev-parse", "HEAD"],
                capture_output=True, text=True, check=True, timeout=5,
            )
            git_sha = out.stdout.strip() or None
        except Exception:  # noqa: BLE001 — best-effort provenance, never fatal
            git_sha = None

    pkg_version = None
    if pkg:
        try:
            from importlib.metadata import version as _pkg_version
            pkg_version = _pkg_version(pkg)
        except Exception:  # noqa: BLE001 — best-effort provenance, never fatal
            pkg_version = None

    return {
        "version": 2,
        "spec_id": spec_id,
        "params": dict(params or {}),
        "n_steps": int(n_steps) if n_steps is not None else None,
        "emitter": emitter,
        "emit_paths": list(emit_paths or []),
        "runtime": dict(runtime or {}),
        "origin": origin,
        "study": study,
        "pkg": pkg,
        "generation_id": generation_id,
        "code_version": {"git_sha": git_sha, "package": pkg_version},
        "env": None,
        "seed": None,
        "fingerprint_fields": None,
        "result_fingerprint": None,
    }


def register_run(
    runs_db: Path,
    run_id: str,
    *,
    spec_id: str | None = None,
    status: str | None = None,
    params: dict | None = None,
    n_steps: int | None = None,
    started_at: float | None = None,
    completed_at: float | None = None,
    emitter_path: str | None = None,
    generation_id: str | None = None,
    ws_root: str | Path | None = None,
    pkg: str | None = None,
) -> None:
    """Upsert a ``runs_meta`` row for ``run_id``, writing ``params`` as
    ``params_json`` (the run-config provenance slot).

    Creates the DB + table if absent. ``spec_id`` defaults to ``run_id`` on
    first insert (the column is NOT NULL); ``status`` defaults to ``"recorded"``.
    On re-register (same run_id) only the supplied fields are overwritten —
    passing ``params`` again replaces the stored config; passing ``None``
    leaves the existing value intact. This makes the runner safe to call once
    up-front (status ``running`` + full config) and again at completion
    (status ``complete``) without losing the recorded config.

    ``params`` is JSON-serialised; non-JSON values fall back to ``str``.

    Whenever ``params`` and/or ``n_steps`` is supplied (i.e. this call is
    recording — not just updating the status of — a run's config), a replay
    manifest is (re)built via :func:`build_run_manifest` and stored as
    ``manifest_json`` — best-effort: on a runs.db that predates the
    ``manifest_json`` column, it's simply not written (see module docstring).
    """
    runs_db = Path(runs_db)
    runs_db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(runs_db), timeout=5.0)
    try:
        conn.executescript(RUNS_META_DDL)
        have = {r[1] for r in conn.execute("PRAGMA table_info(runs_meta)")}
        existing = conn.execute(
            "SELECT run_id FROM runs_meta WHERE run_id=?", (run_id,)
        ).fetchone()
        params_json = (
            json.dumps(params, default=str, sort_keys=True)
            if params is not None else None
        )
        manifest_json = None
        if params is not None or n_steps is not None:
            try:
                manifest_json = json.dumps(build_run_manifest(
                    spec_id=spec_id or run_id, params=params, n_steps=n_steps,
                    generation_id=generation_id, ws_root=ws_root, pkg=pkg,
                ), default=str)
            except Exception:  # noqa: BLE001 — best-effort, never block a run
                manifest_json = None
        now = time.time()
        if existing is None:
            cols = ["run_id", "spec_id", "started_at", "status"]
            vals = [run_id, spec_id or run_id,
                    started_at if started_at is not None else now,
                    status or "recorded"]
            optional = {
                "params_json": params_json,
                "n_steps": n_steps,
                "completed_at": completed_at,
                "emitter_path": emitter_path,
                "generation_id": generation_id,
                "manifest_json": manifest_json,
            }
            for col, val in optional.items():
                if val is not None and col in have:
                    cols.append(col)
                    vals.append(val)
            conn.execute(
                f"INSERT INTO runs_meta ({', '.join(cols)}) "
                f"VALUES ({', '.join('?' * len(cols))})", vals)
        else:
            sets: list[str] = []
            vals = []
            updates = {
                "spec_id": spec_id,
                "status": status,
                "params_json": params_json,
                "n_steps": n_steps,
                "started_at": started_at,
                "completed_at": completed_at,
                "emitter_path": emitter_path,
                "generation_id": generation_id,
                "manifest_json": manifest_json,
            }
            for col, val in updates.items():
                if val is not None and col in have:
                    sets.append(f"{col}=?")
                    vals.append(val)
            if sets:
                vals.append(run_id)
                conn.execute(
                    f"UPDATE runs_meta SET {', '.join(sets)} WHERE run_id=?", vals)
        conn.commit()
    finally:
        conn.close()


def list_runs(runs_db) -> list[dict]:
    """All runs_meta rows, newest first by COALESCE(completed_at, started_at).
    Returns [] if the DB or table is absent. Tolerant of missing columns."""
    path = Path(runs_db)
    if not path.exists():
        return []
    conn = sqlite3.connect(str(path))
    try:
        conn.row_factory = sqlite3.Row
        try:
            cur = conn.execute(
                "SELECT * FROM runs_meta "
                "ORDER BY COALESCE(completed_at, started_at) DESC, rowid DESC"
            )
        except sqlite3.OperationalError:
            return []
        return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def get_run_params(runs_db: Path, run_id: str) -> dict | None:
    """Return the recorded ``params_json`` for ``run_id`` as a dict, or
    ``None`` if the run / DB / column is absent or the JSON is empty.

    Read-only and exception-tolerant (mirrors :func:`latest_run`)."""
    runs_db = Path(runs_db)
    if not runs_db.is_file():
        return None
    try:
        conn = sqlite3.connect(f"file:{runs_db}?mode=ro", uri=True, timeout=1.0)
        try:
            have = {r[1] for r in conn.execute("PRAGMA table_info(runs_meta)")}
            if "params_json" not in have:
                return None
            row = conn.execute(
                "SELECT params_json FROM runs_meta WHERE run_id=?", (run_id,)
            ).fetchone()
        finally:
            conn.close()
        if not row or not row[0]:
            return None
        return json.loads(row[0])
    except (sqlite3.Error, json.JSONDecodeError):
        return None
