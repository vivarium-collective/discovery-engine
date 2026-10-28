"""Compat shim — the workspace-layout resolver now lives in ``viva-workspace``.

This module used to be a HAND-VENDORED copy of the canonical resolver, kept in
sync by hand (with a drift-guard test) because pbg-superpowers did not depend on
the workbench. That sync burden is gone: the single source of truth is the
standalone, dependency-light ``viva-workspace`` package, which viva-superpowers
now depends on directly.

Everything below is re-exported from :mod:`viva_workspace` under the SAME public
names so existing importers (``from discovery_engine.workspace_paths import
WorkspacePaths``) keep working unchanged. Two behaviours were unified in the
consolidation and are worth noting:

* :meth:`WorkspacePaths.study_dir` (and the module-level :func:`study_dir`) now
  default to ``must_exist=False`` — an absent slug returns its canonical write
  location instead of raising. Pass ``must_exist=True`` for the old raise.
* :func:`package_slug` now returns the canonical ``viva_<slug>`` (was
  ``pbg_<slug>`` before the rebrand). During the migration window use
  :func:`resolve_package_dir` to locate a workspace's package on disk — it still
  recognizes an existing ``pbg_<slug>/`` directory.
"""
from __future__ import annotations

from viva_workspace import (  # noqa: F401 — re-exported for back-compat
    LAYOUT_DEFAULTS,
    LAYOUT_KEYS,
    WorkspacePaths,
    find_workspace_root,
    legacy_package_slug,
    package_slug,
    resolve_package_dir,
    study_dir,
)

__all__ = [
    "LAYOUT_DEFAULTS",
    "LAYOUT_KEYS",
    "WorkspacePaths",
    "find_workspace_root",
    "legacy_package_slug",
    "package_slug",
    "resolve_package_dir",
    "study_dir",
]
