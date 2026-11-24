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

import hashlib
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


# The framework packages whose version + git provenance, together with the
# workspace commit + lockfile hash, pin a run's reproduction environment. Dist
# (PyPI) names; each is looked up best-effort and only recorded if installed, so
# the list can carry future packages without breaking on a partial install. This
# is the single canonical set shared by both run-manifest writers (the workbench's
# env_fingerprint.compute_env delegates here) — keep it the union of what each used.
# Framework packages only — NO domain/workspace tokens (the no-domain-coupling
# guard). A caller that knows its own domain packages supplies them via
# capture_environment(extra_packages=...).
_SIM_PACKAGES = (
    "process-bigraph", "bigraph-schema", "bigraph-loom",
    "viva-discovery-engine", "vivarium-workbench", "viva-emitters", "viva-superpowers",
)
# dist → import name where it isn't just `dist.replace("-", "_")`.
_IMPORT_NAME = {"viva-discovery-engine": "discovery_engine"}


def _find_ws_root(start: Path | str | None) -> Path | None:
    """Walk up from ``start`` (a path inside a workspace, e.g. a runs.db) to the
    workspace root — the first ancestor holding ``workspace.yaml`` or ``.git``.

    Lets :func:`register_run` capture the commit even when a caller (e.g. a
    bespoke earn script) never passed ``ws_root`` — so EVERY recorded run carries
    its environment, not only dashboard-launched ones."""
    if start is None:
        return None
    try:
        p = Path(start).resolve()
    except Exception:  # noqa: BLE001
        return None
    for d in (p, *p.parents):
        try:
            if (d / "workspace.yaml").is_file() or (d / ".git").exists():
                return d
        except Exception:  # noqa: BLE001
            continue
    return None


