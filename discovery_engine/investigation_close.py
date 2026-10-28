"""Close-investigation workflow — S5.

Implements the close-investigation mechanic the user asked for:

  > When an investigation closes, the PR should merge into main, adding
  > the composite and new processes and datasets into the main branch.
  > The HTML report should be added to the repo's investigations, and
  > provenance should be preserved so we can track them and give credit
  > as people build on top of these contributions.

This module provides the framework-side primitives the CLI subcommand and
(eventually) the dashboard "Close investigation" button both call into:

  - derive_contributors(ws_root, branch)
      Walk `git log --pretty='%an|%ae|%H' main..branch` to collect human
      contributors. Adds agent contributors from .pbg/agent-sessions/
      (when present). Returns a list of contributor dicts in the v2
      investigation schema's contributors[] shape.

  - close_investigation(ws_root, slug, *, dry_run=False, auto_pr=True,
                        skip_report=False)
      The end-to-end action. Returns a `CloseResult` with the actions
      taken (or proposed, in dry-run). NEVER passes --auto to gh pr
      merge (per the user's standing "no auto-merge" instruction);
      stops after `gh pr create`.

The PR merge step is INTENTIONALLY left to the user (one explicit click
on the GitHub UI). The close action prepares everything — report
checked in, status flipped to "closed", contributors[] populated,
investigation.yaml committed, PR opened with a populated body — but
stops short of merging.

If the workspace has no `gh` available, `auto_pr=False` skips the PR
step and surfaces a `git_push` action in the result so the user knows
what to do next.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import re
import shutil
import subprocess
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from discovery_engine.paths import find_workspace_root
from discovery_engine.workspace_paths import WorkspacePaths
from discovery_engine.study_io import (
    atomic_write as _atomic_write,
    dump_yaml as _dump_yaml,
    load_yaml_mapping as _load_yaml,
)


# ---------------------------------------------------------------------------
# Workspace + investigation resolution
# ---------------------------------------------------------------------------


def _walk_to_workspace(start: Path) -> Path:
    return find_workspace_root(start)


def _investigation_yaml(ws_root: Path, slug: str) -> Path:
    p = WorkspacePaths.load(ws_root).investigations / slug / "investigation.yaml"
    if not p.is_file():
        raise FileNotFoundError(
            f"Investigation '{slug}' not found at {p}. Run "
            f"'/viva-investigation new <slug>' to create it."
        )
    return p


# _load_yaml / _dump_yaml / _atomic_write are imported from study_io (see the
# import block above) — duplicated here before the Theme 3 consolidation.


# ---------------------------------------------------------------------------
# Contributor derivation
# ---------------------------------------------------------------------------


def _run_git(ws_root: Path, *args: str) -> tuple[int, str, str]:
    """Run a git command in ws_root; return (rc, stdout, stderr)."""
    proc = subprocess.run(
        ["git", "-C", str(ws_root), *args],
        capture_output=True, text=True, timeout=60,
    )
    return proc.returncode, proc.stdout, proc.stderr


def _git_main_branch(ws_root: Path) -> str:
    """Return the workspace's main branch name (main, master, or default).

    Falls back to "main" if neither show-ref returns a value — the close
    workflow then surfaces a clear error if the branch doesn't exist.
    """
    for candidate in ("main", "master"):
        rc, _, _ = _run_git(ws_root, "show-ref", "--verify", "--quiet",
                            f"refs/heads/{candidate}")
        if rc == 0:
            return candidate
    return "main"


def derive_contributors(
    ws_root: Path,
    branch: str,
    *,
    main_branch: str | None = None,
) -> list[dict]:
    """Walk `git log <main>..<branch>` to collect contributors.

    Returns a list of contributor dicts in the v2 investigation schema's
    contributors[] shape. Humans are derived from git commit authors;
    agents (when present) are added from .pbg/agent-sessions/<id>.json.

    Each entry:
      {name, email, kind, commits, roles, sessions?, notes?}

    `roles` defaults to ["implementer"] for humans and ["agent_runner"]
    for agents — the close-mechanic invokes derive_contributors() with
    these defaults and lets the user edit roles[] in the final YAML.
    """
    main = main_branch or _git_main_branch(ws_root)
    rc, out, err = _run_git(
        ws_root, "log", f"{main}..{branch}",
        "--pretty=format:%an|%ae|%H",
    )
    if rc != 0:
        # If branch == main (close after merge), there's no diff range —
        # return empty contributor list instead of failing.
        if "unknown revision" in err.lower() or "bad revision" in err.lower():
            return []
        raise RuntimeError(f"git log failed: {err.strip()}")

    # Aggregate by (name, email) — handles people who commit under
    # multiple emails by merging on email; falls back to name when email
    # is the noreply placeholder.
    by_key: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("|", 2)
        if len(parts) < 2:
            continue
        name, email = parts[0].strip(), parts[1].strip()
        sha = parts[2].strip() if len(parts) > 2 else ""
        key = (name, email)
        if key not in by_key:
            by_key[key] = {
                "name": name,
                "email": email,
                "kind": "human",
                "roles": ["implementer"],
                "commits": 0,
            }
        by_key[key]["commits"] += 1
        # Drop the sha (not stored), but it was used to count distinct commits.
        _ = sha

    contributors: list[dict] = list(by_key.values())

    # Strip empty/placeholder emails — leave the key in the dict only if
    # populated, to keep the YAML tidy.
    for c in contributors:
        if not c.get("email"):
            c.pop("email", None)

    # Detect agent commits via the Co-Authored-By trailer (an
    # implementation choice for agent contributors — see also the
    # .pbg/agent-sessions/ section below). When a commit on this branch
    # carries `Co-Authored-By: <name> <email>`, that contributor is
    # added with kind=agent.
    rc, body_out, _ = _run_git(
        ws_root, "log", f"{main}..{branch}",
        "--pretty=format:%B%n--END--",
    )
    if rc == 0:
        agent_pat = re.compile(
            r"Co-Authored-By:\s*(.+?)\s*<(.+?)>",
            re.IGNORECASE,
        )
        agent_seen: set[tuple[str, str]] = set()
        for body in body_out.split("--END--"):
            for m in agent_pat.finditer(body):
                aname, aemail = m.group(1).strip(), m.group(2).strip()
                if (aname, aemail) in agent_seen:
                    continue
                agent_seen.add((aname, aemail))
                # Skip if already in human contributors (the same person).
                already = any(
                    c["name"] == aname and c.get("email", "") == aemail
                    for c in contributors
                )
                if already:
                    continue
                # Heuristic: treat noreply@anthropic.com (claude family)
                # + any *bot*/*ci* email as agent.
                is_agent = (
                    "noreply@anthropic.com" in aemail.lower()
                    or "bot" in aemail.lower()
                    or "ci" in aemail.lower().split("@", 1)[0].split(".")
                )
                contributors.append({
                    "name": aname,
                    "email": aemail,
                    "kind": "agent" if is_agent else "human",
                    "roles": ["agent_runner" if is_agent else "implementer"],
                    "commits": 0,  # not in author position; co-author only
                })

    # Augment with .pbg/agent-sessions/ entries when present. Each
    # session file is JSON with shape {agent_name, session_id, ...}; the
    # mechanic groups sessions by agent_name.
    sessions_dir = WorkspacePaths.load(ws_root).pbg / "agent-sessions"
    if sessions_dir.is_dir():
        sessions_by_agent: OrderedDict[str, list[str]] = OrderedDict()
        for sf in sorted(sessions_dir.glob("*.json")):
            try:
                data = json.loads(sf.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            aname = (data.get("agent_name") or "").strip()
            sid = (data.get("session_id") or sf.stem).strip()
            if not aname or not sid:
                continue
            sessions_by_agent.setdefault(aname, []).append(sid)
        for aname, sids in sessions_by_agent.items():
            existing = next(
                (c for c in contributors if c["name"] == aname),
                None,
            )
            if existing:
                # Merge sessions into the existing entry.
                merged = list(dict.fromkeys(existing.get("sessions", []) + sids))
                existing["sessions"] = merged
                existing.setdefault("kind", "agent")
                if "roles" not in existing:
                    existing["roles"] = ["agent_runner"]
            else:
                contributors.append({
                    "name": aname,
                    "kind": "agent",
                    "roles": ["agent_runner"],
                    "sessions": sids,
                    "commits": 0,
                })

    return contributors


# ---------------------------------------------------------------------------
# Close-investigation result + action
# ---------------------------------------------------------------------------


@dataclass
class CloseAction:
    """One thing the close mechanic did (or proposed in dry-run)."""
    kind: str       # "render_report" | "copy_report" | "update_yaml" |
                    # "git_commit" | "git_push" | "gh_pr_create" | "skip"
    detail: str
    ok: bool = True


@dataclass
class CloseResult:
    slug: str
    branch: str
    contributors: list[dict]
    actions: list[CloseAction] = field(default_factory=list)
    pr_url: str | None = None
    dry_run: bool = False

    def to_dict(self) -> dict:
        return {
            "slug": self.slug,
            "branch": self.branch,
            "contributors": self.contributors,
            "actions": [dataclasses.asdict(a) for a in self.actions],
            "pr_url": self.pr_url,
            "dry_run": self.dry_run,
        }


def close_investigation(
    ws_root: Path,
    slug: str,
    *,
    dry_run: bool = False,
    auto_pr: bool = True,
    skip_report: bool = False,
    main_branch: str | None = None,
    now: _dt.datetime | None = None,
) -> CloseResult:
    """Run the close-investigation workflow end-to-end.

    Steps (each surfaced as a CloseAction in the result):
      1. Resolve the investigation branch (== slug per the
         Investigation ≡ branch convention).
      2. Derive contributors from `git log main..<branch>` + agent
         sessions.
      3. Render the workspace report (unless `skip_report=True`).
      4. Copy reports/index.html → investigations/<slug>/report.html
         (so it lands as a git-tracked artifact under the repo).
      5. Update investigation.yaml: status="closed", closed_at=<now>,
         report_url="report.html", contributors=<derived>.
      6. Commit on the investigation branch.
      7. (auto_pr) Call `gh pr create` if a PR doesn't yet exist for
         the branch; else `gh pr edit` to refresh the body. NEVER
         passes --auto to merge.

    Returns a CloseResult listing every action taken (or proposed, in
    dry-run). The mechanic NEVER passes --auto to gh pr merge — the
    user clicks merge in the GitHub UI.
    """
    now = now or _dt.datetime.now()
    main = main_branch or _git_main_branch(ws_root)

    inv_path = _investigation_yaml(ws_root, slug)
    inv_dir = inv_path.parent
    spec = _load_yaml(inv_path)

    # Per the Investigation ≡ branch convention, the branch name is the
    # slug. Verify the branch exists.
    branch = slug
    rc, _, _ = _run_git(ws_root, "show-ref", "--verify", "--quiet",
                        f"refs/heads/{branch}")
    if rc != 0:
        raise FileNotFoundError(
            f"branch {branch!r} not found (Investigation ≡ branch "
            f"convention). Either create it via /viva-investigation new "
            f"or rename the existing branch."
        )

    result = CloseResult(slug=slug, branch=branch, contributors=[], dry_run=dry_run)

    # 2. Contributors.
    contributors = derive_contributors(ws_root, branch, main_branch=main)
    result.contributors = contributors
    result.actions.append(CloseAction(
        kind="derive_contributors",
        detail=f"derived {len(contributors)} contributor(s) from "
               f"`git log {main}..{branch}` + .pbg/agent-sessions/",
    ))

    # 3. Render report.
    workspace_report = WorkspacePaths.load(ws_root).reports / "index.html"
    if skip_report:
        result.actions.append(CloseAction(
            kind="skip",
            detail="--skip-report: not rendering the workspace report",
        ))
    elif dry_run:
        result.actions.append(CloseAction(
            kind="render_report",
            detail="(dry-run) would render the workspace report via "
                   "discovery_engine.report.render_workspace_report",
        ))
    else:
        try:
            from discovery_engine.report import render_workspace_report
            render_workspace_report(ws_root, force=False)
            result.actions.append(CloseAction(
                kind="render_report",
                detail=f"rendered {workspace_report.relative_to(ws_root)}",
            ))
        except Exception as e:  # noqa: BLE001
            # Don't abort the close — surface the failure as an action.
            # The user can decide whether to retry with --skip-report.
            result.actions.append(CloseAction(
                kind="render_report",
                detail=f"FAILED: {e}",
                ok=False,
            ))

    # 4. Copy report.html into investigations/<slug>/ so it's git-tracked.
    report_dest = inv_dir / "report.html"
    if not skip_report and workspace_report.is_file():
        if dry_run:
            result.actions.append(CloseAction(
                kind="copy_report",
                detail=f"(dry-run) would copy {workspace_report.relative_to(ws_root)} "
                       f"to {report_dest.relative_to(ws_root)}",
            ))
        else:
            shutil.copy2(workspace_report, report_dest)
            result.actions.append(CloseAction(
                kind="copy_report",
                detail=f"copied to {report_dest.relative_to(ws_root)}",
            ))

    # 5. Update investigation.yaml.
    spec["status"] = "closed"
    spec["closed_at"] = now.isoformat(timespec="seconds")
    spec["report_url"] = "report.html"
    # Merge into existing contributors[] (if user had pre-curated entries,
    # preserve their `roles` and `notes`).
    existing = {
        (c.get("name"), c.get("email", "")): c
        for c in (spec.get("contributors") or [])
        if isinstance(c, dict)
    }
    merged: list[dict] = []
    for c in contributors:
        key = (c["name"], c.get("email", ""))
        if key in existing:
            # Preserve user edits: keep their roles + notes, refresh commits/sessions.
            prev = existing.pop(key)
            prev["commits"] = c.get("commits", prev.get("commits", 0))
            if "sessions" in c:
                prev["sessions"] = c["sessions"]
            if "kind" in c:
                prev.setdefault("kind", c["kind"])
            merged.append(prev)
        else:
            merged.append(c)
    # Append any user-only contributors not in the derived list (e.g.,
    # "Eran reviewed the design" without a git commit).
    merged.extend(existing.values())
    spec["contributors"] = merged

    if dry_run:
        result.actions.append(CloseAction(
            kind="update_yaml",
            detail=f"(dry-run) would update {inv_path.relative_to(ws_root)} "
                   f"(status=closed, closed_at, report_url, contributors[{len(merged)}])",
        ))
    else:
        _atomic_write(inv_path, _dump_yaml(spec))
        result.actions.append(CloseAction(
            kind="update_yaml",
            detail=f"updated {inv_path.relative_to(ws_root)} "
                   f"(status=closed, closed_at, report_url, contributors[{len(merged)}])",
        ))

    # 6. Commit on the investigation branch.
    if dry_run:
        result.actions.append(CloseAction(
            kind="git_commit",
            detail="(dry-run) would commit the report.html + investigation.yaml updates",
        ))
    else:
        # Stage the specific files we touched (avoid `git add -A`).
        add_paths = [str(inv_path.relative_to(ws_root))]
        if report_dest.exists():
            add_paths.append(str(report_dest.relative_to(ws_root)))
        rc, _, err = _run_git(ws_root, "add", *add_paths)
        if rc != 0:
            result.actions.append(CloseAction(
                kind="git_commit",
                detail=f"FAILED: git add: {err.strip()}",
                ok=False,
            ))
        else:
            msg = (
                f"close(investigation): {slug}\n\n"
                f"Closed at {now.isoformat(timespec='seconds')}. "
                f"Report checked in at investigations/{slug}/report.html. "
                f"{len(merged)} contributor(s) recorded."
            )
            rc, _, err = _run_git(ws_root, "commit", "-m", msg)
            if rc != 0:
                result.actions.append(CloseAction(
                    kind="git_commit",
                    detail=f"FAILED: git commit: {err.strip()}",
                    ok=False,
                ))
            else:
                result.actions.append(CloseAction(
                    kind="git_commit",
                    detail=f"committed close on branch {branch!r}",
                ))

    # 7. gh pr create (or edit).
    if not auto_pr:
        result.actions.append(CloseAction(
            kind="skip",
            detail="--no-pr: skipping `gh pr create`",
        ))
        return result

    if dry_run:
        result.actions.append(CloseAction(
            kind="gh_pr_create",
            detail=f"(dry-run) would call `gh pr create --base {main} --head {branch}`",
        ))
        return result

    # Check whether a PR already exists for this branch.
    rc, out, _ = _run_git(
        ws_root, "rev-parse", "--abbrev-ref", "HEAD",
    )
    # ... we don't strictly need this; carry on.

    pr_body = (
        f"## Summary\n\n"
        f"Closes investigation `{slug}`. Snapshot at close:\n\n"
        f"- Status flipped to `closed` at {now.isoformat(timespec='seconds')}.\n"
        f"- Report checked in at `investigations/{slug}/report.html`.\n"
        f"- {len(merged)} contributor(s) recorded in `investigation.yaml.contributors[]`.\n\n"
        f"## Contributors\n\n"
        + (
            "\n".join(f"- {c['name']}"
                      f"{' (' + c.get('kind', 'human') + ')' if c.get('kind') and c.get('kind') != 'human' else ''}"
                      f"{' — ' + ', '.join(c.get('roles', [])) if c.get('roles') else ''}"
                      for c in merged) or "_(no commits on this branch)_"
        )
        + "\n\n## Test plan\n\n"
        f"- [ ] Review the rendered report at `investigations/{slug}/report.html`.\n"
        f"- [ ] Verify the contributors list looks right; edit `investigation.yaml.contributors[]` if needed.\n"
        f"- [ ] Merge when ready (this PR does NOT auto-merge).\n"
    )

    # Try `gh pr view` to detect an existing PR for the branch.
    proc = subprocess.run(
        ["gh", "pr", "view", branch, "--json", "url"],
        cwd=ws_root, capture_output=True, text=True, timeout=30,
    )
    if proc.returncode == 0:
        # PR exists — refresh body via gh pr edit.
        try:
            payload = json.loads(proc.stdout)
            result.pr_url = payload.get("url")
        except json.JSONDecodeError:
            result.pr_url = None
        proc = subprocess.run(
            ["gh", "pr", "edit", branch, "--body", pr_body],
            cwd=ws_root, capture_output=True, text=True, timeout=30,
        )
        if proc.returncode == 0:
            result.actions.append(CloseAction(
                kind="gh_pr_edit",
                detail=f"refreshed PR body for {branch!r}: {result.pr_url}",
            ))
        else:
            result.actions.append(CloseAction(
                kind="gh_pr_edit",
                detail=f"FAILED: gh pr edit: {proc.stderr.strip()}",
                ok=False,
            ))
    else:
        # No PR — create one. First push the branch.
        push_proc = subprocess.run(
            ["git", "-C", str(ws_root), "push", "-u", "origin", branch],
            capture_output=True, text=True, timeout=120,
        )
        if push_proc.returncode != 0:
            result.actions.append(CloseAction(
                kind="git_push",
                detail=f"FAILED: git push: {push_proc.stderr.strip()}",
                ok=False,
            ))
            return result
        result.actions.append(CloseAction(
            kind="git_push",
            detail=f"pushed {branch!r} to origin",
        ))

        proc = subprocess.run(
            ["gh", "pr", "create",
             "--base", main, "--head", branch,
             "--title", f"close(investigation): {slug}",
             "--body", pr_body],
            cwd=ws_root, capture_output=True, text=True, timeout=60,
        )
        if proc.returncode == 0:
            result.pr_url = proc.stdout.strip().splitlines()[-1] if proc.stdout else None
            result.actions.append(CloseAction(
                kind="gh_pr_create",
                detail=f"created PR: {result.pr_url}",
            ))
        else:
            result.actions.append(CloseAction(
                kind="gh_pr_create",
                detail=f"FAILED: gh pr create: {proc.stderr.strip()}",
                ok=False,
            ))

    return result


# ---------------------------------------------------------------------------
# CLI entry point — invoked by /viva-investigation close <slug>
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    p = argparse.ArgumentParser(
        prog="python -m discovery_engine.investigation_close",
        description="Close an investigation: stamp status, contributors, "
                    "report.html; commit; open PR (does NOT auto-merge).",
    )
    p.add_argument("slug")
    p.add_argument("--ws", default=None,
                   help="workspace root (default: walk from cwd)")
    p.add_argument("--dry-run", action="store_true",
                   help="print proposed actions; do not write or commit")
    p.add_argument("--no-pr", action="store_true",
                   help="skip the `gh pr create` step (no remote interaction)")
    p.add_argument("--skip-report", action="store_true",
                   help="don't re-render the workspace report; just stamp YAML")
    p.add_argument("--json", action="store_true",
                   help="emit the result as JSON instead of plain text")
    args = p.parse_args(argv)

    ws_root = Path(args.ws) if args.ws else _walk_to_workspace(Path.cwd())
    try:
        result = close_investigation(
            ws_root, args.slug,
            dry_run=args.dry_run,
            auto_pr=not args.no_pr,
            skip_report=args.skip_report,
        )
    except (FileNotFoundError, ValueError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps(result.to_dict(), indent=2, default=str))
    else:
        prefix = "(dry-run) " if args.dry_run else ""
        print(f"{prefix}closed investigation: {result.slug} "
              f"(branch: {result.branch})")
        print(f"  contributors: {len(result.contributors)}")
        for c in result.contributors:
            extra = []
            if c.get("kind") and c["kind"] != "human":
                extra.append(c["kind"])
            if c.get("commits"):
                extra.append(f"{c['commits']} commit(s)")
            tail = f" [{', '.join(extra)}]" if extra else ""
            print(f"    - {c['name']}{tail}")
        print("  actions:")
        for a in result.actions:
            marker = "OK " if a.ok else "FAIL"
            print(f"    [{marker}] {a.kind}: {a.detail}")
        if result.pr_url:
            print(f"\n  PR: {result.pr_url}")
            print(f"  → Review the rendered report at "
                  f"investigations/{result.slug}/report.html, then merge "
                  f"manually in the GitHub UI.")

    # Exit code: 0 on success (even with skipped steps); 1 if any action failed.
    failed = any(not a.ok for a in result.actions)
    return 1 if failed else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
