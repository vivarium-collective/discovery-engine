import pytest
import yaml
from discovery_engine.workspace_yaml import (
    load_workspace, save_workspace, validate_workspace,
    WorkspaceValidationError,
)


def test_load_save_roundtrip(tmp_path):
    p = tmp_path / "workspace.yaml"
    data = {
        "schema_version": 2, "name": "x", "created": "2026-05-09",
        "plugin_version": "0.1.0",
        "stages": {"workspace_bootstrap": {"status": "complete", "pr": 1, "completed": "2026-05-09"}},
    }
    save_workspace(p, data)
    loaded = load_workspace(p)
    assert loaded == data


def test_validate_rejects_invalid(tmp_path):
    bad = {"schema_version": 999, "name": "x"}
    with pytest.raises(WorkspaceValidationError):
        validate_workspace(bad)


def test_save_validates_before_writing(tmp_path):
    p = tmp_path / "workspace.yaml"
    bad = {"schema_version": 999, "name": "x"}
    with pytest.raises(WorkspaceValidationError):
        save_workspace(p, bad)
    assert not p.exists(), "file must not be written when validation fails"


def test_validate_rejects_bad_date_format():
    """The schema declares created: format=date. Validator must enforce it."""
    bad = {
        "schema_version": 2, "name": "x", "created": "not-a-date",
        "plugin_version": "0.1.0",
        "stages": {"workspace_bootstrap": {"status": "complete"}},
    }
    with pytest.raises(WorkspaceValidationError):
        validate_workspace(bad)
