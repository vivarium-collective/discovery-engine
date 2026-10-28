"""Tests for discovery_engine.paths.workspace_root."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from discovery_engine.paths import workspace_root


def _make_ws(tmp_path: Path) -> Path:
    """Create a minimal workspace skeleton and return its root."""
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: t\n")
    (ws / "studies" / "demo").mkdir(parents=True)
    return ws


def test_workspace_root_finds_marker_at_start(tmp_path: Path) -> None:
    ws = _make_ws(tmp_path)
    assert workspace_root(ws) == ws


def test_workspace_root_walks_up_from_subdir(tmp_path: Path) -> None:
    ws = _make_ws(tmp_path)
    deep = ws / "studies" / "demo"
    assert workspace_root(deep) == ws


def test_workspace_root_accepts_string(tmp_path: Path) -> None:
    ws = _make_ws(tmp_path)
    assert workspace_root(str(ws / "studies" / "demo")) == ws


def test_workspace_root_accepts_file_path(tmp_path: Path) -> None:
    """If start is a file, walk up from its parent directory."""
    ws = _make_ws(tmp_path)
    f = ws / "studies" / "demo" / "spec.yaml"
    f.write_text("")
    assert workspace_root(f) == ws


def test_workspace_root_raises_when_no_marker(tmp_path: Path) -> None:
    isolated = tmp_path / "nope"
    isolated.mkdir()
    with pytest.raises(FileNotFoundError):
        workspace_root(isolated)


def test_workspace_root_resolves_symlinks(tmp_path: Path) -> None:
    ws = _make_ws(tmp_path)
    link = tmp_path / "ws_link"
    link.symlink_to(ws)
    assert workspace_root(link / "studies" / "demo") == ws.resolve()


def test_workspace_root_auto_detects_caller_file(tmp_path: Path, monkeypatch) -> None:
    """When start=None, _getframe must point at the script that called us."""
    ws = _make_ws(tmp_path)
    script = ws / "studies" / "demo" / "_probe.py"
    script.write_text(
        textwrap.dedent(
            """
            from discovery_engine.paths import workspace_root
            print(workspace_root())
            """
        ).strip()
    )

    import subprocess
    import sys as _sys

    out = subprocess.check_output(
        [_sys.executable, str(script)], text=True
    ).strip()
    assert out == str(ws)


def test_back_compat_find_workspace_root(tmp_path: Path) -> None:
    """The old API still works (delegates to workspace_root)."""
    from discovery_engine.paths import find_workspace_root

    ws = _make_ws(tmp_path)
    assert find_workspace_root(ws / "studies" / "demo") == ws
