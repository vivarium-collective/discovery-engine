"""B2b: CLI — viva-compute-outcomes."""
from __future__ import annotations

from typing import Any

import click as _click

from .outcomes import compute_outcomes


def _find_workspace_root(start: Any) -> "Any | None":
    """Walk up from *start* looking for a workspace.yaml sentinel file."""
    from pathlib import Path as _Path

    cur = _Path(start).resolve()
    for _ in range(12):
        if (cur / "workspace.yaml").exists():
            return cur
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return None


@_click.command("viva-compute-outcomes")
@_click.option(
    "--workspace", "-w",
    type=_click.Path(file_okay=False),
    default=None,
    help="Workspace root (for resolving relative paths in run entries).",
)
@_click.option(
    "--study", "-s", "study_slug",
    default=None,
    help="Study name/slug to evaluate (looked up under workspace investigations/ or studies/).",
)
@_click.option(
    "--study-dir",
    "study_dir_arg",
    type=_click.Path(exists=True, file_okay=False),
    default=None,
    help="Explicit path to a study directory containing study.yaml.",
)
@_click.option(
    "--all", "all_studies",
    is_flag=True,
    default=False,
    help="Evaluate all studies in the workspace.",
)
def compute_outcomes_cli(
    workspace: "str | None",
    study_slug: "str | None",
    study_dir_arg: "str | None",
    all_studies: bool,
) -> None:
    """Evaluate behavior tests against run data and write computed_outcomes blocks."""
    import sys
    from pathlib import Path as _Path

    ws_root: "_Path | None" = (
        _Path(workspace) if workspace else _find_workspace_root(_Path.cwd())
    )

    targets: list[_Path] = []

    if study_dir_arg:
        targets.append(_Path(study_dir_arg))
    elif study_slug:
        if ws_root is None:
            _click.echo("Cannot find workspace root — pass --workspace.", err=True)
            sys.exit(1)
        for sub in ("investigations", "studies"):
            candidate = ws_root / sub / study_slug
            if (candidate / "study.yaml").exists():
                targets.append(candidate)
                break
        else:
            _click.echo(
                f"Study {study_slug!r} not found under {ws_root}", err=True
            )
            sys.exit(1)
    elif all_studies:
        if ws_root is None:
            _click.echo("Cannot find workspace root — pass --workspace.", err=True)
            sys.exit(1)
        for sub in ("investigations", "studies"):
            base = ws_root / sub
            if base.exists():
                for p in sorted(base.iterdir()):
                    if (p / "study.yaml").exists():
                        targets.append(p)
    else:
        _click.echo(
            "Specify --study-dir, --study <slug>, or --all (with --workspace).",
            err=True,
        )
        sys.exit(1)

    if not targets:
        _click.echo("No studies found.", err=True)
        sys.exit(1)

    total_runs = total_code = total_agent = 0
    for target in targets:
        try:
            summary = compute_outcomes(target, ws_root=ws_root)
            r = summary["runs_evaluated"]
            c = summary["tests_code"]
            a = summary["tests_agent"]
            _click.echo(
                f"  {target.name}: {r} runs evaluated, "
                f"{c} tests code, {a} tests agent"
            )
            total_runs += r
            total_code += c
            total_agent += a
        except Exception as exc:  # noqa: BLE001
            _click.echo(f"  {target.name}: ERROR — {exc}", err=True)

    _click.echo(
        f"\nTotal: {total_runs} runs evaluated, "
        f"{total_code} code, {total_agent} agent"
    )
