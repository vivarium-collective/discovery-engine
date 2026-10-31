"""Global registry of pbg workspaces and their running dashboards.

Two files under ``$VIVA_HOME`` / ``$PBG_HOME`` (default ``~/.pbg``):

* ``workspaces.json``        — catalog of all known workspaces.
* ``servers/<name>.json``    — one per running dashboard (added by the
                                dashboard's ``cmd_serve`` on boot, removed
                                on exit).

Single writer surface for both. Usable as a Python API (imported by the
dashboard backend) and as a CLI (``python -m discovery_engine.workspace_catalog
<subcommand>``) — the CLI is what skill shims and shell scripts invoke.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
# C.M. 260929: Using both Windows and Unix-type file locking got messy and was
# demanding a lot of code. This package is built to manage it.
from filelock import FileLock
# C.M. 261006: Similar to the file locking, code like "os.kill" has different
# behaviors depending on the operating system. Instead of writing code from
# scratch for each system, it is cleaner to use a package already designed
# for these nuances.
import psutil

SCHEMA_VERSION = 1


def _home() -> Path:
    # Env alias (pbg→viva): VIVA_HOME preferred, PBG_HOME as deprecated fallback.
    # The default dir stays ``~/.pbg`` — that directory is shared with the
    # vivarium-workbench server, so renaming it to ``~/.viva`` is a coordinated
    # cross-repo change (read-both in both repos), NOT done here.
    home = os.environ.get("VIVA_HOME") or os.environ.get("PBG_HOME")
    return (Path(home) if home else Path.home() / ".pbg").expanduser()


def _catalog_path() -> Path:
    return _home() / "workspaces.json"


def _servers_dir() -> Path:
    return _home() / "servers"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _safe_resolve(path: str | Path) -> Path:
    return Path(path).expanduser().resolve()


def _load_catalog() -> dict:
    p = _catalog_path()
    if not p.is_file():
        return {"version": SCHEMA_VERSION, "workspaces": []}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not isinstance(data.get("workspaces"), list):
            return {"version": SCHEMA_VERSION, "workspaces": []}
        return data
    except (json.JSONDecodeError, OSError):
        return {"version": SCHEMA_VERSION, "workspaces": []}


def list_workspaces() -> list[dict]:
    return _load_catalog().get("workspaces", [])


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(payload)
        tmp.replace(path)
    except BaseException:
        # Best-effort cleanup of the .tmp on crash; ignore errors so we
        # don't mask the original exception.
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


# C.M. 260929: Hypothetically both of these should run on a Windows or Unix-type
def _with_catalog_lock(fn):
    """Hold an exclusive lock on the catalog lock file while running fn."""
    _home().mkdir(parents=True, exist_ok=True)
    with FileLock(str(_home() / "workspaces.json.lock")):
        return fn()


def _with_servers_lock(fn):
    """Hold an exclusive lock on the servers lock file while running fn.

    The lock file lives at ``~/.pbg/servers.lock`` (a sibling of the
    ``servers/`` directory, not inside it) so that ``*.json`` globs over
    the servers directory never pick it up.
    """
    _home().mkdir(parents=True, exist_ok=True)
    with FileLock(str(_home() / "servers.lock")):
        return fn()

def add(path: str | Path, name: str | None = None, package: str | None = None) -> dict:
    """Append-or-noop. Returns the catalog entry. Raises ValueError if path is
    not a workspace (no workspace.yaml)."""
    target = _safe_resolve(path)
    if not (target / "workspace.yaml").is_file():
        raise ValueError(f"not a workspace (no workspace.yaml): {target}")

    if name is None or package is None:
        import yaml  # local import keeps the module light at import time
        data = yaml.safe_load((target / "workspace.yaml").read_text(encoding="utf-8")) or {}
        name = name or data.get("name") or target.name
        # The schema's canonical top-level key is `package_path` (the scaffolder
        # writes it); `package` only appears under the legacy `stages`/`layout`
        # blocks. Prefer `package_path`, falling back to `package` for older
        # hand-written files. Without this, every entry added without --package
        # (including `vwb serve`'s add(workspace)) stored package=null. (#356)
        package = package or data.get("package_path") or data.get("package")

    def _do_add() -> dict:
        catalog = _load_catalog()
        target_str = str(target)
        for entry in catalog["workspaces"]:
            if entry.get("path") == target_str:
                return entry
        entry = {
            "name": name,
            "path": target_str,
            "package": package,
            "added_at": _now_iso(),
        }
        catalog["workspaces"].append(entry)
        catalog["version"] = SCHEMA_VERSION
        _atomic_write(_catalog_path(), json.dumps(catalog, indent=2))
        return entry

    return _with_catalog_lock(_do_add)


def forget(path) -> bool:
    """Remove the catalog entry for ``path``. Returns True if anything was
    removed, False if no matching entry existed."""
    target_str = str(_safe_resolve(path))

    def _do_forget() -> bool:
        catalog = _load_catalog()
        before = len(catalog["workspaces"])
        catalog["workspaces"] = [
            e for e in catalog["workspaces"] if e.get("path") != target_str
        ]
        if len(catalog["workspaces"]) == before:
            return False
        _atomic_write(_catalog_path(), json.dumps(catalog, indent=2))
        return True

    return _with_catalog_lock(_do_forget)


def _server_filename(name: str, path: Path) -> str:
    """Pick a filename for the running-server entry for (name, path).

    Without a collision, this is ``<name>.json``. If a file already exists with
    that name and a DIFFERENT path, the new file gets a 6-char sha1(path)
    suffix to disambiguate."""
    base = f"{name}.json"
    existing = _servers_dir() / base
    if existing.is_file():
        try:
            data = json.loads(existing.read_text(encoding="utf-8"))
            if data.get("path") == str(path):
                return base
        except (json.JSONDecodeError, OSError):
            pass
        suffix = hashlib.sha1(str(path).encode()).hexdigest()[:6]
        return f"{name}.{suffix}.json"
    return base


def register_server(name: str, path, pid: int, port: int, url: str) -> Path:
    target = _safe_resolve(path)
    _servers_dir().mkdir(parents=True, exist_ok=True)
    entry = {
        "name": name,
        "path": str(target),
        "pid": pid,
        "port": port,
        "url": url,
        "started_at": _now_iso(),
    }

    def _do_register() -> Path:
        # _server_filename + _atomic_write must be done atomically: two
        # concurrent calls for workspaces sharing a name but different paths
        # would both pick <name>.json and race-overwrite each other.
        fname = _server_filename(name, target)
        fpath = _servers_dir() / fname
        _atomic_write(fpath, json.dumps(entry, indent=2))
        return fpath

    return _with_servers_lock(_do_register)


def unregister_server(path) -> bool:
    target_str = str(_safe_resolve(path))
    if not _servers_dir().is_dir():
        return False
    found = False
    for f in _servers_dir().glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("path") == target_str:
            try:
                f.unlink()
                found = True
            except FileNotFoundError:
                pass
    return found


# C.M. 261006: Previously this code used os.kill, which has different behavior
# between Unix and windows. The new call still uses os.kill for Unix under the surface
# but modifies the behavior to also be appropriate for windows. psutil.pid_exists
# reports a dead process as False, not with an error.
def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        return psutil.pid_exists(pid)
    except OSError:
        return False


def find_entry(path) -> dict | None:
    """Return the running-registry entry for path (alive or stale), or None."""
    target_str = str(_safe_resolve(path))
    if not _servers_dir().is_dir():
        return None
    for f in _servers_dir().glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if data.get("path") == target_str:
            return data
    return None


def find_running(path) -> dict | None:
    """Returns the running entry for `path` if its PID is alive, else None."""
    entry = find_entry(path)
    if entry is None:
        return None
    if _pid_alive(int(entry.get("pid", 0))):
        return entry
    return None


def list_servers() -> list[dict]:
    """Return every entry under ~/.pbg/servers/*.json.

    Each entry is augmented with two derived fields:

    * ``_file``  — absolute path to the JSON record on disk.
    * ``_alive`` — bool, result of probing the recorded PID.

    Corrupt or unreadable files are skipped silently. Order is undefined.
    """
    if not _servers_dir().is_dir():
        return []
    out: list[dict] = []
    for f in _servers_dir().glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if not isinstance(data, dict):
            continue
        data["_file"] = str(f)
        data["_alive"] = _pid_alive(int(data.get("pid") or 0))
        out.append(data)
    return out


def find_duplicates_for_path(path) -> list[dict]:
    """Return every server record whose ``path`` matches ``path``.

    Used by ``/viva-workbench start`` to decide whether to dedup a stale entry
    before booting a new server in the same worktree. Multiple records for
    the SAME path are always considered duplicates regardless of liveness;
    records at OTHER paths are intentional (parallel worktrees) and are
    excluded from the result.
    """
    target_str = str(_safe_resolve(path))
    return [e for e in list_servers() if e.get("path") == target_str]


def cleanup_orphans() -> dict:
    """Remove server records whose PID is dead OR whose path no longer exists.

    Returns ``{"removed": [{file, name, path, pid, reason}, ...], "kept": int}``.
    Safe to run repeatedly; never touches files for live processes whose
    worktree path still exists on disk.
    """
    removed: list[dict] = []
    kept = 0

    def _do_cleanup() -> None:
        nonlocal kept
        for entry in list_servers():
            fpath = Path(entry["_file"])
            pid = int(entry.get("pid") or 0)
            path = entry.get("path") or ""
            reasons: list[str] = []
            if not _pid_alive(pid):
                reasons.append("pid-dead")
            if path and not Path(path).is_dir():
                reasons.append("path-missing")
            if reasons:
                try:
                    fpath.unlink()
                    removed.append({
                        "file":   str(fpath),
                        "name":   entry.get("name"),
                        "path":   path,
                        "pid":    pid,
                        "reason": ",".join(reasons),
                    })
                except FileNotFoundError:
                    pass
            else:
                kept += 1

    _with_servers_lock(_do_cleanup)
    return {"removed": removed, "kept": kept}


def remove_server_file(file_path: str | Path) -> bool:
    """Delete one server JSON record by absolute file path.

    Used by ``/viva-workbench start`` to dedup a stale entry before registering
    a new one. Returns True if the file existed and was removed, False
    otherwise. No-op (returns False) if the file is outside the servers
    directory — defensive against path traversal via stale data.
    """
    fpath = Path(file_path).expanduser().resolve()
    servers_root = _servers_dir().resolve()
    try:
        fpath.relative_to(servers_root)
    except ValueError:
        return False

    def _do_remove() -> bool:
        try:
            fpath.unlink()
            return True
        except FileNotFoundError:
            return False

    return _with_servers_lock(_do_remove)


def _main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="discovery_engine.workspace_catalog")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("add")
    p_add.add_argument("--path", required=True)
    p_add.add_argument("--name")
    p_add.add_argument("--package")

    p_forget = sub.add_parser("forget")
    p_forget.add_argument("--path", required=True)

    sub.add_parser("list")

    p_reg = sub.add_parser("register-server")
    p_reg.add_argument("--name", required=True)
    p_reg.add_argument("--path", required=True)
    p_reg.add_argument("--pid", type=int, required=True)
    p_reg.add_argument("--port", type=int, required=True)
    p_reg.add_argument("--url", required=True)

    p_unr = sub.add_parser("unregister-server")
    p_unr.add_argument("--path", required=True)

    sub.add_parser("list-servers")
    sub.add_parser("cleanup-servers")

    p_dup = sub.add_parser("duplicates-for-path")
    p_dup.add_argument("--path", required=True)

    args = parser.parse_args(argv)

    if args.cmd == "add":
        entry = add(args.path, args.name, args.package)
        print(json.dumps(entry))
        return 0
    if args.cmd == "forget":
        ok = forget(args.path)
        return 0 if ok else 1
    if args.cmd == "list":
        print(json.dumps(list_workspaces(), indent=2))
        return 0
    if args.cmd == "register-server":
        register_server(args.name, args.path, args.pid, args.port, args.url)
        return 0
    if args.cmd == "unregister-server":
        unregister_server(args.path)
        return 0
    if args.cmd == "list-servers":
        print(json.dumps(list_servers(), indent=2))
        return 0
    if args.cmd == "cleanup-servers":
        print(json.dumps(cleanup_orphans(), indent=2))
        return 0
    if args.cmd == "duplicates-for-path":
        print(json.dumps(find_duplicates_for_path(args.path), indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(_main())
