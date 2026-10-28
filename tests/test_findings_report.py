"""Pass 10B tests for the cross-study findings index (reports/findings.html).

Fixture builds a tiny workspace with 2 studies and 5 findings total —
mixing all three kinds (biological / computational / methodological) and
all four statuses (confirms / partial / contradicts / novel), plus a
few cite + expert_reference combos — and asserts that

  - reports/findings.html is written;
  - all 5 finding ids appear in the rendered HTML;
  - the four status groupings render in the expected order (confirms,
    partial, contradicts, novel) with the right counts;
  - the breadcrumb link back to index.html is present;
  - the workspace dashboard (index.html) gets the "Findings index" link.
  - the empty-state copy appears when no findings exist.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from discovery_engine.report import (
    render_workspace_findings_index,
    render_workspace_report,
    _harvest_findings,
    _first_sentence,
)


def _make_workspace(tmp_path: Path, *, with_findings: bool = True) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(yaml.safe_dump(
        {
            "schema_version": 2,
            "name": "demo-ws",
            "created": "2026-05-17",
            "plugin_version": "0.9.0",
            "package_path": "pkg",
        },
        sort_keys=False,
    ))
    if with_findings:
        _write_study(ws, "dnaa-01", findings=[
            {  # F-01: biological confirms (with cites)
                "id": "F-01",
                "kind": "biological",
                "status": "confirms",
                "statement": (
                    "v2ecoli reproduces the DnaA dimer count within the "
                    "literature band. Numerically the model lands at the "
                    "lower bound but inside tolerance."
                ),
                "evidence": {"from_test": "dnaA-count-in-range"},
                "expected": {
                    "cites": ["Schmidt2016NatBiotechnol", "Sekimizu1991JBacteriol"],
                },
            },
            {  # F-02: biological contradicts (with expert_reference doc)
                "id": "F-02",
                "kind": "biological",
                "status": "contradicts",
                "statement": "v2ecoli underestimates DnaA-ATP fraction at initiation by ~5x.",
                "evidence": {"from_test": "dnaA-atp-fraction"},
                "expert_reference": {"doc": "chromosome_replication_plan"},
            },
            {  # F-03: methodological novel
                "id": "F-03",
                "kind": "methodological",
                "status": "novel",
                "statement": "Single-seed runs hide rare initiation failures; multi-seed sweep recommended.",
            },
        ])
        _write_study(ws, "ribosome-02", findings=[
            {  # F-01: computational partial (no cites)
                "id": "F-01",
                "kind": "computational",
                "status": "partial",
                "statement": "Ribosome listener mis-keys 70S vs 50S+30S; partial coverage of the gate.",
                "evidence": {"from_test": "ribosome-shape"},
            },
            {  # F-02: biological novel
                "id": "F-02",
                "kind": "biological",
                "status": "novel",
                "statement": "Observed bimodal A-site occupancy distribution not in any cited paper.",
                "expected": {"cites": ["Nilsson2017"]},
            },
        ])
    return ws


def _write_study(ws: Path, slug: str, *, findings: list[dict]) -> None:
    sd = ws / "studies" / slug
    sd.mkdir(parents=True)
    spec = {
        "name": slug,
        "baseline": [{"name": "b1", "composite": "pkg.composites.x"}],
        "findings": findings,
    }
    (sd / "study.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_first_sentence_pulls_first_period_terminated_run():
    assert _first_sentence("Short.") == "Short."
    assert _first_sentence("One thing. Then another.") == "One thing."
    assert _first_sentence("With a question? More text.") == "With a question?"


def test_first_sentence_collapses_whitespace_and_truncates():
    long = "a " * 300
    s = _first_sentence(long)
    assert len(s) <= 220
    assert s.endswith("…")


def test_first_sentence_empty_string_returns_empty():
    assert _first_sentence("") == ""
    assert _first_sentence(None) == ""  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Harvest: aggregation across multiple study.yaml files
# ---------------------------------------------------------------------------


def test_harvest_findings_aggregates_across_studies(tmp_path):
    ws = _make_workspace(tmp_path)
    rows = _harvest_findings(ws)
    assert len(rows) == 5
    # Both studies represented
    slugs = {r["study_slug"] for r in rows}
    assert slugs == {"dnaa-01", "ribosome-02"}
    # All four statuses + three kinds present
    assert {r["status"] for r in rows} == {"confirms", "contradicts", "novel", "partial"}
    assert {r["kind"] for r in rows} == {"biological", "computational", "methodological"}


def test_harvest_findings_drops_malformed_entries(tmp_path):
    ws = _make_workspace(tmp_path, with_findings=False)
    _write_study(ws, "bad", findings=[
        {"id": "F-01", "statement": "x"},  # missing kind + status
        {"id": "F-02", "kind": "bogus-kind", "status": "confirms", "statement": "y"},
        {"id": "F-03", "kind": "biological", "status": "bogus-status", "statement": "z"},
        {"id": "F-04", "kind": "biological", "status": "confirms", "statement": "kept"},
    ])
    rows = _harvest_findings(ws)
    assert [r["id"] for r in rows] == ["F-04"]


def test_harvest_findings_handles_missing_studies_dir(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: x\n")
    assert _harvest_findings(ws) == []


def _write_nested_study(ws: Path, inv: str, slug: str, *, findings: list[dict]) -> None:
    """Write a study under the nested investigations/<inv>/studies/<slug>/ layout."""
    sd = ws / "investigations" / inv / "studies" / slug
    sd.mkdir(parents=True)
    spec = {
        "name": slug,
        "investigation": inv,
        "baseline": [{"name": "b1", "composite": "pkg.composites.x"}],
        "findings": findings,
    }
    (sd / "study.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))


def test_harvest_findings_discovers_nested_investigation_layout(tmp_path):
    """P0 regression: studies under investigations/<inv>/studies/<slug>/ must
    be harvested, not just flat studies/<slug>/."""
    ws = _make_workspace(tmp_path, with_findings=False)
    _write_nested_study(ws, "dnaa-replication", "dnaa-01", findings=[
        {
            "id": "F-01",
            "kind": "biological",
            "status": "confirms",
            "statement": "Nested-layout finding is discovered by the harvester.",
            "expected": {"cites": ["Schmidt2016NatBiotechnol"]},
        },
    ])
    rows = _harvest_findings(ws)
    assert [r["id"] for r in rows] == ["F-01"]
    assert rows[0]["study_slug"] == "dnaa-01"
    assert rows[0]["cites"] == ["Schmidt2016NatBiotechnol"]


def test_harvest_findings_mixes_nested_and_flat_layouts(tmp_path):
    """Both nested and flat studies should be harvested in the same workspace."""
    ws = _make_workspace(tmp_path, with_findings=False)
    _write_study(ws, "flat-study", findings=[
        {"id": "F-FLAT", "kind": "computational", "status": "partial",
         "statement": "Flat-layout finding still works."},
    ])
    _write_nested_study(ws, "inv-a", "nested-study", findings=[
        {"id": "F-NEST", "kind": "biological", "status": "novel",
         "statement": "Nested-layout finding works too."},
    ])
    rows = _harvest_findings(ws)
    ids = {r["id"] for r in rows}
    assert ids == {"F-FLAT", "F-NEST"}
    slugs = {r["study_slug"] for r in rows}
    assert slugs == {"flat-study", "nested-study"}


# ---------------------------------------------------------------------------
# Render: HTML output assertions
# ---------------------------------------------------------------------------


def test_render_findings_index_writes_file_and_contains_all_findings(tmp_path):
    ws = _make_workspace(tmp_path)
    out = render_workspace_findings_index(ws, today="2026-05-17")
    assert out == ws / "reports" / "findings.html"
    assert out.exists()
    html = out.read_text()
    # Every finding id appears.
    for s in ("dnaa-01", "ribosome-02"):
        assert s in html
    # Each (study, F-NN) should be in the page; two studies both have F-01/F-02
    # but the slug+id pair is unique. The simplest check: each id appears at
    # least twice (since two studies each have F-01 and F-02), and F-03 once.
    assert html.count(">F-01<") >= 2
    assert html.count(">F-02<") >= 2
    assert ">F-03<" in html


def test_render_findings_index_has_breadcrumb_back_to_dashboard(tmp_path):
    ws = _make_workspace(tmp_path)
    out = render_workspace_findings_index(ws, today="2026-05-17")
    html = out.read_text()
    assert 'href="index.html"' in html
    assert "Workspace dashboard" in html


def test_render_findings_index_groups_by_status_in_canonical_order(tmp_path):
    ws = _make_workspace(tmp_path)
    out = render_workspace_findings_index(ws, today="2026-05-17")
    html = out.read_text()
    # Status group heads exist and appear in canonical order:
    # confirms < partial < contradicts < novel
    idx_confirms = html.find('data-group-status="confirms"')
    idx_partial = html.find('data-group-status="partial"')
    idx_contradicts = html.find('data-group-status="contradicts"')
    idx_novel = html.find('data-group-status="novel"')
    assert -1 < idx_confirms < idx_partial < idx_contradicts < idx_novel


def test_render_findings_index_shows_status_and_kind_badges(tmp_path):
    ws = _make_workspace(tmp_path)
    out = render_workspace_findings_index(ws, today="2026-05-17")
    html = out.read_text()
    for cls in ("status-confirms", "status-contradicts", "status-novel", "status-partial"):
        assert cls in html
    for cls in ("kind-biological", "kind-computational", "kind-methodological"):
        assert cls in html


def test_render_findings_index_renders_bib_cite_chips(tmp_path):
    ws = _make_workspace(tmp_path)
    out = render_workspace_findings_index(ws, today="2026-05-17")
    html = out.read_text()
    assert "Schmidt2016NatBiotechnol" in html
    assert "Nilsson2017" in html


def test_render_findings_index_renders_expert_doc_tag(tmp_path):
    ws = _make_workspace(tmp_path)
    out = render_workspace_findings_index(ws, today="2026-05-17")
    html = out.read_text()
    assert "chromosome_replication_plan" in html


def test_render_findings_index_empty_state(tmp_path):
    ws = _make_workspace(tmp_path, with_findings=False)
    out = render_workspace_findings_index(ws, today="2026-05-17")
    html = out.read_text()
    assert "No findings recorded" in html
    assert "/viva-study findings" in html


# ---------------------------------------------------------------------------
# Workspace dashboard integration: index.html links to findings.html
# ---------------------------------------------------------------------------


def test_render_workspace_report_links_to_findings_index(tmp_path):
    """render_workspace_report must produce findings.html as a side effect
    AND embed the link from index.html (so the user can discover it)."""
    ws = _make_workspace(tmp_path)
    # We need to skip the linter; the minimal workspace.yaml above is enough
    # to render but won't satisfy every Pass B linter rule.
    out = render_workspace_report(ws, today="2026-05-17", lint=False)
    assert out.exists()
    index_html = out.read_text()
    assert "findings.html" in index_html
    assert "Findings index" in index_html
    # Side-effect: findings.html exists too.
    assert (ws / "reports" / "findings.html").is_file()


def test_render_workspace_report_findings_count_badge_reflects_truth(tmp_path):
    ws = _make_workspace(tmp_path)
    render_workspace_report(ws, today="2026-05-17", lint=False)
    index_html = (ws / "reports" / "index.html").read_text()
    assert "5 finding" in index_html


def test_render_workspace_report_empty_workspace_still_writes_findings_html(tmp_path):
    """Even when no study has findings, findings.html must exist so the
    link in the dashboard doesn't 404."""
    ws = _make_workspace(tmp_path, with_findings=False)
    render_workspace_report(ws, today="2026-05-17", lint=False)
    assert (ws / "reports" / "findings.html").is_file()
    html = (ws / "reports" / "findings.html").read_text()
    assert "No findings recorded" in html


