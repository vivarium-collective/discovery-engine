"""Gap-report CI integration — render the gap-check for GitHub Actions.

The report linter emits a machine-readable report
(:func:`discovery_engine.reporting.linter.build_json_report`,
``python -m discovery_engine.reporting.linter --json``). This module turns that
report into the two things a CI workflow needs, so every repo reuses the SAME
rendering instead of hand-rolling workflow YAML (viva-template just calls the
``gap-report`` composite Action that wraps this):

* **GitHub Actions annotations** — ``::error`` / ``::warning`` / ``::notice``
  workflow commands, one per finding, anchored to
  ``<studies_dir>/<study_slug>/study.yaml`` with the ``field_path`` as title, so
  the gaps show up inline on the PR's Files-changed and Checks tabs.
* **A markdown summary** — a compact table (by level, by check, internal errors,
  exit code) for the PR comment and the job summary.

Run as a module (what the composite Action calls)::

    python -m discovery_engine.integration.ci --ws <WORKSPACE> \
        [--strict] [--ruleset <name>] [--warn-only] \
        [--annotate] [--summary-file <FILE>]

Exit code: 0 clean / report-only, 1 when the gate should fail (blocking errors,
or a crashed check unless ``--allow-internal-errors``). ``--warn-only`` always
exits 0 (report-only adoption) while still emitting annotations + the summary.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

# GitHub Actions workflow-command level for each finding level. A crashed check
# (linter_internal_error) is a blind spot, surfaced as an error.
_GHA_LEVEL = {"error": "error", "warning": "warning", "info": "notice"}


def _escape_data(s: str) -> str:
    """Escape a workflow-command message body (%, CR, LF per GitHub's spec)."""
    return str(s).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _escape_prop(s: str) -> str:
    """Escape a workflow-command property value (adds , and :)."""
    return _escape_data(s).replace(":", "%3A").replace(",", "%2C")


def annotations(report: dict, *, studies_dir: str = "workspace/studies",
                warn_only: bool = False) -> list[str]:
    """GitHub Actions annotation lines for every finding in *report*.

    ``warn_only`` demotes error-level findings to ``warning`` so a repo can adopt
    the gap-report in report-only mode without the annotations reading as hard
    failures. The study file is derived from ``study_slug`` →
    ``<studies_dir>/<slug>/study.yaml`` (mosaic's convention); a finding with no
    study slug (an investigation-level or internal error) gets no ``file=``.
    """
    out: list[str] = []
    for f in report.get("findings", []):
        level = _GHA_LEVEL.get(f.get("level"), "notice")
        if warn_only and level == "error":
            level = "warning"
        props: list[str] = []
        slug = f.get("study_slug")
        if slug:
            props.append(f"file={_escape_prop(f'{studies_dir}/{slug}/study.yaml')}")
        title = f.get("check") or "gap"
        props.append(f"title={_escape_prop(f'gap: {title}')}")
        prop_str = ("," + ",".join(props)) if props else ""
        where = slug or "(workspace)"
        if f.get("field_path"):
            where = where + " [" + str(f.get("field_path")) + "]"
        body = where + ": " + str(f.get("message") or "")
        out.append(f"::{level}{prop_str}::{_escape_data(body)}")
    return out


def markdown_summary(report: dict, *, title: str = "Discovery-engine gap report") -> str:
    """A compact markdown summary of *report* for a PR comment / job summary."""
    s = report.get("summary", {})
    total = s.get("total", 0)
    by_level = s.get("by_level", {})
    by_check = s.get("by_check", {})
    internal = s.get("internal_errors", 0)
    lines = [f"## {title}", ""]
    if not total:
        lines.append("✅ **No gaps** — the gap-check is clean.")
        return "\n".join(lines) + "\n"
    e, w, i = by_level.get("error", 0), by_level.get("warning", 0), by_level.get("info", 0)
    lines.append(f"**{total} finding(s)** — "
                 f"{e} error · {w} warning · {i} info"
                 + (f"  ·  ⚠️ **{internal} crashed check(s)** (gaps UNKNOWN there)" if internal else ""))
    lines.append("")
    lines.append("| check | count |")
    lines.append("|---|---:|")
    for check, n in sorted(by_check.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| `{check}` | {n} |")
    lines.append("")
    lines.append("<details><summary>findings</summary>\n")
    for f in report.get("findings", []):
        slug = f.get("study_slug") or "(workspace)"
        lines.append(f"- **{f.get('level')}** `{f.get('check')}` — {slug}"
                     f"{' [' + f['field_path'] + ']' if f.get('field_path') else ''}: "
                     f"{(f.get('message') or '').splitlines()[0] if f.get('message') else ''}")
    lines.append("\n</details>")
    return "\n".join(lines) + "\n"


def gate_exit_code(report: dict, *, warn_only: bool = False,
                   fail_on_internal_errors: bool = True) -> int:
    """The exit code the workflow should use.

    ``warn_only`` → always 0 (report-only). Otherwise the report's own
    ``exit_code`` (1 when un-overridden blocking errors remain), escalated to 1 if
    a check crashed (``fail_on_internal_errors``) — a blind spot shouldn't pass
    silently.
    """
    if warn_only:
        return 0
    code = int(report.get("exit_code", 0) or 0)
    if fail_on_internal_errors and report.get("summary", {}).get("internal_errors", 0):
        code = 1
    return code


def build_report(ws_root, *, strict: bool = False, ruleset=None) -> dict:
    """Run the linter on *ws_root* and return the --json report dict."""
    from discovery_engine.reporting.linter import (
        lint_workspace_report, build_json_report,
        load_overrides, apply_overrides, has_blocking_errors,
    )
    findings = lint_workspace_report(Path(ws_root), strict=strict, rulesets=ruleset)
    overrides = load_overrides(Path(ws_root))
    visible = apply_overrides(findings, overrides)
    exit_code = 1 if has_blocking_errors(findings, overrides) else 0
    return build_json_report(visible, exit_code=exit_code)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="discovery_engine.integration.ci",
        description="Run the discovery-engine gap-check and render it for CI "
                    "(GitHub annotations + a markdown summary).",
    )
    p.add_argument("--ws", default=".", help="Workspace root (default: cwd).")
    p.add_argument("--strict", action="store_true",
                   help="Promote opt-in warnings to blocking errors.")
    p.add_argument("--ruleset", action="append", default=None,
                   help="Enable an opt-in ruleset (repeatable), e.g. campaign.")
    p.add_argument("--warn-only", action="store_true",
                   help="Report-only: emit annotations + summary but always exit 0.")
    p.add_argument("--allow-internal-errors", action="store_true",
                   help="Do not fail the gate when a check crashed (default: fail).")
    p.add_argument("--studies-dir", default="workspace/studies",
                   help="Path prefix for study files in annotations "
                        "(default: workspace/studies).")
    p.add_argument("--annotate", action="store_true",
                   help="Emit GitHub Actions annotations to stdout.")
    p.add_argument("--summary-file", default=None,
                   help="Write the markdown summary to this file "
                        "(e.g. $GITHUB_STEP_SUMMARY). Also printed to stdout.")
    p.add_argument("--json", action="store_true",
                   help="Also print the raw --json report to stderr (debugging).")
    args = p.parse_args(argv)

    ws_root = Path(args.ws).resolve()
    if not (ws_root / "workspace.yaml").is_file():
        print(f"ERROR: no workspace.yaml under {ws_root}", file=sys.stderr)
        return 2

    report = build_report(ws_root, strict=args.strict, ruleset=args.ruleset)

    if args.annotate:
        for line in annotations(report, studies_dir=args.studies_dir, warn_only=args.warn_only):
            print(line)
    summary = markdown_summary(report)
    print(summary)
    if args.summary_file:
        try:
            with open(args.summary_file, "a", encoding="utf-8") as fh:
                fh.write(summary)
        except OSError as exc:
            print(f"WARNING: could not write summary to {args.summary_file}: {exc}", file=sys.stderr)
    if args.json:
        import json
        print(json.dumps(report), file=sys.stderr)

    n_internal = report.get("summary", {}).get("internal_errors", 0)
    if n_internal:
        print(f"WARNING: {n_internal} gap-check(s) crashed — those gaps were NOT "
              "evaluated (blind spot).", file=sys.stderr)

    return gate_exit_code(
        report, warn_only=args.warn_only,
        fail_on_internal_errors=not args.allow_internal_errors,
    )


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
