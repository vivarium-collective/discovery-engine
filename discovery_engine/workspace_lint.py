"""Single workspace-lint entrypoint — shared engine checks + workspace-local checks.

This is the consolidation seam for workspace linting. Historically every workspace
shipped its own ``scripts/lint-workspace.py`` that re-implemented (and drifted from)
the shared checks, and in practice often *failed to call* the shared ones at all —
e.g. a workspace lint never ran the report linter's ``missing_visualizations`` gate,
so an agentic flow could publish studies with no figures and still pass clean.

``workspace_lint`` composes, into one ``list[LintFinding]`` + one exit code:

1. the **shared** report-linter checks (:func:`report_linter.lint_workspace_report`)
   — so a check lands **once** here and every workspace picks it up on the next
   version bump instead of each repo re-implementing it; and
2. a workspace's **own** checks, discovered from the workspace package's
   ``<pkg>.lint_checks`` module (same package-discovery convention as
   ``evaluators`` / ``build_hook`` — see
   :func:`study_evaluator._workspace_evaluator_packages`), so truly
   workspace-specific rules stay local without forking the whole lint.

The report linter's **override system** is honored: a finding whose ``override_key``
is logged in the workspace's override file is downgraded for display and does not
block (same mechanism ``/viva-report`` uses).

A workspace's ``scripts/lint-workspace.py`` then collapses to a thin shim::

    #!/usr/bin/env python
    import sys
    from discovery_engine.workspace_lint import main  # or viva_superpowers.workspace_lint
    sys.exit(main())

Workspace-local check convention — ``<pkg>/lint_checks.py`` in the workspace's own
Python package::

    # Each callable takes the workspace root (pathlib.Path) and returns an iterable
    # of LintFinding OR plain strings (coerced to workspace-level warnings) — the
    # ``-> list[str]`` shape existing workspace audit_checks already use.
    def check_my_workspace_rule(ws_root):
        return ["study foo: something workspace-specific is off"]

    CHECKS = [check_my_workspace_rule]

Note: ``study_audit`` and ``study_observable_lint`` are composed in a follow-up;
this first increment composes the report linter + the workspace ``CHECKS`` hook,
which is what closes the "shared gate never ran in the workspace lint" blind spot.
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path
from typing import Callable, Iterable, Sequence

from .report_linter import (
    LintFinding,
    apply_overrides,
    format_findings,
    has_blocking_errors,
    lint_workspace_report,
    load_overrides,
)

#: A workspace-local check: ``fn(ws_root: Path) -> Iterable[LintFinding | str]``.
LocalCheck = Callable[[Path], Iterable]

#: Module name (within the workspace package) that declares ``CHECKS``.
LINT_CHECKS_MODULE = "lint_checks"


def load_workspace_lint_checks(ws_root: Path | str | None) -> list[LocalCheck]:
    """Import ``<pkg>.lint_checks`` and return its ``CHECKS`` callables.

    Uses the SAME package discovery as ``evaluators`` / ``build_hook``
    (name-derived slug then ``workspace.yaml:package_path``). Missing module or a
    broken plugin is skipped — a workspace hook must never crash the lint.
    """
    if ws_root is None:
        return []
    from .study_evaluator import _workspace_evaluator_packages

    key = str(Path(ws_root).resolve())
    if key not in sys.path:
        sys.path.insert(0, key)

    checks: list[LocalCheck] = []
    for pkg in _workspace_evaluator_packages(ws_root):
        try:
            mod = __import__(f"{pkg}.{LINT_CHECKS_MODULE}", fromlist=["CHECKS"])
        except Exception:  # noqa: BLE001 -- a broken/absent workspace hook is not fatal
            continue
        for chk in getattr(mod, "CHECKS", None) or []:
            if callable(chk):
                checks.append(chk)
    return checks


def _coerce(item: object, *, check: str = "workspace_local") -> LintFinding:
    """A LintFinding passes through; a plain string becomes a workspace-level
    warning with a stable ``override_key`` (stable so it can be overridden)."""
    if isinstance(item, LintFinding):
        return item
    msg = str(item)
    digest = hashlib.blake2b(msg.encode("utf-8"), digest_size=6).hexdigest()
    return LintFinding(
        level="warning",
        study_slug="<workspace>",
        field_path="",
        message=msg,
        override_key=f"{check}:<workspace>:{digest}",
        check=check,
    )


def lint_workspace(
    ws_root: Path | str,
    *,
    strict: bool = False,
    local_checks: Sequence[LocalCheck] | None = None,
) -> list[LintFinding]:
    """Shared report-linter findings plus the workspace's local-check findings.

    ``local_checks`` overrides package discovery when given (used by tests). A
    local check that raises is reported as a warning rather than aborting the run.
    """
    ws_root = Path(ws_root)
    findings: list[LintFinding] = list(lint_workspace_report(ws_root, strict=strict))

    checks = list(local_checks) if local_checks is not None else load_workspace_lint_checks(ws_root)
    for chk in checks:
        name = getattr(chk, "__name__", repr(chk))
        try:
            for item in chk(ws_root) or []:
                findings.append(_coerce(item))
        except Exception as exc:  # noqa: BLE001 -- one bad check can't fail the lint
            findings.append(_coerce(f"local check {name!r} errored: {exc}"))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m discovery_engine.workspace_lint",
        description="Run the shared workspace lint (report linter) plus any "
                    "workspace-local checks from the workspace package's "
                    "lint_checks.CHECKS. Exits non-zero iff a non-overridden "
                    "error-level finding remains.",
    )
    parser.add_argument("ws_root", nargs="?", default=".", type=Path,
                        help="workspace root (default: current directory)")
    parser.add_argument("--strict", action="store_true",
                        help="promote severity where the report linter supports it")
    args = parser.parse_args(argv)

    findings = lint_workspace(args.ws_root, strict=args.strict)
    overrides = load_overrides(args.ws_root)
    # Downgrade overridden errors to warnings for display; block on the raw set.
    shown = apply_overrides(findings, overrides)
    text = format_findings(shown)
    if text:
        print(text)

    blocking = has_blocking_errors(findings, overrides)
    n_err = sum(1 for f in shown if f.level == "error")
    n_warn = sum(1 for f in shown if f.level == "warning")
    print(f"workspace lint: {'FAIL' if blocking else 'OK'} "
          f"({n_err} error(s), {n_warn} warning(s))")
    return 1 if blocking else 0


if __name__ == "__main__":
    sys.exit(main())
