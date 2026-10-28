"""Resolve resource directories (`templates/`, `server/`) under either an
editable or a wheel install.

In an editable install the source layout has these dirs at the repo root,
so they live one level above the package: `<repo>/templates/`, `<repo>/server/`.

In a wheel install, `pyproject.toml`'s `[tool.hatch.build.targets.wheel.force-include]`
maps them inside the package as `discovery_engine/_templates/`, `discovery_engine/_server/`.

This helper picks whichever exists.
"""
from __future__ import annotations
from pathlib import Path


_PKG_DIR = Path(__file__).resolve().parent
_REPO_ROOT_GUESS = _PKG_DIR.parent


def resource_dir(name: str) -> Path:
    """Return the path to a resource dir, preferring the wheel layout."""
    bundled = _PKG_DIR / f"_{name}"
    if bundled.is_dir():
        return bundled
    fallback = _REPO_ROOT_GUESS / name
    if fallback.is_dir():
        return fallback
    raise RuntimeError(
        f"pbg-superpowers resource '{name}' not found at {bundled} or {fallback}; "
        "is the plugin installed correctly?"
    )
