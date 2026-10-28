"""Tests for discovery_engine.imports — catalog read/write."""
from pathlib import Path

import pytest
import yaml

from discovery_engine.imports import (
    register_import, list_imports, get_import, unregister_import,
)
from discovery_engine.workspace_yaml import (
    save_workspace, WorkspaceValidationError,
)


def _bootstrap_workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    save_workspace(ws / "workspace.yaml", {
        "schema_version": 2,
        "name": "test",
        "created": "2026-05-09",
        "plugin_version": "0.3.0",
        "stages": {
            "workspace_bootstrap": {"status": "complete", "pr": None, "completed": "2026-05-09"}
        },
    })
    return ws


def test_register_and_get(tmp_path):
    ws = _bootstrap_workspace(tmp_path)
    register_import(
        ws, name="v2ecoli",
        source="https://github.com/eagmon/v2ecoli.git",
        ref="v0.5.2",
        mode="reference",
        path="external/v2ecoli",
        description="Whole-cell E. coli model",
    )
    entry = get_import(ws, "v2ecoli")
    assert entry["source"] == "https://github.com/eagmon/v2ecoli.git"
    assert entry["mode"] == "reference"
    assert entry["path"] == "external/v2ecoli"


def test_list_imports_returns_all(tmp_path):
    ws = _bootstrap_workspace(tmp_path)
    register_import(ws, name="a", source="x", ref="r", mode="reference")
    register_import(ws, name="b", source="y", ref="r", mode="fork-source")
    imports = list_imports(ws)
    assert set(imports.keys()) == {"a", "b"}
    assert imports["b"]["mode"] == "fork-source"


def test_register_refuses_duplicate(tmp_path):
    ws = _bootstrap_workspace(tmp_path)
    register_import(ws, name="x", source="s", ref="r", mode="reference")
    with pytest.raises(WorkspaceValidationError):
        register_import(ws, name="x", source="s2", ref="r2", mode="reference")


def test_register_overwrite_replaces(tmp_path):
    ws = _bootstrap_workspace(tmp_path)
    register_import(ws, name="x", source="s", ref="v1", mode="reference")
    register_import(ws, name="x", source="s", ref="v2", mode="reference", overwrite=True)
    assert get_import(ws, "x")["ref"] == "v2"


def test_unregister_returns_existed(tmp_path):
    ws = _bootstrap_workspace(tmp_path)
    register_import(ws, name="x", source="s", ref="r", mode="reference")
    assert unregister_import(ws, "x") is True
    assert get_import(ws, "x") is None
    # workspace.yaml.imports key removed when empty
    ws_data = yaml.safe_load((ws / "workspace.yaml").read_text())
    assert "imports" not in ws_data


def test_unregister_missing_returns_false(tmp_path):
    ws = _bootstrap_workspace(tmp_path)
    assert unregister_import(ws, "nope") is False


def test_register_validates_mode(tmp_path):
    ws = _bootstrap_workspace(tmp_path)
    with pytest.raises(WorkspaceValidationError):
        register_import(ws, name="x", source="s", ref="r", mode="BOGUS")
