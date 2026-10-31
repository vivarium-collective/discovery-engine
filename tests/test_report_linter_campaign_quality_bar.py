"""Campaign quality-bar + enrichment-plan design-principle lint checks.

One tripping spec + one passing spec per check, proving each is sensitive
(fires on the violation) and specific (silent on the fix):

- ``finding_missing_cites`` — a quantitative finding with no provenance.
- ``decision_viz_single_run`` — a Decision view that is a single run, not a
  phase map.
- ``authored_confidence_diverges`` — authored ``confidence:`` disagrees with
  ``study_verdict.derive_confidence``.
- ``investigation_missing_adversarial`` — an investigation with no adversarial
  / ablation member study.

Uses the direct ``_LintContext`` call style of
test_report_linter_review_checks.py (``WorkspacePaths.load`` tolerates a
missing workspace.yaml), plus a tmp-dir workspace for the investigation-level
check which reads member study specs off disk.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from discovery_engine.report_linter import (
    _LintContext,
    _check_authored_confidence_diverges,
    _check_decision_viz_single_run,
    _check_finding_missing_cites,
    _check_investigation_missing_adversarial,
    lint_workspace_report,
)
from discovery_engine import study_verdict


def _run(check_fn, spec: dict, ws_root: Path | None = None, slug: str = "s1"):
    ctx = _LintContext(ws_root=ws_root or Path("."), slug=slug, spec=spec)
    check_fn(ctx)
    return ctx.findings


def _by_check(findings, name):
    return [f for f in findings if f.check == name]


# --- finding_missing_cites -------------------------------------------------

def test_finding_missing_cites_fires_on_uncited_quantitative_finding():
    spec = {
        "findings": [
            {"id": "f1", "statement": "H2 flux peaks at 3.2 mmol/L",
             "expected": {"range": [2.0, 4.0]}},
        ],
    }
    findings = _run(_check_finding_missing_cites, spec)
    assert len(_by_check(findings, "finding_missing_cites")) == 1


def test_finding_missing_cites_silent_when_cited():
    spec = {
        "findings": [
            {"id": "f1", "statement": "H2 flux peaks at 3.2 mmol/L",
             "expected": {"range": [2.0, 4.0], "cites": ["sojo2016"]}},
        ],
    }
    assert _by_check(_run(_check_finding_missing_cites, spec),
                     "finding_missing_cites") == []


def test_finding_missing_cites_silent_on_qualitative_finding():
    spec = {
        "findings": [
            {"id": "f1", "statement": "the pore concentrates solutes",
             "tier": "observation"},
        ],
    }
    assert _by_check(_run(_check_finding_missing_cites, spec),
                     "finding_missing_cites") == []


def test_finding_missing_cites_accepts_provenance_or_expert_ref():
    spec = {
        "findings": [
            {"id": "f1", "statement": "observed value", "divergence_factor": 1.4,
             "provenance": "theory (deliberate modeling choice, not fit)"},
            {"id": "f2", "statement": "another", "expected": {"value": 7},
             "expert_reference": {"doc": "enrichment-plan"}},
        ],
    }
    assert _by_check(_run(_check_finding_missing_cites, spec),
                     "finding_missing_cites") == []


# --- decision_viz_single_run -----------------------------------------------

def test_decision_viz_single_run_fires_when_decision_view_is_not_a_phase_map():
    spec = {
        "visualizations": [
            {"name": "world_view", "view": "world"},
            {"name": "decision_view", "view": "decision", "chart": "charts/run.svg"},
        ],
    }
    findings = _run(_check_decision_viz_single_run, spec)
    assert len(_by_check(findings, "decision_viz_single_run")) == 1


def test_decision_viz_single_run_silent_when_phase_map():
    spec = {
        "visualizations": [
            {"name": "decision_view", "view": "decision", "phase_map": True,
             "grid_artifact": "studies/s1/grid/phase_map.parquet"},
        ],
    }
    assert _by_check(_run(_check_decision_viz_single_run, spec),
                     "decision_viz_single_run") == []


def test_decision_viz_single_run_silent_when_no_decision_view_declared():
    spec = {
        "visualizations": [
            {"name": "trajectory", "view": "mechanism"},
        ],
    }
    assert _by_check(_run(_check_decision_viz_single_run, spec),
                     "decision_viz_single_run") == []


def test_decision_viz_single_run_accepts_kind_heatmap():
    spec = {
        "visualizations": [
            {"name": "phase", "role": "decision", "kind": "heatmap"},
        ],
    }
    assert _by_check(_run(_check_decision_viz_single_run, spec),
                     "decision_viz_single_run") == []


# --- authored_confidence_diverges ------------------------------------------

def test_authored_confidence_diverges_fires_on_accepted_over_failed():
    # derive_confidence maps an authored failed verdict -> Refuted; an author
    # pinning Accepted contradicts it.
    spec = {"confidence": "Accepted", "report": {"verdict": "failed"}}
    assert study_verdict.derive_confidence(spec) == "Refuted"
    findings = _run(_check_authored_confidence_diverges, spec)
    assert len(_by_check(findings, "authored_confidence_diverges")) == 1


def test_authored_confidence_diverges_silent_when_matching():
    spec = {"confidence": "Refuted", "report": {"verdict": "failed"}}
    assert _by_check(_run(_check_authored_confidence_diverges, spec),
                     "authored_confidence_diverges") == []


def test_authored_confidence_diverges_silent_when_unset():
    spec = {"report": {"verdict": "failed"}}
    assert _by_check(_run(_check_authored_confidence_diverges, spec),
                     "authored_confidence_diverges") == []


def test_confidence_diverges_helper_is_pure():
    assert study_verdict.confidence_diverges_from_derived(
        {"confidence": "Accepted", "report": {"verdict": "failed"}}) is True
    assert study_verdict.confidence_diverges_from_derived(
        {"confidence": "Refuted", "report": {"verdict": "failed"}}) is False
    assert study_verdict.confidence_diverges_from_derived({}) is False


# --- investigation_missing_adversarial -------------------------------------

def _write_study(ws: Path, slug: str, spec: dict) -> None:
    d = ws / "studies" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "study.yaml").write_text(yaml.safe_dump({"name": slug, **spec}))


def test_investigation_missing_adversarial_fires_when_none(tmp_path):
    ws = tmp_path
    _write_study(ws, "og-00", {})
    _write_study(ws, "og-01", {})
    inv_spec = {"name": "origins", "studies": ["og-00", "og-01"]}
    findings = _run(_check_investigation_missing_adversarial, inv_spec,
                    ws_root=ws, slug="origins")
    assert len(_by_check(findings, "investigation_missing_adversarial")) == 1


def test_investigation_missing_adversarial_silent_with_adversarial_member(tmp_path):
    ws = tmp_path
    _write_study(ws, "og-00", {})
    _write_study(ws, "og-adv", {"kind": "adversarial"})
    inv_spec = {"name": "origins", "studies": ["og-00", "og-adv"]}
    assert _by_check(
        _run(_check_investigation_missing_adversarial, inv_spec, ws_root=ws,
             slug="origins"),
        "investigation_missing_adversarial") == []


def test_investigation_missing_adversarial_silent_with_ablation_block(tmp_path):
    ws = tmp_path
    _write_study(ws, "og-00", {})
    _write_study(ws, "og-abl", {"ablation": {"removes": "mineral_catalysis"}})
    inv_spec = {"name": "origins", "studies": ["og-00", "og-abl"]}
    assert _by_check(
        _run(_check_investigation_missing_adversarial, inv_spec, ws_root=ws,
             slug="origins"),
        "investigation_missing_adversarial") == []


def test_investigation_missing_adversarial_respects_opt_out(tmp_path):
    ws = tmp_path
    _write_study(ws, "og-00", {})
    inv_spec = {"name": "origins", "studies": ["og-00"],
                "adversarial_coverage_skip": True,
                "adversarial_coverage_skip_reason": "single-arm screen"}
    assert _by_check(
        _run(_check_investigation_missing_adversarial, inv_spec, ws_root=ws,
             slug="origins"),
        "investigation_missing_adversarial") == []


def test_investigation_check_runs_via_workspace_report(tmp_path):
    """End-to-end: the investigation check fires through lint_workspace_report."""
    ws = tmp_path
    (ws / "workspace.yaml").write_text("name: origins\n")
    _write_study(ws, "og-00", {})
    inv_dir = ws / "investigations" / "origins"
    inv_dir.mkdir(parents=True, exist_ok=True)
    (inv_dir / "investigation.yaml").write_text(yaml.safe_dump({
        "name": "origins", "studies": ["og-00"],
        # satisfy the narrative-spine check so only our check fires
        "narrative_spine_skip": ["executive", "scientific_argument",
                                 "biological_story"],
    }))
    findings = lint_workspace_report(ws, rulesets=["campaign"])
    assert _by_check(findings, "investigation_missing_adversarial")


def test_campaign_rules_are_opt_in_not_default(tmp_path):
    from discovery_engine import report_linter as rl
    campaign = set(rl._RULESETS["campaign"][0]) | set(rl._RULESETS["campaign"][1])
    assert campaign
    assert not campaign & (set(rl._CHECK_FUNCTIONS) | set(rl._INVESTIGATION_CHECK_FUNCTIONS))
    assert "campaign" in rl.available_rulesets()


def test_workspace_yaml_enables_ruleset(tmp_path):
    from discovery_engine import report_linter as rl
    (tmp_path / "workspace.yaml").write_text("lint:\n  rulesets: [campaign]\n")
    assert rl._workspace_rulesets(tmp_path) == ["campaign"]
    assert rl._workspace_rulesets(tmp_path / "nope") == []