def _git_out(ws_root, *args) -> str | None:
    """Best-effort `git -C ws_root <args>` stdout, or None. Never raises."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", str(ws_root), *args],
                           capture_output=True, text=True, timeout=5, check=False)
        return r.stdout.strip() or None if r.returncode == 0 else None
    except Exception:  # noqa: BLE001 — best-effort provenance, never fatal
        return None


def _pkg_info(dist: str) -> dict:
    """``{version, git_sha}`` for a dist: version from importlib.metadata, git_sha
    (best-effort) from the git repo containing the imported module — populated for
    an editable / local checkout, None for a wheel install. Never raises."""
    version = git_sha = None
    try:
        from importlib.metadata import version as _pkg_version
        version = _pkg_version(dist)
    except Exception:  # noqa: BLE001
        version = None
    try:
        import importlib
        mod = importlib.import_module(_IMPORT_NAME.get(dist, dist.replace("-", "_")))
        f = getattr(mod, "__file__", None)
        if f:
            git_sha = _git_out(Path(f).resolve().parent, "rev-parse", "HEAD")
    except Exception:  # noqa: BLE001
        git_sha = None
    return {"version": version, "git_sha": git_sha}


def _lockfile_hash(ws_root) -> str | None:
    """16-hex sha256 of the workspace's ``uv.lock`` (the anchor for the full
    resolved package set), or None. Never raises."""
    if ws_root is None:
        return None
    try:
        lock = Path(ws_root) / "uv.lock"
        return hashlib.sha256(lock.read_bytes()).hexdigest()[:16] if lock.is_file() else None
    except Exception:  # noqa: BLE001
        return None


def env_id(env: dict) -> str:
    """Stable, order-independent 16-hex digest of an ``env`` dict (as produced by
    :func:`capture_environment`). The identity of "this exact environment"; the
    canonical hash shared with the workbench so both writers' env_ids agree."""
    return hashlib.sha256(
        json.dumps(env, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()[:16]


def capture_environment(ws_root: Path | str | None = None,
                        pkg: str | None = None,
                        cache_fingerprint: str | None = None,
                        extra_packages: "tuple[str, ...] | list[str] | None" = None) -> dict:
    """Best-effort reproduction environment for a run — the single canonical shape
    shared by both run-manifest writers (the workbench's
    ``env_fingerprint.compute_env`` delegates here):

    ``{workspace_commit, dirty, branch, sim_packages:{dist:{version,git_sha}},
    lockfile_hash, python, platform, cache_fingerprint}``.

    The ``workspace_commit`` is the anchor; ``lockfile_hash`` pins the full
    resolved package set; ``sim_packages`` records framework version + git_sha
    directly so a run is still traceable when the tree is dirty or the lockfile
    isn't pushed; ``dirty`` flags when the commit alone won't reproduce it. The
    separate :func:`env_id` folds this into one comparable id.

    Never raises — every probe degrades to ``None`` — so recording a run is never
    blocked. Always returns a dict: every simulation carries an environment. (For
    legacy callers, ``pkg`` is also captured in ``sim_packages`` when given.)
    """
    import platform as _platform

    commit = branch = dirty = None
    if ws_root is not None:
        import subprocess
        commit = _git_out(ws_root, "rev-parse", "HEAD")
        branch = _git_out(ws_root, "rev-parse", "--abbrev-ref", "HEAD")
        if commit is not None:
            # a dedicated call so a clean tree (rc 0, empty output) is
            # distinguishable from a git failure (rc != 0) — _git_out collapses
            # both to None, which can't tell clean from unknown.
            try:
                r = subprocess.run(
                    ["git", "-C", str(ws_root), "status", "--porcelain"],
                    capture_output=True, text=True, timeout=5, check=False)
                dirty = bool(r.stdout.strip()) if r.returncode == 0 else None
            except Exception:  # noqa: BLE001
                dirty = None

    sim_packages: dict[str, dict] = {}
    wanted = list(_SIM_PACKAGES) + list(extra_packages or []) + ([pkg] if pkg else [])
    for dist in dict.fromkeys(wanted):  # dedupe, preserve order
        info = _pkg_info(dist)
        if info["version"] is not None or info["git_sha"] is not None:
            sim_packages[dist] = info

    try:
        py = _platform.python_version()
    except Exception:  # noqa: BLE001
        py = None
    try:
        plat = _platform.platform()
    except Exception:  # noqa: BLE001
        plat = None

    return {
        "workspace_commit": commit,
        "dirty": dirty,
        "branch": branch,
        "sim_packages": sim_packages,
        "lockfile_hash": _lockfile_hash(ws_root),
        "python": py,
        "platform": plat,
        "cache_fingerprint": cache_fingerprint,
    }


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

    ``env`` is the reproduction environment (:func:`capture_environment`): the
    workspace commit + dirty flag + framework package versions + the lockfile
    hash, so every recorded run can be traced back to the exact code + packages
    (:func:`env_id` folds it into one comparable id, written to the env_id column).
    ``seed``, ``fingerprint_fields``, ``result_fingerprint`` remain v2 keys for
    later tasks — present as ``null`` here, not computed.
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
        "env": capture_environment(ws_root, pkg),
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
    sim_name: str | None = None,
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
    # Capture the environment even when the caller didn't pass ws_root: derive
    # the workspace root from the runs.db location so a bespoke earn script that
    # only knows the DB path still records the commit (every run gets an env).
    if ws_root is None:
        ws_root = _find_ws_root(runs_db)
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
        env_id_val = None
        if params is not None or n_steps is not None:
            try:
                manifest = build_run_manifest(
                    spec_id=spec_id or run_id, params=params, n_steps=n_steps,
                    generation_id=generation_id, ws_root=ws_root, pkg=pkg,
                )
                _env = manifest.get("env")
                env_id_val = env_id(_env) if isinstance(_env, dict) else None
                manifest_json = json.dumps(manifest, default=str)
            except Exception:  # noqa: BLE001 — best-effort, never block a run
                manifest_json = None
                env_id_val = None
        now = time.time()
        if existing is None:
            cols = ["run_id", "spec_id", "started_at", "status"]
            vals = [run_id, spec_id or run_id,
                    started_at if started_at is not None else now,
                    status or "recorded"]
            optional = {
                # sim_name is the identity the workbench study-Simulations view
                # keys on — default it to run_id so a row is NEVER identity-less
                # (a NULL sim_name is read at the low level but dropped by the
                # study runs listing, orphaning a run that study.yaml claims).
                "sim_name": sim_name or run_id,
                "params_json": params_json,
                "n_steps": n_steps,
                "completed_at": completed_at,
                "emitter_path": emitter_path,
                "generation_id": generation_id,
                "manifest_json": manifest_json,
                "env_id": env_id_val,
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
                # only overwrite sim_name when a caller supplies one — a
                # status-only re-register must not clobber an existing identity.
                "sim_name": sim_name,
                "params_json": params_json,
                "n_steps": n_steps,
                "started_at": started_at,
                "completed_at": completed_at,
                "emitter_path": emitter_path,
                "generation_id": generation_id,
                "manifest_json": manifest_json,
                "env_id": env_id_val,
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


def backfill_sim_names(runs_db) -> int:
    """Heal legacy rows: set ``sim_name = run_id`` for every ``runs_meta`` row with
    a NULL/empty ``sim_name`` (rows written before :func:`register_run` set it, which
    the study-Simulations view silently drops). Returns the number of rows updated.
    Idempotent and best-effort — a missing DB / column / lock yields 0, never raises."""
    runs_db = Path(runs_db)
    if not runs_db.is_file():
        return 0
    try:
        conn = sqlite3.connect(str(runs_db), timeout=5.0)
    except sqlite3.Error:
        return 0
    try:
        have = {r[1] for r in conn.execute("PRAGMA table_info(runs_meta)")}
        if not {"sim_name", "run_id"} <= have:
            return 0
        cur = conn.execute(
            "UPDATE runs_meta SET sim_name = run_id "
            "WHERE sim_name IS NULL OR sim_name = ''")
        conn.commit()
        return cur.rowcount or 0
    except sqlite3.Error:
        return 0
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


def read_runlog(runlog_path) -> list[dict]:
    """Fold the workbench's append-only run event log (``.pbg/runs.jsonl``,
    vivarium-workbench #612) into one dict per ``run_id``, carrying that run's
    LATEST logged event.

    The per-study ``runs.db`` is the engine's authoritative run store, but a
    workspace whose runs are recorded only in the run log would otherwise be
    invisible to evidence reconciliation (``0 registered runs`` even though the
    run happened). Each line is one JSON event; later events for the same
    ``run_id`` supersede earlier ones (append-only order). Returns ``[]`` if the
    file is absent or unreadable; tolerant of blank/malformed lines and events
    without a ``run_id``.

    Events for one run carry different fields (a ``started`` event has
    ``study_slug`` + ``spec_id``; a ``completed`` event has the final
    ``status``), so fields are **merged** across a run_id's events — later
    events override shared keys but earlier-only fields (e.g. ``study_slug``)
    are preserved. Taking the last event wholesale would drop them.
    """
    path = Path(runlog_path)
    if not path.exists():
        return []
    try:
        text = path.read_text()
    except OSError:
        return []
    latest: dict[str, dict] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(ev, dict):
            continue
        rid = ev.get("run_id") or ev.get("id")
        if not isinstance(rid, str) or not rid.strip():
            continue
        rid = rid.strip()
        prev = latest.get(rid)
        latest[rid] = {**prev, **ev} if prev else dict(ev)  # merge fields across events
    return list(latest.values())


def runs_by_study_from_runlog(runlog_path, slugs=None) -> dict[str, list[dict]]:
    """Group :func:`read_runlog` runs by their ``study_slug`` (falling back to a
    ``study`` key). ``slugs``, when given, restricts the result to those study
    slugs. Runs with no resolvable study slug are dropped."""
    want = set(slugs) if slugs is not None else None
    out: dict[str, list[dict]] = {}
    for run in read_runlog(runlog_path):
        slug = run.get("study_slug") or run.get("study")
        if not isinstance(slug, str) or not slug.strip():
            continue
        slug = slug.strip()
        if want is not None and slug not in want:
            continue
        out.setdefault(slug, []).append(run)
    return out


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
