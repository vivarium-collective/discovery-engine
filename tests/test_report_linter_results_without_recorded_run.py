"""A study that presents committed simulation figures/data must be backed by a
recorded run, or the linter errors (blocking, override-able).

Committed chart artifacts (``studies/<slug>/charts/*.png`` or a non-``.meta.json``
``*.json``, or the same under ``viz/``) mean a simulation actually produced them.
If the study has no recorded run — ``runs: []`` and an empty/absent
``studies/<slug>/runs.db`` — the displayed results trace to no tracked,
reproducible run, so the gate fires an error. A non-empty ``runs:`` list, or a
``runs.db`` with a ``simulations`` row, satisfies it.

Uses the direct ``_LintContext`` call style of the viz-gate tests —
``WorkspacePaths.load`` tolerates a missing workspace.yaml and falls back to flat
``studies/<slug>/`` under the tmp root.
"""
from __future__ import annotations

import sqlite3

from discovery_engine.reporting.linter import (
    _check_results_without_recorded_run,
    _LintContext,
)

CHECK = "results_without_recorded_run"


def _findings(ws_root, slug, spec):
    ctx = _LintContext(ws_root=ws_root, slug=slug, spec=spec)
    _check_results_without_recorded_run(ctx)
    return [f for f in ctx.findings if f.check == CHECK]


def _commit_chart(ws_root, slug, name="x.png"):
    d = ws_root / "studies" / slug / "charts"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_bytes(b"\x89PNG committed-figure")
    return d / name


def _make_runs_db_with_sim(ws_root, slug):
    d = ws_root / "studies" / slug
    d.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(d / "runs.db"))
    conn.execute("CREATE TABLE simulations (simulation_id TEXT)")
    conn.execute("INSERT INTO simulations (simulation_id) VALUES ('sim-1')")
    conn.commit()
    conn.close()


# (a) committed figure + no recorded run -> ERROR

def test_committed_png_without_recorded_run_is_a_blocking_error(tmp_path):
    _commit_chart(tmp_path, "s1", "x.png")
    found = _findings(tmp_path, "s1", {"status": "completed", "runs": []})
    assert len(found) == 1
    f = found[0]
    assert f.level == "error"
    assert f.field_path == "runs"
    assert "recorded run" in f.message.lower()


def test_committed_data_json_without_recorded_run_errors(tmp_path):
    _commit_chart(tmp_path, "s1", "series.json")
    found = _findings(tmp_path, "s1", {"status": "completed"})
    assert len(found) == 1
    assert found[0].level == "error"


# (b) committed figure + a recorded run -> silent

def test_committed_figure_with_runs_list_is_silent(tmp_path):
    _commit_chart(tmp_path, "s1", "x.png")
    spec = {"status": "completed", "runs": [{"run_id": "r1"}]}
    assert _findings(tmp_path, "s1", spec) == []


def test_committed_figure_with_runs_db_simulation_row_is_silent(tmp_path):
    _commit_chart(tmp_path, "s1", "x.png")
    _make_runs_db_with_sim(tmp_path, "s1")
    assert _findings(tmp_path, "s1", {"status": "completed", "runs": []}) == []


# Negative / boundary cases

def test_no_committed_results_is_silent(tmp_path):
    # No charts/ or viz/ artifacts on disk -> nothing to back, no finding.
    assert _findings(tmp_path, "s1", {"status": "completed", "runs": []}) == []


def test_meta_json_alone_does_NOT_count_as_committed_results(tmp_path):
    # A `.meta.json` sidecar is not chart output -> does not trip the gate.
    d = tmp_path / "studies" / "s1" / "charts"
    d.mkdir(parents=True)
    (d / "x.meta.json").write_text("{}", encoding="utf-8")
    assert _findings(tmp_path, "s1", {"runs": []}) == []


def test_declared_mockup_viz_without_committed_file_does_NOT_trip(tmp_path):
    # A declared-but-planned visualization with no committed file on disk must
    # NOT count as committed results (the gate keys on files, not the spec).
    spec = {
        "status": "in-progress",
        "runs": [],
        "visualizations": [{"name": "mock", "address": "local:MockChart"}],
    }
    assert _findings(tmp_path, "s1", spec) == []


def test_empty_runs_db_without_simulation_rows_still_errors(tmp_path):
    # runs.db exists but has no simulations rows -> not a recorded run.
    _commit_chart(tmp_path, "s1", "x.png")
    d = tmp_path / "studies" / "s1"
    conn = sqlite3.connect(str(d / "runs.db"))
    conn.execute("CREATE TABLE simulations (simulation_id TEXT)")
    conn.commit()
    conn.close()
    found = _findings(tmp_path, "s1", {"runs": []})
    assert len(found) == 1
    assert found[0].level == "error"


def test_workspace_pseudo_slug_is_skipped(tmp_path):
    _commit_chart(tmp_path, "<workspace>", "x.png")
    assert _findings(tmp_path, "<workspace>", {"status": "completed"}) == []


# ---------------------------------------------------------------------------
# NESTED-layout end-to-end: on a real nested workspace, studies live under
# investigations/<inv>/studies/<slug>/, NOT the flat studies/<slug>/. These
# exercise the full lint_workspace_report path (which must enumerate nested
# studies) + the nested-aware study_dir() probe in both helpers.
# ---------------------------------------------------------------------------

def _nested_study_dir(ws_root, inv, slug):
    d = ws_root / "investigations" / inv / "studies" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "study.yaml").write_text(f"name: {slug}\nruns: []\n", encoding="utf-8")
    return d


def _lint_check_findings(ws_root, slug):
    from discovery_engine.reporting.linter import lint_workspace_report
    return [
        f for f in lint_workspace_report(ws_root)
        if f.check == CHECK and f.study_slug == slug
    ]


def test_nested_committed_figure_without_run_errors_end_to_end(tmp_path):
    sd = _nested_study_dir(tmp_path, "inv1", "s1")
    (sd / "charts").mkdir()
    (sd / "charts" / "x.png").write_bytes(b"\x89PNG committed-figure")
    found = _lint_check_findings(tmp_path, "s1")
    assert len(found) == 1
    assert found[0].level == "error"


def test_nested_committed_figure_with_runs_db_is_silent_end_to_end(tmp_path):
    sd = _nested_study_dir(tmp_path, "inv1", "s1")
    (sd / "charts").mkdir()
    (sd / "charts" / "x.png").write_bytes(b"\x89PNG committed-figure")
    conn = sqlite3.connect(str(sd / "runs.db"))
    conn.execute("CREATE TABLE simulations (simulation_id TEXT)")
    conn.execute("INSERT INTO simulations (simulation_id) VALUES ('sim-1')")
    conn.commit()
    conn.close()
    assert _lint_check_findings(tmp_path, "s1") == []
