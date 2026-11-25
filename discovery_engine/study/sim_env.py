"""Per-study simulator environments (RFC-0008 Phase 2, materialization).

A study may pin its own simulator (``study.yaml simulator: {package, ref}``, RFC-0008).
When studies in one investigation pin *different* simulators/versions, they can't share
one venv — each pin needs its own environment. This module turns a pin into a
reproducible, cached environment **built with uv**, keyed so the same pin reuses the
same env across studies and runs.

This is the first, deliberately bounded slice: a pure planner (:func:`plan_study_env`)
plus an **opt-in** builder (:func:`materialize_study_env`, ``execute=False`` by default).
Nothing here is wired into the hot run path yet — the run subprocess still uses the
workspace venv. Wiring, git-ref resolution, and investigation-level orchestration across
mixed envs are later slices (see RFC-0008 Phase 2/3).

Domain-free: it takes a ``{package, ref}`` pin, never a specific simulator name.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
from pathlib import Path


def env_cache_root() -> Path:
    """Where materialized per-study envs are cached. Override with
    ``VIVA_STUDY_ENV_CACHE``; defaults under the user cache dir."""
    override = os.environ.get("VIVA_STUDY_ENV_CACHE")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or (Path.home() / ".cache")
    return Path(base) / "viva" / "study-envs"


def env_key(pin: dict, python: str | None = None) -> str:
    """Stable 12-hex key identifying a pin's environment: the package, the ref, and
    the interpreter it is built for. The same pin reuses the same cached env."""
    package = (pin or {}).get("package")
    ref = (pin or {}).get("ref")
    py = python or platform.python_version()
    ident = json.dumps({"package": package, "ref": ref, "python": py},
                       sort_keys=True, default=str)
    return hashlib.sha256(ident.encode("utf-8")).hexdigest()[:12]


def _requirement(package: str, ref: str | None) -> str:
    """The uv/pip requirement for a pin. A version ref -> ``package==ref``; no ref
    -> bare ``package``. (A git-SHA ref needs the package's repo URL to resolve and
    is a later slice — here it degrades to the bare package so the build still runs.)"""
    if ref and _looks_like_version(ref):
        return f"{package}=={ref}"
    return package


def _looks_like_version(ref: str) -> bool:
    # a version ref is digits/dots with optional pre/post tags; a 7-40 hex string
    # is a git sha, which pip cannot resolve without the repo URL.
    r = ref.strip()
    if r and all(c in "0123456789abcdef" for c in r.lower()) and 7 <= len(r) <= 40:
        return False
    return bool(r) and r[0].isdigit()


def plan_study_env(pin: dict, python: str | None = None) -> dict:
    """Resolve a pin to its environment plan WITHOUT building anything:
    ``{package, ref, env_key, env_dir, requirement, uv_commands, cached, git_ref}``.
    Pure and side-effect-free — safe to call anywhere. ``cached`` reports whether the
    env already exists on disk."""
    package = (pin or {}).get("package")
    ref = (pin or {}).get("ref")
    key = env_key(pin, python)
    env_dir = env_cache_root() / key
    req = _requirement(package, ref) if isinstance(package, str) and package else None
    git_ref = bool(ref) and not _looks_like_version(ref)
    uv_commands = []
    if req:
        py_bin = env_dir / "bin" / "python"
        uv_commands = [
            ["uv", "venv", str(env_dir)],
            ["uv", "pip", "install", "--python", str(py_bin), req],
        ]
    return {
        "package": package, "ref": ref, "env_key": key, "env_dir": str(env_dir),
        "requirement": req, "uv_commands": uv_commands,
        "cached": (env_dir / "bin" / "python").exists(),
        "git_ref": git_ref,
    }


def materialize_study_env(pin: dict, *, execute: bool = False,
                          python: str | None = None, timeout: int = 600) -> dict:
    """Return a pin's :func:`plan_study_env`, and — only when ``execute=True`` —
    build the environment with uv if it isn't cached. Returns the plan plus
    ``{built: bool, python_path: str|None, error: str|None}``.

    ``execute`` defaults to False so this never builds by surprise; a caller opts in
    explicitly. A git-SHA ref isn't resolvable without the package's repo URL yet, so
    it is not built (``error`` says so) — a later slice adds registry-backed git refs.
    """
    plan = plan_study_env(pin, python)
    out = {**plan, "built": False, "python_path": None, "error": None}
    py_bin = Path(plan["env_dir"]) / "bin" / "python"
    if plan["cached"]:
        out["python_path"] = str(py_bin)
        return out
    if not execute:
        return out
    if not plan["requirement"]:
        out["error"] = "pin has no package to install"
        return out
    if plan["git_ref"]:
        out["error"] = ("git-sha ref not resolvable without the package's repo URL "
                        "(registry-backed git refs are a later RFC-0008 slice)")
        return out
    try:
        for cmd in plan["uv_commands"]:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                              check=False)
            if r.returncode != 0:
                out["error"] = (r.stderr or r.stdout or "uv command failed").strip()[:500]
                return out
        out["built"] = True
        out["python_path"] = str(py_bin)
    except Exception as e:  # noqa: BLE001 — a build failure is reported, never raised
        out["error"] = str(e)
    return out
