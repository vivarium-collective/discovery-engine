"""Check registry, opt-in rulesets, and the ``lint_workspace_report``
orchestrator + CLI.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

import yaml

from discovery_engine.study.band_provenance import has_numeric_band as _is_numeric_band
from discovery_engine.bibtex import bib_keys
from discovery_engine.reporting.rigor import run_is_emitter_backed
from discovery_engine.workspace_paths import WorkspacePaths
from discovery_engine.study.observable_lint import reconcile_study_observables
from discovery_engine.reporting.linter.context import *  # noqa: F401,F403
from discovery_engine.reporting.linter.investigation_checks import *  # noqa: F401,F403
from discovery_engine.reporting.linter.checks_report import *  # noqa: F401,F403
from discovery_engine.reporting.linter.checks_findings import *  # noqa: F401,F403
from discovery_engine.reporting.linter.checks_structure import *  # noqa: F401,F403
from discovery_engine.reporting.linter.checks_completeness import *  # noqa: F401,F403
from discovery_engine.reporting.linter.checks_render import *  # noqa: F401,F403
from discovery_engine.reporting.linter.checks_rigor import *  # noqa: F401,F403




_INVESTIGATION_CHECK_FUNCTIONS = (
    _check_investigation_narrative_spine,
)


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


CHECKS = (
    # Render-guarantee: the spec loads through the dashboard's own load_spec(),
    # so "passes lint" implies "renders" (#96). Error: fails to render.
    "render_blocked",
    "incomplete_summaries",
    "status_contradictions",
    "missing_provenance",
    # Computed verdict contradicts the authored one (H4): blocks publication,
    # overridable by an author who intends the divergence.
    "verdict_divergence",
    "unresolved_placeholders",
    "duplicate_modal_phrases",
    "truncated_takeaways",
    # Pass 10A findings linter additions
    "decide_phase_missing_findings",
    "finding_without_evidence",
    # Empty finding statement renders as "(no statement)" + an empty Key-takeaways
    # bullet (dnaa-replication 2026-06-07). Error: every finding must be filled in.
    "finding_without_statement",
    "finding_cites_unknown_bib_key",
    "finding_references_unknown_expert_doc",
    # Band provenance (stage 3a): expert-sourced bands should carry structured cites
    "band_test_missing_cites",
    "band_cites_unknown_bib_key",
    # Derive-on-read status drift (status-drift lesson)
    "status_out_of_date_vs_runs",
    # Graded-test ontology Phase 2: a persisted computed grade whose _grader stamp is behind the
    # current grader — the stored verdict is stale and should be re-derived (warning, non-blocking).
    "stale_computed_grades",
    # Forward-drift: declares done/passed but records no runs (dnaa-replication
    # 2026-05-31 — reviewer couldn't tell if studies had actually run)
    "status_claims_done_no_runs_recorded",
    # Future-proofing: study has runs but none persisted via an emitter
    "runs_without_emitter",
    # A passed study that hedges its conclusion ("should"/"likely"/"seems") —
    # the wording symptom of a claim resting on something other than evidence.
    "hedged_verdict_when_passed",
    # Reviewer-facing clarity strip ambiguities (single-sourced from
    # study_status.study_clarity_summary): ran-but-tests-pending, gate↔test drift
    "reviewer_clarity_ambiguity",
    # Chart's stamped source run != the study's latest run (or unrendered /
    # untracked-on-disk). Supersedes the mtime-based figure_stale_vs_run.
    # warning by default; error under --strict.
    "viz_stale_vs_latest_run",
    # S3: v4 narrative-spine nudge — info-level reminder of missing dnaa-style sections
    "narrative_spine_completeness",
    # Investigation-level narrative sections REQUIRED (skippable via
    # narrative_spine_skip) — warning-level (publication-gating)
    "investigation_narrative_spine_required",
    # Expert-handoff readiness — every study card in a generated report
    # should show baseline composites, variants planned, simulation
    # runs planned, readouts, runs, tests, and visualizations. When a
    # study YAML has any of those blocks empty/absent, the report ends
    # up with empty sections that the expert can't critique.  These
    # warnings flag the gap pre-publication.  All warning-level (non-
    # blocking) — a study CAN publish without them, but the expert will
    # see "TBD" rather than concrete content.
    "missing_baseline",
    "missing_variants",
    "missing_planned_runs",
    "missing_readouts",
    # HARD GATE (error, blocking, override-able): EVERY study must DECLARE ≥1
    # visualization (a visualizations[] / embed_visualizations[] entry) — a loose
    # on-disk figure does NOT count, since the dashboard renders declared viz.
    # Keeps every study's Visualizations surface non-empty. Opt out for a genuinely
    # early study via .pbg/report-lint-overrides.json (or /viva-report --force).
    "missing_visualizations",
    # Build / Simulations tab readiness — the dashboard's study-detail
    # template renders the Build tab from `conditions.{baseline,variants,
    # model_settings}` (or legacy model_change / implementation_requirements)
    # and the Simulations tab from `simulation_set:`. A study without
    # those v4 fields renders those tabs BLANK even when v3 fields
    # `baseline:` + `variants:` + `planned_runs:` are populated.
    "missing_conditions_block",
    "missing_simulation_set",
    # SP2b-ii: readout migration status — migratable (info) + needs_human (warning)
    "readout_migration_status",
    # Wave 3a: workflow-typing enums (next_action_type / study_type) — soft
    "next_action_type_missing",
    "next_action_type_unknown",
    "study_type_unknown",
    # Wave 3b: claim_scope / generality / lifecycle_state enums + floor — soft
    "claim_scope_unknown",
    "generality_axis_unknown",
    "generality_level_unknown",
    "lifecycle_state_unknown",
    "lifecycle_state_below_floor",
    # Peer-review rigor (framework-rigor design G1/G4/G5):
    # G1-lint — every behavior_tests[] entry declares its gate_class
    # (regression_pin | acceptance_criterion); never inferred by the linter.
    "gate_class_missing",
    "gate_class_unknown",
    # G4a — structural config-consumption audit: supplied composite config
    # keys must be accepted by the process's declared config_schema (unknown
    # keys are silently dropped by bigraph-schema fill — the mut_sigma: 0.0
    # disease). Static ast analysis only; no instantiation.
    "config_consumption",
    # G4b — seed-all-stochastic: a stochastic composite process (declared
    # flag / known-stochastic name / RNG source signal) must pin a seed.
    "stochastic_unseeded",
    # G5 — physical unit labels in claim/finding text require a
    # units_and_time declaration; otherwise they are decorative (WARN).
    "unearned_unit_labels",
    # Reproducibility — the effective baseline run-config (composite defaults
    # ⊕ config_file ⊕ inline params, plus the separate runtime: surface) must
    # add SOMETHING beyond composite defaults / a bare {seed, cache_dir}.
    # Otherwise the workbench viewer shows composite defaults, not a
    # reproducible spec, and a Run won't reproduce the study.
    "baseline_config_carry",
    # Campaign quality bar + enrichment-plan design principles (campaign lessons):
    # provenance on every quantitative claim; phase-maps-not-single-runs for a
    # Decision view; authored vs derived confidence reconciliation.
    "finding_missing_cites",
    "decision_viz_single_run",
    "authored_confidence_diverges",
    # Investigation-level: at least one adversarial / ablation (negative-control)
    # member study — a system that should fail the same gates.
    "investigation_missing_adversarial",
)


_CHECK_FUNCTIONS = (
    _check_contract_observables,
    _check_renders_via_dashboard,
    # Static twin of the above: catches an empty/malformed v3/v4 baseline that
    # the workbench study-detail loader rejects, WITHOUT needing a running server.
    _check_render_guarantee_baseline,
    _check_incomplete_summaries,
    _check_status_contradictions,
    _check_missing_provenance,
    _check_verdict_divergence,
    _check_unresolved_placeholders,
    _check_duplicate_modal_phrases,
    _check_truncated_takeaways,
    _check_decide_phase_missing_findings,
    _check_finding_without_evidence,
    _check_finding_without_statement,
    _check_finding_cites_unknown_bib_key,
    _check_finding_references_unknown_expert_doc,
    # Band provenance (stage 3a)
    _check_band_test_missing_cites,
    _check_band_cites_unknown_bib_key,
    _check_visualization_addresses,
    _check_visualization_files,
    _check_dag_edges_legacy_and_canonical_both_set,
    _check_status_legacy_only,
    _check_runs_yaml_vs_db_drift,
    _check_status_out_of_date_vs_runs,
    _check_stale_computed_grades,
    _check_status_claims_done_but_no_runs_recorded,
    _check_runs_without_emitter,
    _check_reviewer_clarity_ambiguities,
    _check_viz_stale_vs_latest_run,
    _check_narrative_spine_completeness,
    # Expert-handoff readiness (warning-level — see CHECKS comment block)
    _check_missing_baseline,
    _check_missing_variants,
    _check_missing_planned_runs,
    _check_missing_readouts,
    # HARD GATE (error, blocking, override-able): every study must DECLARE a viz
    _check_missing_visualizations,
    _check_missing_conditions_block,
    _check_missing_simulation_set,
    # Anti-slop & honesty checks (added 2026-05-25 after pdmp-* feedback)
    _check_machine_projected_tests,
    _check_speculative_readout_paths,
    # SP2b-ii: readout migration status (migratable + needs_human)
    _check_readout_migration_status,
    _check_readout_contract,
    # Wave 3a: workflow-typing enums (next_action_type / study_type)
    _check_workflow_typing,
    # Wave 3b: claim_scope / generality / lifecycle_state enums + floor
    _check_finding_scope_generality_lifecycle,
    # Hedged verdict: a passed study whose conclusion hedges ("should"/"likely")
    _check_hedged_verdict_when_passed,
    # Peer-review rigor (framework-rigor): G1 gate_class declared, G4a config
    # consumption (structural), G4b seed-all-stochastic, G5 units/time declared
    _check_gate_class_declared,
    _check_config_consumption,
    _check_stochastic_unseeded,
    _check_unearned_unit_labels,
    # Reproducibility — baseline must resolve to a real run-config (not bare
    # composite defaults / {seed, cache_dir}); config_file ⊕ params ⊕ runtime.
    _check_baseline_config_carry,
)


# ---------------------------------------------------------------------------
# Opt-in rulesets
#
# ``_CHECK_FUNCTIONS`` / ``_INVESTIGATION_CHECK_FUNCTIONS`` are the DEFAULT,
# domain-neutral structural checks. Policy-level expectations (a "quality bar"
# a given research programme chooses to hold itself to) live in NAMED rulesets
# that a consumer enables explicitly:
#
#   * ``lint_workspace_report(ws, rulesets=["campaign"])``
#   * ``set_default_rulesets(["campaign"])``   (process-wide)
#   * ``lint: {rulesets: [campaign]}`` in ``workspace.yaml``
#   * ``python -m discovery_engine.reporting.linter --ruleset campaign``
#
# Each rule keeps its own logic; a ruleset only decides whether it runs.
# ---------------------------------------------------------------------------

_RULESETS: dict[str, tuple[tuple, tuple]] = {}
_DEFAULT_RULESETS: list[str] = []


def register_ruleset(name: str, study_checks=(), investigation_checks=()) -> None:
    """Register (or replace) a named opt-in ruleset.

    ``study_checks`` / ``investigation_checks`` are callables taking a
    ``_LintContext``. Registration does NOT enable the ruleset.
    """
    _RULESETS[name] = (tuple(study_checks), tuple(investigation_checks))


def available_rulesets() -> list[str]:
    return sorted(_RULESETS)


def set_default_rulesets(names) -> None:
    """Process-wide default for ``rulesets=None`` (replaces; ``[]`` clears)."""
    names = list(names or [])
    unknown = [n for n in names if n not in _RULESETS]
    if unknown:
        raise KeyError(f"unknown lint ruleset(s) {unknown}; available: {available_rulesets()}")
    _DEFAULT_RULESETS[:] = names


def _workspace_rulesets(ws_root: Path) -> list[str]:
    """Rulesets a workspace opts into via ``lint.rulesets`` in workspace.yaml."""
    try:
        import yaml as _yaml
        data = _yaml.safe_load((ws_root / "workspace.yaml").read_text(encoding="utf-8")) or {}
        names = (data.get("lint") or {}).get("rulesets") or []
        return [str(n) for n in names] if isinstance(names, list) else []
    except Exception:  # noqa: BLE001 - absent/invalid workspace.yaml -> no opt-ins
        return []


# Campaign quality bar + enrichment-plan design principles (campaign lessons):
# (b) a quantitative finding with no provenance;
# (a) a Decision view that is a single run, not a phase map;
# (d) authored confidence that diverges from derive_confidence;
# plus an investigation must include an adversarial / ablation member study.
register_ruleset(
    "campaign",
    study_checks=(
        _check_finding_missing_cites,
        _check_decision_viz_single_run,
        _check_authored_confidence_diverges,
    ),
    investigation_checks=(_check_investigation_missing_adversarial,),
)


def lint_workspace_report(
    ws_root: Path, *, strict: bool = False, rulesets=None
) -> list[LintFinding]:
    """Run every Pass B check against every study in the workspace.

    Returns a flat list of findings. Sort: error before warning before
    info; within each level, sorted by study_slug then field_path so the
    output is stable across runs.

    ``rulesets`` names extra opt-in rulesets (see ``register_ruleset``) run on
    top of the default structural checks. ``None`` means "the workspace's
    ``lint.rulesets`` plus ``set_default_rulesets``"; ``[]`` means defaults only.

    ``strict`` promotes opt-in warning-level checks (e.g.
    ``viz_stale_vs_latest_run``) to error level.

    Workspace-global scans (bib keys, expert-doc names, viz classes) depend
    only on ``ws_root`` — not on the individual study — so a single
    ``ws_cache`` dict is minted here and shared by every ``_LintContext``
    built in this call (studies and investigations alike). Each scan then
    runs at most once per call to this function, instead of once per
    study. See ``_cached()``. The dict is scoped to this one call — a
    fresh one every time ``lint_workspace_report()`` runs — so it can
    never serve stale data across two separate lint runs.
    """
    out: list[LintFinding] = []
    ws_cache: dict = {}
    if rulesets is None:
        rulesets = list(dict.fromkeys([*_DEFAULT_RULESETS, *_workspace_rulesets(ws_root)]))
    study_checks = list(_CHECK_FUNCTIONS)
    inv_checks = list(_INVESTIGATION_CHECK_FUNCTIONS)
    for name in rulesets:
        if name not in _RULESETS:
            raise KeyError(f"unknown lint ruleset {name!r}; available: {available_rulesets()}")
        study_checks += _RULESETS[name][0]
        inv_checks += _RULESETS[name][1]
    for slug, spec in _iter_study_specs(ws_root):
        ctx = _LintContext(ws_root=ws_root, slug=slug, spec=spec, strict=strict, ws_cache=ws_cache)
        for fn in study_checks:
            try:
                fn(ctx)
            except Exception as e:  # noqa: BLE001
                # A buggy check shouldn't crash the whole lint run; surface it
                # as an info finding so reviewers can still see something.
                ctx.add(
                    level="info",
                    field_path="<linter>",
                    message=f"Linter check {fn.__name__} raised {e!r} on study {slug!r}.",
                    check="linter_internal_error",
                )
        out.extend(ctx.findings)

    # Investigation-level checks (the modern v2 investigation.yaml narrative
    # spine) — run on investigation specs, not studies.
    for slug, spec in _iter_investigation_specs(ws_root):
        ctx = _LintContext(ws_root=ws_root, slug=slug, spec=spec, strict=strict, ws_cache=ws_cache)
        for fn in inv_checks:
            try:
                fn(ctx)
            except Exception as e:  # noqa: BLE001
                ctx.add(
                    level="info",
                    field_path="<linter>",
                    message=f"Linter check {fn.__name__} raised {e!r} on investigation {slug!r}.",
                    check="linter_internal_error",
                )
        out.extend(ctx.findings)

    level_order = {"error": 0, "warning": 1, "info": 2}
    out.sort(key=lambda f: (level_order.get(f.level, 99), f.study_slug, f.field_path))
    return out


def format_findings(findings: Iterable[LintFinding]) -> str:
    """Render findings as a human-readable plain-text report."""
    lines: list[str] = []
    by_level: dict[str, list[LintFinding]] = {}
    for f in findings:
        by_level.setdefault(f.level, []).append(f)
    for lvl in ("error", "warning", "info"):
        entries = by_level.get(lvl, [])
        if not entries:
            continue
        lines.append(f"[{lvl.upper()}] ({len(entries)})")
        for f in entries:
            lines.append(f"  {f.study_slug}: {f.field_path} — {f.message}")
            lines.append(f"    override_key: {f.override_key}")
    n_blocking = len(by_level.get("error", [])) + len(by_level.get("warning", []))
    n_info = len(by_level.get("info", []))
    if not lines:
        lines.append("OK — no lint findings.")
    elif n_blocking == 0:
        # Only info-level nudges (e.g. v4 narrative-spine completeness):
        # explicitly state publication isn't blocked so the user can tell
        # at a glance.
        lines.append(f"OK — no blocking findings ({n_info} info-level nudges shown above).")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# CLI: python -m discovery_engine.reporting.linter
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    """CLI entry: lint a workspace and report findings.

    Exit code:
      0 — clean (no blocking errors after overrides applied), or --force given.
      1 — blocking errors remain.
      2 — usage / IO error.
    """
    import argparse
    import sys

    p = argparse.ArgumentParser(
        prog="python -m discovery_engine.reporting.linter",
        description="Pre-publication report linter for workspace studies.",
    )
    p.add_argument(
        "--ws", "--workspace",
        dest="ws",
        default=".",
        help="Path to the workspace root (default: current directory).",
    )
    p.add_argument(
        "--force",
        action="store_true",
        help="Log blocking errors to .pbg/report-lint-overrides.json and exit 0.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit findings as JSON instead of plain text.",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Promote opt-in warning checks (e.g. viz_stale_vs_latest_run) to errors.",
    )
    p.add_argument(
        "--ruleset",
        action="append",
        default=None,
        help="Enable an opt-in ruleset (repeatable), e.g. --ruleset campaign.",
    )
    args = p.parse_args(argv)

    ws_root = Path(args.ws).resolve()
    if not (ws_root / "workspace.yaml").is_file():
        print(f"ERROR: no workspace.yaml under {ws_root}", file=sys.stderr)
        return 2

    findings = lint_workspace_report(ws_root, strict=args.strict, rulesets=args.ruleset)
    overrides = load_overrides(ws_root)
    visible = apply_overrides(findings, overrides)

    if args.json:
        print(json.dumps([f.to_dict() for f in visible], indent=2))
    else:
        print(format_findings(visible))

    blocking = [f for f in findings if f.level == "error" and f.override_key not in overrides]
    if not blocking:
        return 0
    if args.force:
        for f in blocking:
            write_override(ws_root, f)
        print(
            f"--force: logged {len(blocking)} blocking finding(s) to "
            f"{override_path(ws_root).relative_to(ws_root)}",
            file=sys.stderr,
        )
        return 0
    print(
        f"BLOCKING: {len(blocking)} error-level finding(s) — refusing publication. "
        "Re-run with --force to log overrides and proceed.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))


__all__ = [
    '_INVESTIGATION_CHECK_FUNCTIONS',
    'CHECKS',
    '_CHECK_FUNCTIONS',
    '_RULESETS',
    '_DEFAULT_RULESETS',
    'register_ruleset',
    'available_rulesets',
    'set_default_rulesets',
    '_workspace_rulesets',
    'lint_workspace_report',
    'format_findings',
    'main',
]
