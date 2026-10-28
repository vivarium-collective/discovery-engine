"""Tests for discovery_engine/investigation_close.py — S5 close-investigation
workflow.

Covers:
- derive_contributors(): parses `git log main..branch` to extract human
  authors; counts commits per author; merges Co-Authored-By trailers as
  agent contributors (when the email indicates a bot/agent).
- derive_contributors() with .pbg/agent-sessions/<id>.json files —
  agents from session files are added or merged into existing entries.
- close_investigation() dry-run: writes nothing, no commit, no PR.
- close_investigation() write path: stamps status=closed, closed_at,
  report_url, contributors[] on investigation.yaml; commits on the
  branch.
- close_investigation() preserves user-edited contributor entries
  (`roles`, `notes`) and only refreshes derived sub-fields (commits,
  sessions) on subsequent runs.
- Wrong branch / missing investigation surface clean errors.

Skips PR creation (`gh pr create`) — tests pass `auto_pr=False` so they
don't depend on a remote.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from discovery_engine.investigation_close import (
    close_investigation,
    derive_contributors,
)


# ---------------------------------------------------------------------------
# Fixture: a fully-formed pbg workspace inside a git repo with the
# Investigation ≡ branch convention.
# ---------------------------------------------------------------------------


def _git(ws: Path, *args: str, env: dict[str, str] | None = None) -> str:
    """Run a git command in ws and return stdout (raising on failure)."""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    proc = subprocess.run(
        ["git", "-C", str(ws), *args],
        capture_output=True, text=True, check=True, env=full_env,
    )
    return proc.stdout


@pytest.fixture
def ws_with_investigation(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    # workspace.yaml + initial commit on main.
    (ws / "workspace.yaml").write_text(
        "schema_version: 2\nname: ws\nplugin_version: 0.6.1\n"
    )
    _git(ws, "init", "-q", "-b", "main")
    _git(ws, "config", "user.name", "Initial Author")
    _git(ws, "config", "user.email", "init@example.com")
    _git(ws, "add", "workspace.yaml")
    _git(ws, "commit", "-q", "-m", "init")

    # Branch off + create an investigation.
    _git(ws, "checkout", "-q", "-b", "my-inv")
    inv_dir = ws / "investigations" / "my-inv"
    inv_dir.mkdir(parents=True)
    (inv_dir / "investigation.yaml").write_text(yaml.safe_dump({
        "schema_version": 2,
        "name": "my-inv",
        "title": "My Investigation",
        "status": "planning",
        "studies": [],
    }))
    _git(ws, "add", "investigations/my-inv/investigation.yaml")
    _git(ws, "commit", "-q", "-m", "feat(investigation): scaffold my-inv",
         env={"GIT_AUTHOR_NAME": "Alice", "GIT_AUTHOR_EMAIL": "alice@example.com",
              "GIT_COMMITTER_NAME": "Alice", "GIT_COMMITTER_EMAIL": "alice@example.com"})

    # Add a few more commits with different authors.
    (inv_dir / "notes.md").write_text("First note.\n")
    _git(ws, "add", "investigations/my-inv/notes.md")
    _git(ws, "commit", "-q", "-m", "docs: notes",
         env={"GIT_AUTHOR_NAME": "Alice", "GIT_AUTHOR_EMAIL": "alice@example.com",
              "GIT_COMMITTER_NAME": "Alice", "GIT_COMMITTER_EMAIL": "alice@example.com"})

    (inv_dir / "more.md").write_text("More.\n")
    _git(ws, "add", "investigations/my-inv/more.md")
    _git(ws, "commit", "-q", "-m", "docs: more",
         env={"GIT_AUTHOR_NAME": "Bob", "GIT_AUTHOR_EMAIL": "bob@example.com",
              "GIT_COMMITTER_NAME": "Bob", "GIT_COMMITTER_EMAIL": "bob@example.com"})

    return ws


# ---------------------------------------------------------------------------
# derive_contributors
# ---------------------------------------------------------------------------


class TestDeriveContributors:
    def test_aggregates_by_author(self, ws_with_investigation):
        contribs = derive_contributors(ws_with_investigation, "my-inv")
        # Two distinct authors: Alice (3 commits — scaffold, notes, +) and Bob (1).
        names = sorted(c["name"] for c in contribs)
        assert names == ["Alice", "Bob"]
        alice = next(c for c in contribs if c["name"] == "Alice")
        bob = next(c for c in contribs if c["name"] == "Bob")
        assert alice["commits"] == 2  # scaffold and notes are both Alice
        assert bob["commits"] == 1
        # All defaults populated.
        for c in contribs:
            assert c["kind"] == "human"
            assert c["roles"] == ["implementer"]

    def test_empty_when_branch_no_diff_from_main(self, ws_with_investigation):
        # Switch to main; running with same range should yield 0 contributors.
        _git(ws_with_investigation, "checkout", "-q", "main")
        contribs = derive_contributors(ws_with_investigation, "main")
        assert contribs == []

    def test_picks_up_co_authored_by_agent(self, ws_with_investigation):
        # Add a commit with Co-Authored-By trailer.
        inv_dir = ws_with_investigation / "investigations" / "my-inv"
        (inv_dir / "agent-touched.md").write_text("agent-touched\n")
        _git(ws_with_investigation, "add",
             "investigations/my-inv/agent-touched.md")
        msg = (
            "feat: agent contribution\n\n"
            "Co-Authored-By: Claude Opus 4.7 <noreply@anthropic.com>"
        )
        _git(ws_with_investigation, "commit", "-q", "-m", msg,
             env={"GIT_AUTHOR_NAME": "Alice", "GIT_AUTHOR_EMAIL": "alice@example.com",
                  "GIT_COMMITTER_NAME": "Alice", "GIT_COMMITTER_EMAIL": "alice@example.com"})
        contribs = derive_contributors(ws_with_investigation, "my-inv")
        agent = next(
            (c for c in contribs if "Claude" in c["name"]),
            None,
        )
        assert agent is not None
        assert agent["kind"] == "agent"
        assert agent["roles"] == ["agent_runner"]

    def test_merges_agent_sessions(self, ws_with_investigation):
        # Drop an agent-sessions file naming an agent.
        sd = ws_with_investigation / ".pbg" / "agent-sessions"
        sd.mkdir(parents=True)
        (sd / "abc.json").write_text(json.dumps({
            "agent_name": "Claude Opus 4.7 (1M context)",
            "session_id": "abc-123-def",
        }))
        (sd / "xyz.json").write_text(json.dumps({
            "agent_name": "Claude Opus 4.7 (1M context)",
            "session_id": "xyz-456",
        }))
        contribs = derive_contributors(ws_with_investigation, "my-inv")
        agent = next(c for c in contribs if c["name"].startswith("Claude"))
        assert agent["kind"] == "agent"
        assert sorted(agent["sessions"]) == ["abc-123-def", "xyz-456"]


# ---------------------------------------------------------------------------
# close_investigation
# ---------------------------------------------------------------------------


class TestCloseInvestigationDryRun:
    def test_dry_run_writes_nothing(self, ws_with_investigation):
        before = (ws_with_investigation / "investigations" / "my-inv"
                  / "investigation.yaml").read_text()
        result = close_investigation(
            ws_with_investigation, "my-inv",
            dry_run=True, auto_pr=False, skip_report=True,
        )
        after = (ws_with_investigation / "investigations" / "my-inv"
                 / "investigation.yaml").read_text()
        assert before == after
        assert result.dry_run is True
        # No git_commit action when dry-run + no actual write.
        action_kinds = [a.kind for a in result.actions]
        assert "derive_contributors" in action_kinds
        # All non-skip actions are tagged "(dry-run)" in their detail text.
        for a in result.actions:
            if a.kind in {"render_report", "copy_report", "update_yaml", "git_commit", "gh_pr_create"}:
                assert "dry-run" in a.detail.lower()


class TestCloseInvestigationWrite:
    def test_stamps_yaml_and_commits(self, ws_with_investigation):
        result = close_investigation(
            ws_with_investigation, "my-inv",
            auto_pr=False, skip_report=True,
        )
        assert result.dry_run is False
        # YAML stamped.
        spec = yaml.safe_load((ws_with_investigation / "investigations"
                               / "my-inv" / "investigation.yaml").read_text())
        assert spec["status"] == "closed"
        assert "closed_at" in spec
        assert spec["report_url"] == "report.html"
        names = sorted(c["name"] for c in spec["contributors"])
        assert names == ["Alice", "Bob"]
        # Commit landed on the my-inv branch.
        head_msg = _git(ws_with_investigation, "log", "-1", "--pretty=%s").strip()
        assert head_msg == "close(investigation): my-inv"

    def test_preserves_user_edited_contributors(self, ws_with_investigation):
        # First close.
        close_investigation(
            ws_with_investigation, "my-inv",
            auto_pr=False, skip_report=True,
        )
        # User edits Alice's contributor entry (adds notes + roles).
        inv_path = (ws_with_investigation / "investigations" / "my-inv"
                    / "investigation.yaml")
        spec = yaml.safe_load(inv_path.read_text())
        alice = next(c for c in spec["contributors"] if c["name"] == "Alice")
        alice["roles"] = ["designer", "implementer", "reviewer"]
        alice["notes"] = "Led the dnaa-replication arc."
        inv_path.write_text(yaml.safe_dump(spec, sort_keys=False))
        _git(ws_with_investigation, "add",
             "investigations/my-inv/investigation.yaml")
        _git(ws_with_investigation, "commit", "-q", "-m", "edit alice's notes",
             env={"GIT_AUTHOR_NAME": "Alice", "GIT_AUTHOR_EMAIL": "alice@example.com",
                  "GIT_COMMITTER_NAME": "Alice", "GIT_COMMITTER_EMAIL": "alice@example.com"})
        # Re-close. The user edits should survive.
        close_investigation(
            ws_with_investigation, "my-inv",
            auto_pr=False, skip_report=True,
        )
        spec = yaml.safe_load(inv_path.read_text())
        alice = next(c for c in spec["contributors"] if c["name"] == "Alice")
        assert alice["roles"] == ["designer", "implementer", "reviewer"]
        assert alice["notes"] == "Led the dnaa-replication arc."


class TestErrors:
    def test_missing_investigation(self, ws_with_investigation):
        with pytest.raises(FileNotFoundError, match="not found"):
            close_investigation(
                ws_with_investigation, "no-such-inv",
                auto_pr=False, skip_report=True,
            )

    def test_missing_branch(self, ws_with_investigation):
        # Delete the my-inv branch (after switching off it).
        _git(ws_with_investigation, "checkout", "-q", "main")
        _git(ws_with_investigation, "branch", "-D", "my-inv")
        with pytest.raises(FileNotFoundError, match="branch"):
            close_investigation(
                ws_with_investigation, "my-inv",
                auto_pr=False, skip_report=True,
            )