# ---------------------------------------------------------------------------
# Planned / targets (design-phase findings)
# ---------------------------------------------------------------------------


def test_harvest_findings_keeps_planned_targets(tmp_path):
    """Design-phase `planned` findings are harvested, not silently dropped."""
    ws = _make_workspace(tmp_path, with_findings=False)
    _write_study(ws, "design-study", findings=[
        {"id": "T-01", "kind": "computational", "status": "planned",
         "statement": "Target: network amplification beats a matched null."},
        {"id": "F-01", "kind": "biological", "status": "confirms",
         "statement": "Realized result."},
    ])
    rows = _harvest_findings(ws)
    assert {r["id"] for r in rows} == {"T-01", "F-01"}
    assert {r["status"] for r in rows} == {"planned", "confirms"}


def test_render_findings_index_planned_in_distinct_group(tmp_path):
    """Planned findings render in their own labelled section, with a planned
    badge, and are NOT mingled into the realized status grouping."""
    ws = _make_workspace(tmp_path, with_findings=False)
    _write_study(ws, "design-study", findings=[
        {"id": "T-01", "kind": "computational", "status": "planned",
         "statement": "Target stated before the study runs."},
        {"id": "F-99", "kind": "biological", "status": "confirms",
         "statement": "Realized result."},
    ])
    out = render_workspace_findings_index(ws, today="2026-05-17")
    html = out.read_text()
    # Distinct, clearly-labelled planned section.
    assert "Planned / targets" in html
    assert "not results" in html  # the "these are not results" label
    assert "status-planned" in html
    assert ">T-01<" in html
    # No realized status group is created for planned (not mingled).
    assert 'data-group-status="planned"' not in html
    # Realized finding still grouped by status as before.
    assert 'data-group-status="confirms"' in html
    assert ">F-99<" in html
    # The planned row sits outside the filterable realized views.
    assert 'id="realized-views"' in html
    assert 'class="panel planned-panel"' in html


def test_render_findings_index_planned_only_workspace(tmp_path):
    """A workspace whose only findings are planned shows the planned section
    and neither the realized filter bar nor the empty-state."""
    ws = _make_workspace(tmp_path, with_findings=False)
    _write_study(ws, "all-design", findings=[
        {"id": "T-01", "kind": "computational", "status": "planned",
         "statement": "Only a target so far."},
    ])
    out = render_workspace_findings_index(ws, today="2026-05-17")
    html = out.read_text()
    assert "Planned / targets" in html
    assert ">T-01<" in html
    assert "No findings recorded" not in html
    assert 'id="filter-bar"' not in html
