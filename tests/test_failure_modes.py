"""Failure-mode tests covering the testable in-process portions of spec §13.

Many of the §13 error modes are runtime-only (gh CLI, network, dashboard server,
submodule pointer divergence) and are exercised by the SKILL.md walkthroughs at
runtime, not by L1 tests. The cases below are the ones with pure-Python entry
points: schema corruption."""
import pytest

from discovery_engine.workspace_yaml import (
    load_workspace, save_workspace, WorkspaceValidationError,
)


def test_save_refuses_malformed_workspace_yaml(tmp_path):
    """Per spec §13: 'workspace.yaml malformed' → refuse to mutate; preserve invalid file."""
    p = tmp_path / "workspace.yaml"
    with pytest.raises(WorkspaceValidationError):
        save_workspace(p, {"schema_version": "BAD"})
    assert not p.exists(), "save must NOT write the file when validation fails"


def test_load_rejects_truncated_workspace_yaml(tmp_path):
    """A YAML parse error or schema mismatch must surface — not silently load partial state."""
    p = tmp_path / "workspace.yaml"
    p.write_text("schema_version: 1\nname:\nbroken_token::")  # invalid YAML
    with pytest.raises(Exception):
        load_workspace(p)
