"""Tests for the Pass B report linter (discovery_engine.report_linter).

One test per check, plus override-file roundtrip + render-blocking
integration tests.

Fixtures live under tests/fixtures/lint-cases/ — one workspace per case,
each with a minimal workspace.yaml and a studies/<slug>/study.yaml that
triggers exactly the violations under test (plus a clean baseline).
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from discovery_engine.report_linter import (
    LintFinding,
    apply_overrides,
    format_findings,
    has_blocking_errors,
    lint_workspace_report,
    load_overrides,
    main,
    override_path,
    unresolved_composite_refs,
    write_override,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "lint-cases"


def _copy_fixture(name: str, dest: Path) -> Path:
    src = FIXTURES / name
    if not src.is_dir():
        raise FileNotFoundError(src)
    shutil.copytree(src, dest)
    return dest


def _findings_by_check(findings: list[LintFinding]) -> dict[str, list[LintFinding]]:
    out: dict[str, list[LintFinding]] = {}
    for f in findings:
        out.setdefault(f.check, []).append(f)
    return out


# ---------------------------------------------------------------------------
# Clean baseline — no findings at all
# ---------------------------------------------------------------------------


def test_clean_baseline_produces_no_findings(tmp_path):
    ws = _copy_fixture("clean-baseline", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    # S3: the narrative_spine_completeness check is info-level and fires on
    # any spec missing the v4 narrative-spine fields, which the clean
    # baseline (a minimal v3 spec) intentionally lacks. Filter it out — the
    # contract this test guards is "no blocking errors/warnings"; the info
    # nudge is expected for v3 specs.
    blocking = [f for f in findings if f.level != "info"]
    assert blocking == [], f"expected no error/warning findings, got {blocking}"
    assert not has_blocking_errors(findings)


# ---------------------------------------------------------------------------
# 1. incomplete_summaries
# ---------------------------------------------------------------------------


def test_incomplete_summaries_fires_on_evaluated_without_conclusion_logic(tmp_path):
    ws = _copy_fixture("incomplete-summary", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    incs = by_check.get("incomplete_summaries", [])
    assert len(incs) == 1
    f = incs[0]
    assert f.level == "error"
    assert f.study_slug == "study-incomplete"
    assert "conclusion_logic" in f.field_path
    assert "evaluated" in f.message.lower()


# ---------------------------------------------------------------------------
# 2. status_contradictions
# ---------------------------------------------------------------------------


def test_status_contradictions_fires_for_each_combo(tmp_path):
    ws = _copy_fixture("status-contradictions", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    contradictions = by_check.get("status_contradictions", [])
    # 3 distinct studies, each triggering 1 distinct contradiction.
    slugs = sorted({f.study_slug for f in contradictions})
    assert slugs == ["study-contradict", "study-impl-running", "study-review-blocked"]
    assert all(f.level == "error" for f in contradictions)


# ---------------------------------------------------------------------------
# 3. missing_provenance
# ---------------------------------------------------------------------------


def test_missing_provenance_fires_for_each_finding_without_run_ids(tmp_path):
    ws = _copy_fixture("missing-provenance", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    prov = by_check.get("missing_provenance", [])
    # 2 findings in the fixture both lack run_ids.
    assert len(prov) == 2
    paths = sorted(f.field_path for f in prov)
    assert paths == [
        "findings[0].provenance.run_ids",
        "findings[1].provenance.run_ids",
    ]
    assert all(f.level == "error" for f in prov)


# ---------------------------------------------------------------------------
# 4. unresolved_placeholders
# ---------------------------------------------------------------------------


def test_unresolved_placeholders_fires_for_TBD_TODO_insert_fillin(tmp_path):
    ws = _copy_fixture("unresolved-placeholders", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    placeholders = by_check.get("unresolved_placeholders", [])
    # 4 strings hit a placeholder: objective(TBD), description(TODO),
    # purpose.mechanism(<insert>), purpose.expected_outcome([fill in]).
    assert len(placeholders) == 4
    fields = sorted(f.field_path for f in placeholders)
    assert "description" in fields
    assert "objective" in fields
    assert "purpose.mechanism" in fields
    assert "purpose.expected_outcome" in fields
    assert all(f.level == "error" for f in placeholders)


# ---------------------------------------------------------------------------
# 5. duplicate_modal_phrases
# ---------------------------------------------------------------------------


def test_duplicate_modal_phrases_fires_on_near_identical_test_descriptions(tmp_path):
    ws = _copy_fixture("duplicate-modal", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    dupes = by_check.get("duplicate_modal_phrases", [])
    # test-one and test-two have identical descriptions -> 1 finding flagged
    # on the second (b) item. test-three is distinct.
    assert len(dupes) == 1
    assert dupes[0].level == "warning"
    assert "test-two" in dupes[0].message or "test-one" in dupes[0].message


# ---------------------------------------------------------------------------
# 6. truncated_takeaways
# ---------------------------------------------------------------------------


def test_truncated_takeaways_fires_on_short_or_unterminated_text(tmp_path):
    ws = _copy_fixture("truncated-takeaway", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    trunc = by_check.get("truncated_takeaways", [])
    # Both if_pass ("Confirm reproduces and") and if_fail ("Halt") trigger
    # — if_fail trips the <20 char rule; if_pass trips the missing-terminator
    # rule.
    paths = sorted(f.field_path for f in trunc)
    assert paths == ["conclusion_logic.if_fail", "conclusion_logic.if_pass"]
    assert all(f.level == "error" for f in trunc)


# ---------------------------------------------------------------------------
# Pass 10A — findings-protocol checks
# ---------------------------------------------------------------------------


def test_decide_phase_missing_findings_fires_on_decide_with_no_findings(tmp_path):
    ws = _copy_fixture("decide-missing-findings", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    decide = by_check.get("decide_phase_missing_findings", [])
    assert len(decide) == 1
    f = decide[0]
    assert f.level == "error"
    assert f.study_slug == "study-decide"
    assert "/viva-study findings" in f.message
    assert "study-decide" in f.message


def test_finding_without_evidence_fires_for_biological_with_no_link(tmp_path):
    ws = _copy_fixture("finding-no-evidence", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    no_ev = by_check.get("finding_without_evidence", [])
    # Only F-01 (biological, no evidence link) should fire.
    # F-02 has evidence.from_run.
    # F-03 is methodological — kind not in the warned set.
    # F-04 has evidence.from_test.
    assert len(no_ev) == 1
    f = no_ev[0]
    assert f.level == "warning"
    assert "F-01" in f.message
    assert "biological" in f.message


def test_finding_cites_unknown_bib_key_fires_per_unknown_key(tmp_path):
    ws = _copy_fixture("finding-unknown-bib", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    unknown = by_check.get("finding_cites_unknown_bib_key", [])
    # F-01 cites 2 unknown keys (MadeUpKey2099, AnotherFakeRef); F-02 is clean.
    assert len(unknown) == 2
    assert all(f.level == "error" for f in unknown)
    keys_called_out = sorted(
        msg
        for f in unknown
        for msg in [f.message]
    )
    assert any("MadeUpKey2099" in m for m in keys_called_out)
    assert any("AnotherFakeRef" in m for m in keys_called_out)


def test_finding_references_unknown_expert_doc_fires(tmp_path):
    ws = _copy_fixture("finding-unknown-expert", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    unk = by_check.get("finding_references_unknown_expert_doc", [])
    # F-01 references known_expert_doc -> ok.
    # F-02 references mystery_doc_not_in_workspace -> fires.
    assert len(unk) == 1
    f = unk[0]
    assert f.level == "error"
    assert "F-02" in f.message
    assert "mystery_doc_not_in_workspace" in f.message


# ---------------------------------------------------------------------------
# 11. visualization_address_unresolved
# ---------------------------------------------------------------------------


def test_visualization_address_unresolved_fires_on_missing_local_class(tmp_path):
    """Both `local:DnaAStateVisualization` and `local:DnaABoxOccupancyVisualization`
    point at classes that don't exist anywhere under pkg/visualizations/."""
    ws = _copy_fixture("viz-address-unresolved", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    unresolved = by_check.get("visualization_address_unresolved", [])
    assert len(unresolved) == 2
    assert all(f.level == "error" for f in unresolved)
    classes_called_out = sorted(f.message for f in unresolved)
    assert any("DnaAStateVisualization" in m for m in classes_called_out)
    assert any("DnaABoxOccupancyVisualization" in m for m in classes_called_out)
    # Field path points at the offending visualizations[] entry, not the study root.
    assert all(f.field_path.startswith("visualizations[") for f in unresolved)


def test_visualization_address_unresolved_skips_dotted_and_empty(tmp_path):
    """The fixture also declares a dotted address, an empty address, and a
    bare class name without the local: prefix. None of those should fire."""
    ws = _copy_fixture("viz-address-unresolved", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    unresolved = by_check.get("visualization_address_unresolved", [])
    # Exactly the two local: entries with missing classes fired — no more.
    assert len(unresolved) == 2
    # The viz names of the skipped entries must not appear in any finding.
    flagged_viz_names = [
        f.message.split("'")[1] for f in unresolved  # `Visualization 'NAME' …`
    ]
    assert "ts-from-obs" not in flagged_viz_names  # dotted path skipped
    assert "empty-addr" not in flagged_viz_names   # empty address skipped
    assert "bare-name" not in flagged_viz_names    # no local: prefix skipped


def test_visualization_class_misplaced_fires_when_class_exists_outside_visualizations(tmp_path):
    """A Visualization subclass defined at the package ROOT (pkg/viz_misplaced.py)
    rather than under pkg/visualizations/ is the common authoring mistake. The
    linter distinguishes it from 'no such class': it fires
    `visualization_class_misplaced` (not the generic unresolved) and names both
    the offending file and the fix."""
    ws = _copy_fixture("viz-class-misplaced", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    misplaced = by_check.get("visualization_class_misplaced", [])
    assert len(misplaced) == 1
    f = misplaced[0]
    assert f.level == "error"
    assert "MisplacedViz" in f.message
    assert "pkg/viz_misplaced.py" in f.message            # names the offending file
    assert "visualizations/" in f.message                 # names the fix location
    assert f.field_path.startswith("visualizations[")
    # It did NOT also fire the generic 'no such class' unresolved finding.
    assert by_check.get("visualization_address_unresolved", []) == []


def test_visualization_address_resolved_produces_no_findings(tmp_path):
    """Classes declared via subclassing OR via @as_visualization update_*
    factories resolve cleanly. Both PascalCase and snake_case forms work."""
    ws = _copy_fixture("viz-address-resolved", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    assert by_check.get("visualization_address_unresolved", []) == []


def test_visualization_address_check_tolerates_missing_visualizations_field(tmp_path):
    """A study with no visualizations[] block must not crash the linter."""
    ws = _copy_fixture("clean-baseline", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    assert by_check.get("visualization_address_unresolved", []) == []


def test_visualization_address_missing_fires_per_entry(tmp_path):
    """mem3dg-readdy friction #26: study.yaml.visualizations[] entries
    without `address:` 500 at render time with KeyError('address'). The
    fixture has two entries without address (one omitted, one empty
    string); both must fire `visualization_address_missing`."""
    ws = _copy_fixture("viz-address-missing", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    missing = by_check.get("visualization_address_missing", [])
    assert len(missing) == 2
    assert all(f.level == "error" for f in missing)
    viz_names_called_out = sorted(f.message for f in missing)
    assert any("barrier-kinematics" in m for m in viz_names_called_out)
    assert any("phase-space" in m for m in viz_names_called_out)
    # The actionable hint must name both fix paths.
    assert all("local:<ClassName>" in f.message for f in missing)
    assert all("workspace.yaml.visualizations" in f.message for f in missing)


def test_visualization_path_without_address_nudges_to_canonical(tmp_path):
    """A figure declared via the bare `path:` field (no `address:`) works in the
    workbench (tolerant discovery) but isn't canonical. The linter nudges toward
    `address: <scheme>:<file>` as a WARNING — not the hard address-missing error —
    and suggests the right scheme per file type. A real `local:` renderer entry
    must NOT be nudged, and the file-pointer entries must NOT fire the error."""
    ws = _copy_fixture("viz-path-not-canonical", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)

    nudges = by_check.get("visualization_path_not_canonical", [])
    assert len(nudges) == 2, [f.message for f in nudges]
    assert all(f.level == "warning" for f in nudges)
    names = {f.message.split("'")[1] for f in nudges}
    assert names == {"pop-timeseries", "colony-snapshot"}
    joined = " ".join(f.message for f in nudges)
    assert "address: html:viz/pop.html" in joined     # html scheme for .html
    assert "address: image:viz/colony.png" in joined  # image scheme for images
    assert all(f.field_path.startswith("visualizations[") for f in nudges)

    # The file-pointer entries must NOT also fire the hard address-missing error…
    missing = by_check.get("visualization_address_missing", [])
    missing_names = {f.message.split("'")[1] for f in missing}
    assert "pop-timeseries" not in missing_names
    assert "colony-snapshot" not in missing_names
    # …and the real live renderer must not be nudged.
    assert "live-renderer" not in names


# ---------------------------------------------------------------------------
# readout migration status — surface migratable + needs_human (SP2b-ii)
# ---------------------------------------------------------------------------


def test_linter_surfaces_readout_migration_status(tmp_path):
    """A study with migratable + needs_human readouts produces a lint finding
    naming both surfaces: the migratable ones (suggestion to canonicalize) and
    the needs_human ones (higher-severity, re-author against /api/observables)."""
    ws = _copy_fixture("readout-migration-status", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    rm = by_check.get("readout_migration_status", [])
    assert rm, "expected a readout_migration_status finding"
    msgs = " ".join(f.message for f in rm)
    # the 37-unresolved surface — needs_human re-authoring
    assert "needs_human" in msgs or "re-author" in msgs
    # the safe canonicalize surface — migratable
    assert "migratable" in msgs or "canonicaliz" in msgs
    # needs_human is the higher-severity surface (warning), migratable is info
    levels = {f.level for f in rm}
    assert "warning" in levels  # needs_human
    # the re-authoring path points at the SP2b-i observables endpoint
    assert "/api/observables" in msgs or "check-observables" in msgs
    # the canonicalize path names the explicit migrate subcommand
    assert "migrate-readouts" in msgs


def test_linter_readout_migration_status_silent_when_all_canonical(tmp_path):
    """A clean study with no readouts (or all-canonical) must not fire."""
    ws = _copy_fixture("clean-baseline", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    assert by_check.get("readout_migration_status", []) == []


# ---------------------------------------------------------------------------
# 12. dag_edges_* — F3 (canonical pipeline_gate.prerequisites)
# ---------------------------------------------------------------------------


def test_dag_edges_legacy_only_fires_migration_warning(tmp_path):
    """A study with parent_studies but no pipeline_gate.prerequisites fires
    the soft migration warning — same shape as the runtime DeprecationWarning
    the dashboard emits."""
    ws = _copy_fixture("dag-edges-legacy-only", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    legacy = by_check.get("dag_edges_legacy_only", [])
    assert len(legacy) == 1
    f = legacy[0]
    assert f.level == "warning"
    assert f.study_slug == "legacy"
    assert "pipeline_gate.prerequisites" in f.message
    assert "back-compat fallback" in f.message
    # The disagreement and redundant variants must NOT fire for this case.
    assert by_check.get("dag_edges_legacy_redundant", []) == []
    assert by_check.get("dag_edges_legacy_and_canonical_disagree", []) == []


def test_dag_edges_both_agree_fires_redundancy_warning(tmp_path):
    """When both fields list the same parent SLUG SET (regardless of per-entry
    condition), the legacy field is redundant — warn so the workspace drops
    it during the next edit."""
    ws = _copy_fixture("dag-edges-both-agree", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    redundant = by_check.get("dag_edges_legacy_redundant", [])
    assert len(redundant) == 1
    f = redundant[0]
    assert f.level == "warning"
    assert f.study_slug == "agree"
    assert "Drop the `parent_studies` field" in f.message
    # The legacy-only warning must NOT fire — the canonical field IS set.
    assert by_check.get("dag_edges_legacy_only", []) == []


def test_dag_edges_both_conflict_fires_error(tmp_path):
    """When both fields list DIFFERENT parent sets, the dashboard silently
    ignores the legacy entries — that's a real foot-gun, so it's an error."""
    ws = _copy_fixture("dag-edges-both-conflict", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    disagree = by_check.get("dag_edges_legacy_and_canonical_disagree", [])
    assert len(disagree) == 1
    f = disagree[0]
    assert f.level == "error"
    assert f.study_slug == "conflict"
    # Message names both sets so the author can see which side is which.
    assert "upstream-a" in f.message
    assert "upstream-z" in f.message
    assert "silently ignored" in f.message


def test_dag_edges_check_silent_on_clean_baseline(tmp_path):
    """A study with neither field set produces no DAG-edge findings."""
    ws = _copy_fixture("clean-baseline", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    assert by_check.get("dag_edges_legacy_only", []) == []
    assert by_check.get("dag_edges_legacy_redundant", []) == []
    assert by_check.get("dag_edges_legacy_and_canonical_disagree", []) == []


# ---------------------------------------------------------------------------
# 13. status_legacy_only — F1 (multi-axis status canonical)
# ---------------------------------------------------------------------------


def test_status_legacy_only_fires_migration_warning(tmp_path):
    """A study with top-level `status` but no multi-axis fields fires the
    migration warning — same message shape as the runtime DeprecationWarning
    from effective_status()."""
    ws = _copy_fixture("status-legacy-only", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    legacy = by_check.get("status_legacy_only", [])
    assert len(legacy) == 1
    f = legacy[0]
    assert f.level == "warning"
    assert f.study_slug == "legacy-status"
    assert "in-progress" in f.message
    # Names all six axes so the author can pick the right one.
    for axis in ("design_status", "implementation_status", "simulation_status",
                 "evaluation_status", "gate_status", "expert_review_status"):
        assert axis in f.message


def test_status_legacy_only_silent_when_multi_axis_set(tmp_path):
    """The `clean-baseline` fixture sets `status` alongside multi-axis
    fields. The check is silent there because at least one multi-axis
    field carries the canonical value — redundancy is harmless drift,
    not a foot-gun."""
    ws = _copy_fixture("clean-baseline", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    assert by_check.get("status_legacy_only", []) == []


def test_status_legacy_only_silent_on_findings_internal_status(tmp_path):
    """`findings[].status` is a different field (confirms/contradicts/etc).
    The check reads spec.get("status") which targets the TOP LEVEL only,
    so the finding-related fixtures with nested status fields stay silent
    on this check."""
    ws = _copy_fixture("finding-unknown-bib", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    assert by_check.get("status_legacy_only", []) == []


# ---------------------------------------------------------------------------
# status_claims_done_no_runs_recorded — forward-drift (dnaa-replication 5-31)
# ---------------------------------------------------------------------------


def test_reviewer_clarity_flags_ran_but_tests_pending(tmp_path):
    """A study that ran + declares gate passed but whose run has no outcomes
    (so every test pill renders 'pending') is flagged by the clarity check."""
    ws = _copy_fixture("reviewer-clarity", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    flagged = by_check.get("reviewer_clarity_ambiguity", [])
    assert len(flagged) == 1
    f = flagged[0]
    assert f.level == "warning"
    assert f.study_slug == "study-ran-no-outcomes"
    assert "pending" in f.message


def test_status_claims_done_no_runs_fires_when_no_run_provenance(tmp_path):
    """A study declaring completion (gate passed / ran / evaluated) with no
    runs:, simulation_set:, or planned_runs: block fires a warning; a sibling
    study with the same claim but a runs: block stays silent."""
    ws = _copy_fixture("claims-done-no-runs", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    flagged = by_check.get("status_claims_done_no_runs_recorded", [])
    assert len(flagged) == 1
    f = flagged[0]
    assert f.level == "warning"
    assert f.study_slug == "study-claims-no-runs"
    assert f.field_path == "runs"
    assert "records no runs" in f.message


# ---------------------------------------------------------------------------
# 14. runs_yaml_vs_db_drift — F2 (runs.db is canonical)
# ---------------------------------------------------------------------------


def _seed_runs_db(ws: Path, study_slug: str, run_ids: list[str]) -> None:
    """Populate studies/<slug>/runs.db with runs_meta rows for the given ids."""
    import sqlite3
    db = ws / "studies" / study_slug / "runs.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db))
    conn.execute("""
        CREATE TABLE runs_meta (
            run_id      TEXT PRIMARY KEY,
            spec_id     TEXT NOT NULL,
            started_at  REAL NOT NULL,
            status      TEXT NOT NULL,
            sim_name    TEXT
        )
    """)
    for rid in run_ids:
        conn.execute(
            "INSERT INTO runs_meta (run_id, spec_id, started_at, status, sim_name) "
            "VALUES (?, 'pkg.x', 1.0, 'completed', ?)", (rid, rid),
        )
    conn.commit()
    conn.close()


def test_runs_yaml_vs_db_drift_warns_on_yaml_only_entries(tmp_path):
    """study.yaml.runs[] lists run_ids that runs.db doesn't have. The
    dashboard shows them via the back-compat count fallback but can't
    pull metadata — flag as warning so the author either restores
    runs.db or drops the legacy entries."""
    ws = _copy_fixture("runs-yaml-drift-missing-db", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    drift = by_check.get("runs_yaml_vs_db_drift", [])
    assert len(drift) == 1
    f = drift[0]
    assert f.level == "warning"
    assert f.study_slug == "drift"
    # Names at least one of the offending run_ids
    assert "ghost-1" in f.message or "ghost-2" in f.message
    assert "runs.db" in f.message
    # The redundant variant must NOT fire.
    assert by_check.get("runs_yaml_vs_db_redundant", []) == []


def test_runs_yaml_vs_db_redundant_fires_info(tmp_path):
    """yaml entries that exactly match runs.db are redundant — info-level
    (not a warning) because nothing is actually broken; the workspace is
    mid-migration."""
    ws = _copy_fixture("runs-yaml-redundant", tmp_path / "ws")
    _seed_runs_db(ws, "redundant", ["r1", "r2"])
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    redundant = by_check.get("runs_yaml_vs_db_redundant", [])
    assert len(redundant) == 1
    f = redundant[0]
    assert f.level == "info"
    assert f.study_slug == "redundant"
    assert "drop study.yaml.runs[]" in f.message
    # The drift warning must NOT fire — all yaml ids are in db.
    assert by_check.get("runs_yaml_vs_db_drift", []) == []


def test_runs_yaml_vs_db_drift_silent_when_yaml_empty(tmp_path):
    """The F2 target state — study.yaml has no runs[] field — must be
    silent regardless of what's in runs.db."""
    ws = _copy_fixture("clean-baseline", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    assert by_check.get("runs_yaml_vs_db_drift", []) == []
    assert by_check.get("runs_yaml_vs_db_redundant", []) == []


# ---------------------------------------------------------------------------
# Override file roundtrip
# ---------------------------------------------------------------------------


def test_override_keys_are_stable_across_runs(tmp_path):
    ws = _copy_fixture("incomplete-summary", tmp_path / "ws")
    a = lint_workspace_report(ws)
    b = lint_workspace_report(ws)
    keys_a = sorted(f.override_key for f in a)
    keys_b = sorted(f.override_key for f in b)
    assert keys_a == keys_b
    assert all(":" in k for k in keys_a)  # check:slug:hash shape


def test_write_override_creates_file_and_downgrades_finding(tmp_path):
    ws = _copy_fixture("incomplete-summary", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    blockers = [f for f in findings if f.level == "error"]
    assert blockers
    write_override(ws, blockers[0], reason="acked in PR-123")

    path = override_path(ws)
    assert path.is_file()
    data = json.loads(path.read_text())
    assert data["schema_version"] == 1
    assert len(data["overrides"]) == 1
    entry = data["overrides"][0]
    assert entry["key"] == blockers[0].override_key
    assert entry["reason"] == "acked in PR-123"
    assert entry["check"] == "incomplete_summaries"
    assert entry["study_slug"] == "study-incomplete"

    overrides = load_overrides(ws)
    assert blockers[0].override_key in overrides
    assert not has_blocking_errors(findings, overrides)

    downgraded = apply_overrides(findings, overrides)
    matching = [f for f in downgraded if f.override_key == blockers[0].override_key]
    assert matching and matching[0].level == "warning"
    assert "[overridden]" in matching[0].message


def test_write_override_is_idempotent(tmp_path):
    ws = _copy_fixture("incomplete-summary", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    f = [x for x in findings if x.level == "error"][0]
    write_override(ws, f)
    write_override(ws, f)
    data = json.loads(override_path(ws).read_text())
    assert len(data["overrides"]) == 1


# ---------------------------------------------------------------------------
# Integration: render_workspace_report refuses to render on blocking errors
# ---------------------------------------------------------------------------


def test_render_workspace_report_blocks_on_lint_errors(tmp_path):
    from discovery_engine.report import render_workspace_report, ReportLintBlocked

    ws = _copy_fixture("incomplete-summary", tmp_path / "ws")
    with pytest.raises(ReportLintBlocked) as excinfo:
        render_workspace_report(ws, today="2026-05-17")
    assert excinfo.value.findings
    # The HTML must NOT have been written.
    assert not (ws / "reports" / "index.html").exists()


def test_render_workspace_report_force_logs_overrides_and_proceeds(tmp_path):
    """--force writes overrides AND renders. Re-run is then clean."""
    from discovery_engine.report import render_workspace_report

    ws = _copy_fixture("incomplete-summary", tmp_path / "ws")

    # Without a templates dir / decisions file the template render itself
    # may still fail — but the linter gate happens FIRST, so if force
    # gets past the linter, the linter half of Pass B works. We still
    # verify the override file got populated even if the subsequent HTML
    # render is unrelated to lint logic.
    try:
        render_workspace_report(ws, today="2026-05-17", force=True)
    except Exception:
        pass

    # The override file must exist and contain the previously-blocking finding.
    data = json.loads(override_path(ws).read_text())
    assert data["overrides"], "force=True must have logged at least one override"

    # Re-run: now the linter should be clean (or at least not blocking).
    findings = lint_workspace_report(ws)
    overrides = load_overrides(ws)
    assert not has_blocking_errors(findings, overrides)


def test_render_workspace_report_lint_false_bypasses_check(tmp_path):
    """lint=False preserves pre-Pass-B unconditional behavior."""
    from discovery_engine.report import render_workspace_report

    ws = _copy_fixture("incomplete-summary", tmp_path / "ws")
    # The linter would otherwise block; with lint=False we skip it
    # entirely. Render may still fail downstream on template lookup but
    # NOT with ReportLintBlocked.
    try:
        render_workspace_report(ws, today="2026-05-17", lint=False)
    except Exception as e:
        from discovery_engine.report import ReportLintBlocked
        assert not isinstance(e, ReportLintBlocked)


# ---------------------------------------------------------------------------
# CLI: python -m discovery_engine.report_linter
# ---------------------------------------------------------------------------


def test_cli_exits_0_on_clean_workspace(tmp_path, capsys):
    ws = _copy_fixture("clean-baseline", tmp_path / "ws")
    rc = main(["--ws", str(ws)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "OK" in out


def test_cli_exits_1_on_blocking_findings(tmp_path, capsys):
    ws = _copy_fixture("incomplete-summary", tmp_path / "ws")
    rc = main(["--ws", str(ws)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "BLOCKING" in err


def test_cli_force_exits_0_and_writes_overrides(tmp_path, capsys):
    ws = _copy_fixture("incomplete-summary", tmp_path / "ws")
    rc = main(["--ws", str(ws), "--force"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "logged" in err
    assert override_path(ws).is_file()


def test_cli_json_mode_emits_valid_json(tmp_path, capsys):
    ws = _copy_fixture("incomplete-summary", tmp_path / "ws")
    main(["--ws", str(ws), "--json"])
    out = capsys.readouterr().out
    data = json.loads(out)
    assert isinstance(data, list)
    assert all("level" in entry and "override_key" in entry for entry in data)


# ---------------------------------------------------------------------------
# format_findings smoke
# ---------------------------------------------------------------------------


def test_format_findings_empty_returns_OK(tmp_path):
    assert "OK" in format_findings([])


def test_format_findings_renders_each_level(tmp_path):
    findings = [
        LintFinding(level="error", study_slug="s", field_path="f", message="m1",
                    override_key="k1", check="x"),
        LintFinding(level="warning", study_slug="s", field_path="f", message="m2",
                    override_key="k2", check="y"),
    ]
    txt = format_findings(findings)
    assert "[ERROR]" in txt
    assert "[WARNING]" in txt
    assert "override_key: k1" in txt


# ---------------------------------------------------------------------------
# Anti-slop & honesty checks (added 2026-05-25 after pdmp-* feedback)
# ---------------------------------------------------------------------------


def test_machine_projected_tests_fires_on_auto_projected_v4_tests(tmp_path):
    """tests[] entries that mirror expected_behavior[].name AND are missing
    classification + have stringified-dict measure → AI-slop pattern."""
    ws = _copy_fixture("machine-projected-tests", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    slop = by_check.get("machine_projected_tests", [])
    assert len(slop) == 1
    f = slop[0]
    assert f.level == "warning"
    assert f.study_slug == "study-slop"
    assert "auto-projected" in f.message
    assert "classification" in f.message


def test_speculative_readout_paths_fires_per_entry(tmp_path):
    """A readout with status=implemented but no file at path → error.
    A readout with status=planned + speculative path → warning.
    TBD-prefixed or no-path readouts → no finding."""
    ws = _copy_fixture("speculative-readout-paths", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    spec = by_check.get("speculative_readout_path", [])
    # Expect 2 findings: alpha-real (error) + beta-speculative (warning)
    assert len(spec) == 2
    by_level = {f.level: f for f in spec}
    assert "error" in by_level
    assert "warning" in by_level
    assert by_level["error"].field_path == "readouts[0].path"
    assert "alpha-real" in by_level["error"].message
    assert by_level["warning"].field_path == "readouts[1].path"
    assert "beta-speculative" in by_level["warning"].message


def test_viz_stale_vs_latest_run_fires_on_mismatch(tmp_path):
    import sqlite3
    from discovery_engine.run_registry import RUNS_META_DDL
    from discovery_engine.viz_freshness import stamp_meta
    from discovery_engine.report_linter import _LintContext, _check_viz_stale_vs_latest_run
    sd = tmp_path / "studies" / "s1"; (sd / "charts").mkdir(parents=True)
    (sd / "charts" / "c.svg").write_text("x")
    stamp_meta(sd / "charts" / "c.svg", source_run_id="OLD",
               generation_id=None, rendered_at=1.0, command="cmd")
    db = sd / "runs.db"; conn = sqlite3.connect(db); conn.executescript(RUNS_META_DDL)
    conn.execute("INSERT INTO runs_meta(run_id,spec_id,started_at,completed_at,status)"
                 " VALUES('NEW','s1',1,2,'complete')"); conn.commit(); conn.close()
    spec = {"evaluation_status": "evaluated",
            "visualizations": [{"name": "v", "chart": "charts/c.svg", "render": "cmd"}]}
    ctx = _LintContext(ws_root=tmp_path, slug="s1", spec=spec)
    _check_viz_stale_vs_latest_run(ctx)
    stale = [f for f in ctx.findings if f.check == "viz_stale_vs_latest_run"]
    # Folded + demoted: a single per-study INFO finding (not a warning gap).
    assert len(stale) == 1
    assert stale[0].level == "info"


def test_viz_stale_folds_untracked_into_one_info(tmp_path):
    """N unregistered on-disk charts produce ONE info finding, not N warnings,
    so the viz_stale noise stops counting as a gap (gaps = error+warning)."""
    from discovery_engine.report_linter import _LintContext, _check_viz_stale_vs_latest_run
    sd = tmp_path / "studies" / "s1"; (sd / "charts").mkdir(parents=True)
    for n in ("a", "b", "c"):
        (sd / "charts" / f"{n}.svg").write_text("x")
    # No visualizations[] registered → all three charts are untracked orphans.
    spec = {"evaluation_status": "evaluated", "visualizations": []}
    ctx = _LintContext(ws_root=tmp_path, slug="s1", spec=spec)
    _check_viz_stale_vs_latest_run(ctx)
    stale = [f for f in ctx.findings if f.check == "viz_stale_vs_latest_run"]
    assert len(stale) == 1
    assert stale[0].level == "info"
    assert "3 chart(s)" in stale[0].message


def test_viz_stale_error_under_strict(tmp_path):
    import sqlite3
    from discovery_engine.run_registry import RUNS_META_DDL
    from discovery_engine.viz_freshness import stamp_meta
    from discovery_engine.report_linter import _LintContext, _check_viz_stale_vs_latest_run
    sd = tmp_path / "studies" / "s1"; (sd / "charts").mkdir(parents=True)
    (sd / "charts" / "c.svg").write_text("x")
    stamp_meta(sd / "charts" / "c.svg", source_run_id="OLD",
               generation_id=None, rendered_at=1.0, command="cmd")
    db = sd / "runs.db"; conn = sqlite3.connect(db); conn.executescript(RUNS_META_DDL)
    conn.execute("INSERT INTO runs_meta(run_id,spec_id,started_at,completed_at,status)"
                 " VALUES('NEW','s1',1,2,'complete')"); conn.commit(); conn.close()
    spec = {"visualizations": [{"name": "v", "chart": "charts/c.svg", "render": "cmd"}]}
    ctx = _LintContext(ws_root=tmp_path, slug="s1", spec=spec, strict=True)
    _check_viz_stale_vs_latest_run(ctx)
    stale = [f for f in ctx.findings if f.check == "viz_stale_vs_latest_run"]
    assert len(stale) == 1 and stale[0].level == "error"
def test_finding_without_statement_fires_for_empty_or_missing(tmp_path):
    """finding_without_statement is error-level for empty/missing statements."""
    from pathlib import Path
    from discovery_engine.report_linter import (
        _LintContext,
        _check_finding_without_statement,
    )
    spec = {
        "findings": [
            {"id": "F-filled", "statement": "Something concrete happened."},
            {"id": "F-empty", "statement": "   "},
            {"id": "F-missing"},
        ]
    }
    ctx = _LintContext(ws_root=Path("."), slug="s1", spec=spec)
    _check_finding_without_statement(ctx)
    errs = [f for f in ctx.findings if f.check == "finding_without_statement"]
    assert len(errs) == 2
    assert all(f.level == "error" for f in errs)
    assert any("F-empty" in f.message for f in errs)
    assert any("F-missing" in f.message for f in errs)
    assert not any("F-filled" in f.message for f in errs)


# ---------------------------------------------------------------------------
# Wave 3a — workflow-typing enum soft checks (critique #7 / #10)
# ---------------------------------------------------------------------------

import yaml as _yaml


def _ws_with_study(tmp_path, spec):
    ws = tmp_path / "ws"
    sd = ws / "studies" / spec.get("name", "s")
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("schema_version: 2\nname: ws\npackage_path: pbg_ws\n")
    (sd / "study.yaml").write_text(_yaml.safe_dump(spec))
    return ws


def test_next_action_type_missing_warns(tmp_path):
    ws = _ws_with_study(tmp_path, {
        "name": "s", "findings": [
            {"id": "F1", "statement": "x.", "next_action": "Calibrate kS"},
        ]})
    by_check = _findings_by_check(lint_workspace_report(ws))
    fs = by_check.get("next_action_type_missing", [])
    assert len(fs) == 1
    assert fs[0].level == "warning"


def test_next_action_type_present_silent(tmp_path):
    ws = _ws_with_study(tmp_path, {
        "name": "s", "findings": [
            {"id": "F1", "statement": "x.", "next_action": "Calibrate kS",
             "next_action_type": "calibrate"},
        ]})
    by_check = _findings_by_check(lint_workspace_report(ws))
    assert by_check.get("next_action_type_missing", []) == []
    assert by_check.get("next_action_type_unknown", []) == []


def test_next_action_type_unknown_warns(tmp_path):
    ws = _ws_with_study(tmp_path, {
        "name": "s", "findings": [
            {"id": "F1", "statement": "x.", "next_action": "do",
             "next_action_type": "frobnicate"},
        ]})
    by_check = _findings_by_check(lint_workspace_report(ws))
    assert len(by_check.get("next_action_type_unknown", [])) == 1


def test_study_type_unknown_warns(tmp_path):
    ws = _ws_with_study(tmp_path, {"name": "s", "study_type": "speculative"})
    by_check = _findings_by_check(lint_workspace_report(ws))
    assert len(by_check.get("study_type_unknown", [])) == 1


def test_study_type_known_and_kind_alias_silent(tmp_path):
    ws = _ws_with_study(tmp_path, {"name": "s", "study_type": "confirmatory"})
    by_check = _findings_by_check(lint_workspace_report(ws))
    assert by_check.get("study_type_unknown", []) == []
    ws2 = _ws_with_study(tmp_path / "b", {"name": "s2", "kind": "adversarial"})
    by_check2 = _findings_by_check(lint_workspace_report(ws2))
    assert by_check2.get("study_type_unknown", []) == []


# ---------------------------------------------------------------------------
# Real-composite resolution — unresolved_composite_refs (pure helper)
# ---------------------------------------------------------------------------


def test_unresolved_composite_refs_collects_from_all_sites():
    spec = {
        "baseline": [{"name": "b", "composite": "pkg.composites.real"}],
        "conditions": {"baseline": {"composite": "pkg.composites.cond"}},
        "simulation_set": [{"name": "r", "base_model": "pkg.composites.sim"}],
    }
    known = {"pkg.composites.real"}
    out = unresolved_composite_refs(spec, known)
    # real resolves; cond + sim do not
    assert out == ["pkg.composites.cond", "pkg.composites.sim"]


def test_unresolved_composite_refs_empty_when_all_resolve():
    spec = {"baseline": [{"composite": "a"}], "simulation_set": [{"composite": "a"}]}
    assert unresolved_composite_refs(spec, {"a"}) == []


def test_unresolved_composite_refs_dedupes_in_order():
    spec = {
        "baseline": [{"composite": "x"}, {"composite": "y"}, {"composite": "x"}],
    }
    assert unresolved_composite_refs(spec, set()) == ["x", "y"]


def test_unresolved_composite_refs_empty_registry_returns_all():
    spec = {"baseline": [{"composite": "x"}]}
    assert unresolved_composite_refs(spec, None) == ["x"]
    assert unresolved_composite_refs(spec, set()) == ["x"]


def test_unresolved_composite_refs_tolerant_of_missing_fields():
    assert unresolved_composite_refs({}, {"a"}) == []
    assert unresolved_composite_refs({"baseline": "not-a-list"}, set()) == []


# ---------------------------------------------------------------------------
# runs_without_emitter soft-WARN
# ---------------------------------------------------------------------------


def test_runs_without_emitter_warns(tmp_path):
    ws = _ws_with_study(tmp_path, {
        "name": "s", "runs": [{"name": "r1"}, {"name": "r2"}]})
    by_check = _findings_by_check(lint_workspace_report(ws))
    fs = by_check.get("runs_without_emitter", [])
    assert len(fs) == 1
    assert fs[0].level == "warning"


def test_runs_without_emitter_silent_when_emitter_backed(tmp_path):
    ws = _ws_with_study(tmp_path, {
        "name": "s", "runs": [{"name": "r1", "emitter": "sqlite"}]})
    by_check = _findings_by_check(lint_workspace_report(ws))
    assert by_check.get("runs_without_emitter", []) == []


def test_runs_without_emitter_silent_when_no_runs(tmp_path):
    ws = _ws_with_study(tmp_path, {"name": "s"})
    by_check = _findings_by_check(lint_workspace_report(ws))
    assert by_check.get("runs_without_emitter", []) == []


def test_runs_without_emitter_silent_when_runs_db_has_rows(tmp_path):
    # Canonical persistence in runs.db (F2) suppresses the warning even if the
    # YAML run records don't restate the emitter.
    ws = _ws_with_study(tmp_path, {"name": "s", "runs": [{"name": "r1"}]})
    _seed_runs_db(ws, "s", ["r1"])
    by_check = _findings_by_check(lint_workspace_report(ws))
    assert by_check.get("runs_without_emitter", []) == []


# ---------------------------------------------------------------------------
# investigation_narrative_spine_required (REQUIRED, skippable)
# ---------------------------------------------------------------------------

import yaml as _yaml


def _mk_investigation(tmp_path, inv: dict):
    ws = tmp_path / "ws"
    d = ws / "investigations" / "demo"
    d.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: demo\n")
    (d / "investigation.yaml").write_text(_yaml.safe_dump(inv))
    return ws


def _narrative_findings(ws):
    return [f for f in lint_workspace_report(ws)
            if f.check == "investigation_narrative_spine_required"]


def test_investigation_narrative_required_fires_when_missing(tmp_path):
    ws = _mk_investigation(tmp_path, {"name": "demo", "schema_version": 2})
    f = _narrative_findings(ws)
    assert {x.field_path for x in f} == {"executive", "scientific_argument", "biological_story"}
    assert all(x.level == "warning" for x in f)  # required = blocking


def test_investigation_narrative_satisfied_when_authored(tmp_path):
    ws = _mk_investigation(tmp_path, {
        "name": "demo",
        "executive": {"what_is_this": "x"},
        "scientific_argument": {"main_claim": "y"},
        "biological_story": "z",
    })
    assert _narrative_findings(ws) == []


def test_investigation_narrative_explicit_skip_suppresses(tmp_path):
    ws = _mk_investigation(tmp_path, {
        "name": "demo",
        "narrative_spine_skip": ["executive", "scientific_argument", "biological_story"],
        "narrative_spine_skip_reason": "slim single-study screen",
    })
    assert _narrative_findings(ws) == []


def test_investigation_narrative_partial_skip(tmp_path):
    ws = _mk_investigation(tmp_path, {
        "name": "demo",
        "executive": {"verdict": "passing"},
        "narrative_spine_skip": ["biological_story"],
    })
    f = _narrative_findings(ws)
    assert {x.field_path for x in f} == {"scientific_argument"}  # only the un-skipped, un-authored one


# ---------------------------------------------------------------------------
# Visualization file existence + misplacement guard (_check_visualization_files)
# ---------------------------------------------------------------------------


def _min_viz_ws(root: Path, study_yaml: str, *, figures=None, alt_figures=None) -> Path:
    """Minimal workspace: one study + optional figures at the canonical
    (reports/figures/<slug>/) and/or look-alike (workspace/reports/figures/)
    locations."""
    (root / "studies" / "s1").mkdir(parents=True)
    (root / "workspace.yaml").write_text(
        "schema_version: 2\nname: t\ncreated: '2026-06-16'\npackage_path: pkg\n")
    (root / "studies" / "s1" / "study.yaml").write_text(study_yaml)
    for loc, names in (("reports/figures/s1", figures), ("workspace/reports/figures/s1", alt_figures)):
        if names:
            d = root / loc
            d.mkdir(parents=True, exist_ok=True)
            for n in names:
                (d / n).write_text("<html></html>")
    return root


def test_viz_embed_missing_file_fires(tmp_path):
    ws = _min_viz_ws(tmp_path / "ws",
                     "name: s1\nembed_visualizations:\n"
                     "  - name: f\n    url: /reports/figures/s1/missing.html\n")
    checks = _findings_by_check(lint_workspace_report(ws))
    assert "viz_file_missing" in checks


def test_viz_embed_present_file_clean(tmp_path):
    ws = _min_viz_ws(tmp_path / "ws",
                     "name: s1\nembed_visualizations:\n"
                     "  - name: f\n    url: /reports/figures/s1/there.html\n",
                     figures=["there.html"])
    checks = _findings_by_check(lint_workspace_report(ws))
    assert "viz_file_missing" not in checks
    assert "viz_misplaced" not in checks


def test_viz_image_address_missing_file_fires(tmp_path):
    ws = _min_viz_ws(tmp_path / "ws",
                     "name: s1\nvisualizations:\n"
                     "  - name: f\n    address: image:charts/nope.png\n")
    checks = _findings_by_check(lint_workspace_report(ws))
    assert "viz_file_missing" in checks


def test_viz_misplaced_in_workspace_reports_fires(tmp_path):
    # Figure parked in workspace/reports/figures/<slug>/ (not canonical) -> warn.
    ws = _min_viz_ws(tmp_path / "ws", "name: s1\n", alt_figures=["orphan.html"])
    checks = _findings_by_check(lint_workspace_report(ws))
    assert "viz_misplaced" in checks
    assert any("orphan.html" in f.message for f in checks["viz_misplaced"])


def test_missing_visualizations_requires_a_DECLARED_viz(tmp_path):
    # A DECLARED visualization satisfies the rule: embed_visualizations[] counts.
    ws1 = _min_viz_ws(tmp_path / "w1",
                      "name: s1\nembed_visualizations:\n"
                      "  - name: f\n    url: /reports/figures/s1/there.html\n",
                      figures=["there.html"])
    assert "missing_visualizations" not in _findings_by_check(lint_workspace_report(ws1))
    # STRENGTHENED: an on-disk figure alone no longer satisfies it — only a
    # declared visualizations[] / embed_visualizations[] does — so this now
    # ERRORS (the workbench renders declared viz, not loose figure files).
    ws2 = _min_viz_ws(tmp_path / "w2", "name: s1\n", figures=["auto.html"])
    checks2 = _findings_by_check(lint_workspace_report(ws2))
    assert "missing_visualizations" in checks2
    assert checks2["missing_visualizations"][0].level == "error"
    # nothing at all -> also an error.
    ws3 = _min_viz_ws(tmp_path / "w3", "name: s1\n")
    checks3 = _findings_by_check(lint_workspace_report(ws3))
    assert "missing_visualizations" in checks3
    assert checks3["missing_visualizations"][0].level == "error"


# --- Phase-aware completeness gating (issue #97) ----------------------------

from discovery_engine.report_linter import (  # noqa: E402
    _LintContext,
    _check_missing_planned_runs,
    _completeness_level,
)


def test_completeness_level_gates_on_phase():
    # No phase signal -> preserve pre-#97 behavior (warn).
    assert _completeness_level({}, warn_at="Simulate") == "warning"
    # Below the threshold -> downgraded to info (correctly sparse for the stage).
    assert _completeness_level({"phase": "Design"}, warn_at="Simulate") == "info"
    assert _completeness_level({"phase": "Build"}, warn_at="Simulate") == "info"
    # At/after the threshold -> genuinely overdue -> warn.
    assert _completeness_level({"phase": "Simulate"}, warn_at="Simulate") == "warning"
    assert _completeness_level({"phase": "Decide"}, warn_at="Simulate") == "warning"
    # Case-insensitive; unknown phase strings fall back to warn.
    assert _completeness_level({"phase": "design"}, warn_at="Build") == "info"
    assert _completeness_level({"phase": "bogus"}, warn_at="Build") == "warning"


def test_completeness_level_infers_stage_from_status_when_phase_absent():
    """A seeded/planned study often has status signals but no `phase:`. Infer the
    stage from them rather than defaulting to warn, so an unbuilt design isn't
    flagged for missing runs/variants/conditions it legitimately can't have yet."""
    # planned / not-run / drafted -> Design-stage -> completeness checks stay info.
    planned = {"status": "planned", "simulation_status": "not_run", "design_status": "drafted"}
    assert _completeness_level(planned, warn_at="Build") == "info"
    assert _completeness_level(planned, warn_at="Simulate") == "info"
    # Any one signal is enough.
    assert _completeness_level({"simulation_status": "not_run"}, warn_at="Build") == "info"
    assert _completeness_level({"status": "draft"}, warn_at="Build") == "info"
    # A ran study with no phase -> Evaluate -> built content is genuinely expected.
    assert _completeness_level({"simulation_status": "ran"}, warn_at="Build") == "warning"
    assert _completeness_level({"status": "ran"}, warn_at="Simulate") == "warning"
    # No phase AND no status signal at all -> preserve back-compat warn.
    assert _completeness_level({}, warn_at="Build") == "warning"


@pytest.mark.parametrize(
    "phase, expected_level",
    [(None, "warning"), ("Design", "info"), ("Build", "info"), ("Simulate", "warning")],
)
def test_missing_planned_runs_phase_gated(tmp_path, phase, expected_level):
    """A Design-stage study with no planned_runs is 'correctly sparse' (info);
    a Simulate-stage one is genuinely overdue (warning). Regression for #97 —
    the completeness warning no longer buries real findings during Design."""
    spec = {} if phase is None else {"phase": phase}
    ctx = _LintContext(ws_root=tmp_path, slug="s1", spec=spec)
    _check_missing_planned_runs(ctx)
    fired = [f for f in ctx.findings if f.check == "missing_planned_runs"]
    assert len(fired) == 1
    assert fired[0].level == expected_level


# --- Render-guarantee check (issue #96) -------------------------------------

import discovery_engine.report_linter as _rl  # noqa: E402
from discovery_engine.report_linter import _check_renders_via_dashboard  # noqa: E402


def _render_ctx(tmp_path, slug="s1"):
    return _LintContext(ws_root=tmp_path, slug=slug, spec={"name": slug})


def test_render_check_skips_when_server_absent(tmp_path, monkeypatch):
    """When the workbench isn't reachable, the check is a silent no-op."""
    monkeypatch.setattr(_rl, "_dashboard_render_error", lambda ws, slug: None)
    ctx = _render_ctx(tmp_path)
    _check_renders_via_dashboard(ctx)
    assert ctx.findings == []


def test_render_check_flags_unloadable_spec(tmp_path, monkeypatch):
    """A spec that passes static lint but fails the dashboard loader is an
    error-level render_blocked finding (regression for #96)."""
    ctx = _render_ctx(tmp_path)
    monkeypatch.setattr(
        _rl, "_dashboard_render_error",
        lambda ws, slug: (
            "failed to build study 's1': InvestigationSpecError: "
            "'baseline' must be a non-empty list of composites"
        ),
    )
    _check_renders_via_dashboard(ctx)
    assert [f.check for f in ctx.findings] == ["render_blocked"]
    assert ctx.findings[0].level == "error"
    assert "baseline" in ctx.findings[0].message


def test_render_check_passes_loadable_spec(tmp_path, monkeypatch):
    """When the dashboard loads the study, there's no finding."""
    ctx = _render_ctx(tmp_path)
    monkeypatch.setattr(_rl, "_dashboard_render_error", lambda ws, slug: None)
    _check_renders_via_dashboard(ctx)
    assert ctx.findings == []


def test_render_blocked_registered_in_checks():
    assert "render_blocked" in _rl.CHECKS


# --- _dashboard_render_error HTTP parsing -----------------------------------

def _http_error(url, code, payload):
    import io
    import json as _json
    import urllib.error
    body = io.BytesIO(_json.dumps(payload).encode("utf-8"))
    return urllib.error.HTTPError(url, code, "err", {}, body)


def test_dashboard_render_error_flags_spec_error(tmp_path, monkeypatch):
    """A 500 whose error names InvestigationSpecError → returns the message."""
    import discovery_engine.server_preflight as _sp
    monkeypatch.setattr(_sp, "read_server_url", lambda root=".": "http://localhost:0")

    def boom(req, timeout=None):
        raise _http_error(
            req.full_url, 500,
            {"error": "failed to build study 's1': InvestigationSpecError: bad baseline"},
        )
    monkeypatch.setattr("urllib.request.urlopen", boom)
    err = _rl._dashboard_render_error(tmp_path, "s1")
    assert err and "InvestigationSpecError" in err


def test_dashboard_render_error_ignores_non_spec_500(tmp_path, monkeypatch):
    """A 500 from some other loader/enrichment failure is NOT a render block."""
    import discovery_engine.server_preflight as _sp
    monkeypatch.setattr(_sp, "read_server_url", lambda root=".": "http://localhost:0")

    def boom(req, timeout=None):
        raise _http_error(req.full_url, 500, {"error": "failed to build study 's1': KeyError: 'runs'"})
    monkeypatch.setattr("urllib.request.urlopen", boom)
    assert _rl._dashboard_render_error(tmp_path, "s1") is None


def test_dashboard_render_error_skips_when_no_server(tmp_path, monkeypatch):
    import discovery_engine.server_preflight as _sp
    monkeypatch.setattr(_sp, "read_server_url", lambda root=".": None)
    assert _rl._dashboard_render_error(tmp_path, "s1") is None


# ---------------------------------------------------------------------------
# perf/report-linter-cache — workspace-global scans run once per lint run,
# not once per study.
# ---------------------------------------------------------------------------


def _multi_study_ws_for_scan_cache(tmp_path, n: int):
    """An n-study workspace that exercises all three workspace-global scans
    (bib keys, expert-doc names, viz classes) identically in every study —
    each study cites a known + an unknown bib key, references a known + an
    unknown expert doc, and addresses a resolved + an unresolved local viz
    class. Every one of those checks depends only on ``ws_root``, so before
    memoization each scan ran once PER STUDY (n times); after memoization
    each should run once for the whole lint call.
    """
    ws = tmp_path / "ws"
    (ws / "references").mkdir(parents=True)
    (ws / "references" / "papers.bib").write_text(
        "@article{KnownKey2020,\n  title = {Known},\n  year = {2020},\n}\n"
    )
    (ws / "pkg" / "visualizations").mkdir(parents=True)
    (ws / "pkg" / "visualizations" / "viz.py").write_text(
        "class KnownViz:\n    pass\n"
    )
    (ws / "workspace.yaml").write_text(_yaml.safe_dump({
        "schema_version": 2,
        "name": "multi-study-scan-cache",
        "package_path": "pkg",
        "expert_docs": [
            {"name": "known_doc", "path": "references/expert/known.pdf"},
        ],
    }))
    slugs = [f"study-{i}" for i in range(n)]
    for slug in slugs:
        sd = ws / "studies" / slug
        sd.mkdir(parents=True)
        spec = {
            "name": slug,
            "baseline": [{"name": "b1", "composite": "pkg.composites.x"}],
            "findings": [
                {
                    "id": "F-01",
                    "kind": "biological",
                    "status": "confirms",
                    "statement": "Cites a known and an unknown bib key.",
                    "evidence": {"from_test": "t1"},
                    "expected": {"cites": ["KnownKey2020", "UnknownKey2099"]},
                },
                {
                    "id": "F-02",
                    "kind": "biological",
                    "status": "confirms",
                    "statement": "References an unknown expert doc.",
                    "evidence": {"from_test": "t2"},
                    "expert_reference": {"doc": "unknown_doc_not_registered"},
                },
            ],
            "visualizations": [
                {"name": "viz-ok", "address": "local:KnownViz"},
                {"name": "viz-bad", "address": "local:MissingViz"},
            ],
        }
        (sd / "study.yaml").write_text(_yaml.safe_dump(spec))
    return ws, slugs


def test_workspace_scans_run_once_per_lint_not_once_per_study(tmp_path, monkeypatch):
    """The three workspace-global scans (bib keys, expert-doc names, viz
    classes) depend only on ws_root, not on the individual study. Before
    the perf/report-linter-cache fix, each ran once PER STUDY (N times for
    an N-study workspace); this asserts each now runs exactly once for the
    whole lint_workspace_report() call, and that the findings are exactly
    what an uncached scan would have produced (memoization must not change
    or leak results across studies)."""
    n = 4
    ws, slugs = _multi_study_ws_for_scan_cache(tmp_path, n=n)

    calls = {"bib_keys": 0, "expert_doc_names": 0, "viz_classes": 0}
    orig_bib = _rl._bib_keys_for_workspace
    orig_expert = _rl._expert_doc_names_for_workspace
    orig_viz = _rl._viz_classes_in_workspace

    def counting_bib(ws_root):
        calls["bib_keys"] += 1
        return orig_bib(ws_root)

    def counting_expert(ws_root):
        calls["expert_doc_names"] += 1
        return orig_expert(ws_root)

    def counting_viz(ws_root):
        calls["viz_classes"] += 1
        return orig_viz(ws_root)

    monkeypatch.setattr(_rl, "_bib_keys_for_workspace", counting_bib)
    monkeypatch.setattr(_rl, "_expert_doc_names_for_workspace", counting_expert)
    monkeypatch.setattr(_rl, "_viz_classes_in_workspace", counting_viz)

    findings = lint_workspace_report(ws)

    assert calls == {"bib_keys": 1, "expert_doc_names": 1, "viz_classes": 1}, (
        f"expected each workspace-global scan to run exactly once across a "
        f"{n}-study lint run (memoized), got {calls}"
    )

    # Correctness: every study still produced its own finding from each
    # check — the shared cache must not leak stale or study-specific data.
    by_check = _findings_by_check(findings)
    unknown_bib = by_check.get("finding_cites_unknown_bib_key", [])
    unknown_expert = by_check.get("finding_references_unknown_expert_doc", [])
    unresolved_viz = by_check.get("visualization_address_unresolved", [])
    assert len(unknown_bib) == n
    assert len(unknown_expert) == n
    assert len(unresolved_viz) == n
    assert {f.study_slug for f in unknown_bib} == set(slugs)
    assert {f.study_slug for f in unknown_expert} == set(slugs)
    assert {f.study_slug for f in unresolved_viz} == set(slugs)
    for f in unknown_bib:
        assert "UnknownKey2099" in f.message
    for f in unknown_expert:
        assert "unknown_doc_not_registered" in f.message
    for f in unresolved_viz:
        assert "MissingViz" in f.message


def test_workspace_scan_cache_is_not_stale_across_separate_lint_runs(tmp_path):
    """The memo cache is scoped to one lint_workspace_report() call (a fresh
    dict is minted every call — see lint_workspace_report()), not a
    module-level cache keyed on ws_root. Prove it can't go stale: lint once
    with no bibliography (every cite is "unknown"), add references/papers.bib
    with the previously-missing key, lint again on the SAME ws_root, and
    confirm the second run reflects the new file instead of a cached miss."""
    ws = tmp_path / "ws"
    sd = ws / "studies" / "s1"
    sd.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("schema_version: 2\nname: ws\npackage_path: pkg\n")
    (sd / "study.yaml").write_text(_yaml.safe_dump({
        "name": "s1",
        "baseline": [{"name": "b1", "composite": "pkg.composites.x"}],
        "findings": [{
            "id": "F-01",
            "kind": "biological",
            "status": "confirms",
            "statement": "Cites a key that doesn't exist yet.",
            "evidence": {"from_test": "t1"},
            "expected": {"cites": ["LaterKey2020"]},
        }],
    }))

    # No papers.bib yet: the check is silent (no bibliography to compare
    # against is a deliberate no-op, per _check_finding_cites_unknown_bib_key).
    before = _findings_by_check(lint_workspace_report(ws))
    assert before.get("finding_cites_unknown_bib_key", []) == []

    # Now add the bibliography, with the cited key present.
    (ws / "references").mkdir()
    (ws / "references" / "papers.bib").write_text(
        "@article{LaterKey2020,\n  title = {x},\n  year = {2020},\n}\n"
    )
    after = _findings_by_check(lint_workspace_report(ws))
    assert after.get("finding_cites_unknown_bib_key", []) == []
    assert after.get("finding_references_unknown_expert_doc", []) == []


# 4b. placeholder markers are standalone tokens, not hyphenated-compound words
def test_placeholder_patterns_ignore_hyphenated_compounds():
    from discovery_engine.report_linter import _PLACEHOLDER_PATTERNS

    def hits(s):
        return [p.pattern for p in _PLACEHOLDER_PATTERNS if p.search(s)]

    # false positives fixed: TODO/TBD used as common nouns inside a compound
    assert hits("a forward build-TODO list with no authored content") == []
    assert hits("the TODO-list is empty") == []
    # real placeholders still fire
    assert hits("TODO: fill this in") and hits("value is TBD") and hits("XXX")
    # bracketed markers unaffected
    assert hits("see [fill in] and <insert>")


# verdict_divergence (H4)

def _verdict_div(tmp_path, spec):
    from discovery_engine.report_linter import _LintContext, _check_verdict_divergence
    ctx = _LintContext(ws_root=tmp_path, slug="s1", spec=spec)
    _check_verdict_divergence(ctx)
    return [f for f in ctx.findings if f.check == "verdict_divergence"]


def test_verdict_divergence_study_level(tmp_path):
    out = _verdict_div(tmp_path, {"pipeline_gate": {"gate_evaluator": {"diverges_from_authored": True}}})
    assert len(out) == 1 and out[0].level == "error"
    assert out[0].field_path == "pipeline_gate.gate_evaluator.diverges_from_authored"


def test_verdict_divergence_per_test(tmp_path):
    spec = {"runs": [{"computed_outcomes": {"t_growth": {"reconcile": "divergent"},
                                            "t_ok": {"reconcile": "agree"}}}]}
    out = _verdict_div(tmp_path, spec)
    assert len(out) == 1 and out[0].level == "error"
    assert "t_growth" in out[0].message and "t_growth" in out[0].field_path


def test_verdict_divergence_clean_and_malformed(tmp_path):
    assert _verdict_div(tmp_path, {"pipeline_gate": {"gate_evaluator": {"diverges_from_authored": False}},
                                   "runs": [{"computed_outcomes": {"t": {"reconcile": "agree"}}}]}) == []
    assert _verdict_div(tmp_path, {"pipeline_gate": "x", "runs": [None, {"computed_outcomes": []}]}) == []
    assert _verdict_div(tmp_path, {}) == []
