"""Read, write, validate workspace.yaml.

Thin wrapper over :mod:`viva_workspace.workspace_yaml`, which holds the shared
load/validate/save logic. The only plugin-specific part is where the schema
lives: a copy bundled in the package.
"""
from __future__ import annotations

from pathlib import Path

from viva_workspace.workspace_yaml import (
    WorkspaceValidationError,
    load_workspace as _load,
    save_workspace as _save,
    validate_workspace as _validate,
)

__all__ = ["WorkspaceValidationError", "validate_workspace", "load_workspace", "save_workspace"]


def _schema_path() -> Path:
    return Path(__file__).parent / "schemas" / "workspace.schema.json"


def validate_workspace(data: dict) -> None:
    _validate(data, _schema_path())


def load_workspace(path: Path | str) -> dict:
    return _load(path, _schema_path())


def save_workspace(path: Path | str, data: dict) -> None:
    _save(path, data, _schema_path())
