"""Tests for the ``baseline_config_carry`` reproducibility lint check.

The workbench viewer/Run reconstructs a study from the EFFECTIVE baseline
run-config: composite defaults ⊕ ``conditions.baseline.config_file`` ⊕ inline
``conditions.baseline.params`` — plus the SEPARATE ``runtime:`` surface
(emitter, generation count). A baseline that resolves to nothing beyond
composite defaults / a bare ``{seed, cache_dir}`` renders as composite defaults
and a Run won't reproduce it.

One tripping spec + several passing specs, proving the check is sensitive
(fires on the bare baseline) and specific (silent when the run-config is
carried by a resolvable config_file, rich inline params, runtime, or the v3
baseline[] shape). Uses the direct ``_LintContext`` call style of
``test_report_linter_review_checks.py`` (``WorkspacePaths.load`` tolerates a
missing workspace.yaml). Synthetic fixtures only.
"""
from __future__ import annotations

from pathlib import Path

from discovery_engine.report_linter import (
    CHECKS,
    _CHECK_FUNCTIONS,
    _LintContext,
    _check_baseline_config_carry,
)


def _run(ws_root: Path, spec: dict, slug: str = "s1"):
    ctx = _LintContext(ws_root=ws_root, slug=slug, spec=spec)
    _check_baseline_config_carry(ctx)
    return ctx.findings


def _by_check(findings, name="baseline_config_carry"):
    return [f for f in findings if f.check == name]


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def test_baseline_config_carry_registered():
    assert "baseline_config_carry" in CHECKS
    assert _check_baseline_config_carry in _CHECK_FUNCTIONS


# ---------------------------------------------------------------------------
# Sensitive — bare baseline is flagged
# ---------------------------------------------------------------------------


def test_bare_seed_cache_dir_params_is_flagged(tmp_path):
    # Only reproducibility plumbing, no scientific config, no config_file.
    spec = {
        "phase": "Build",
        "conditions": {"baseline": {
            "composite": "some_composite",
            "params": {"seed": 0, "cache_dir": "/tmp/cache"},
        }},
    }
    found = _by_check(_run(tmp_path, spec))
    assert len(found) == 1
    f = found[0]
    assert f.level == "warning"
    assert f.field_path == "conditions.baseline"
    assert "reproducible spec" in f.message
    assert "config_file" in f.message


def test_empty_params_no_config_file_is_flagged(tmp_path):
    spec = {
        "phase": "Build",
        "conditions": {"baseline": {"composite": "c", "params": {}}},
    }
    assert len(_by_check(_run(tmp_path, spec))) == 1


def test_unresolvable_config_file_is_flagged(tmp_path):
    # config_file points nowhere resolvable → still bare.
    spec = {
        "phase": "Build",
        "conditions": {"baseline": {
            "composite": "c",
            "params": {"seed": 1},
            "config_file": "configs/does_not_exist.json",
        }},
    }
    assert len(_by_check(_run(tmp_path, spec))) == 1


def test_bare_baseline_in_design_phase_is_info_not_warning(tmp_path):
    # Phase-gated like the neighbouring completeness checks: Design-stage
    # sparseness is a nudge, not a warning.
    spec = {
        "phase": "Design",
        "conditions": {"baseline": {"composite": "c", "params": {"seed": 0}}},
    }
    found = _by_check(_run(tmp_path, spec))
    assert len(found) == 1
    assert found[0].level == "info"


# ---------------------------------------------------------------------------
# Specific — carrying a real run-config passes
# ---------------------------------------------------------------------------


def test_rich_inline_params_passes(tmp_path):
    spec = {
        "phase": "Build",
        "conditions": {"baseline": {
            "composite": "c",
            "params": {"seed": 0, "cache_dir": "/tmp/c",
                       "geometry": {"kla_correlation": "wells-riley"}},
        }},
    }
    assert _by_check(_run(tmp_path, spec)) == []


def test_resolvable_config_file_study_dir_relative_passes(tmp_path):
    # study-dir-relative config_file that actually exists on disk.
    study_dir = tmp_path / "studies" / "s1"
    (study_dir / "configs").mkdir(parents=True)
    (study_dir / "configs" / "run.json").write_text("{}", encoding="utf-8")
    spec = {
        "phase": "Build",
        "conditions": {"baseline": {
            "composite": "c",
            "params": {"seed": 0},
            "config_file": "configs/run.json",
        }},
    }
    assert _by_check(_run(tmp_path, spec)) == []


def test_resolvable_config_file_workspace_root_relative_passes(tmp_path):
    (tmp_path / "shared").mkdir()
    (tmp_path / "shared" / "run.json").write_text("{}", encoding="utf-8")
    spec = {
        "phase": "Build",
        "conditions": {"baseline": {
            "composite": "c",
            "params": {"seed": 0},
            "config_file": "shared/run.json",
        }},
    }
    assert _by_check(_run(tmp_path, spec)) == []


def test_resolvable_config_file_absolute_passes(tmp_path):
    cfg = tmp_path / "abs_run.json"
    cfg.write_text("{}", encoding="utf-8")
    spec = {
        "phase": "Build",
        "conditions": {"baseline": {
            "composite": "c",
            "params": {},
            "config_file": str(cfg),
        }},
    }
    assert _by_check(_run(tmp_path, spec)) == []


def test_runtime_max_generations_passes(tmp_path):
    # The separate runtime: surface carries the run-config → not falsely bare.
    spec = {
        "phase": "Build",
        "runtime": {"max_generations": 8},
        "conditions": {"baseline": {"composite": "c", "params": {"seed": 0}}},
    }
    assert _by_check(_run(tmp_path, spec)) == []


def test_runtime_emitter_passes(tmp_path):
    spec = {
        "phase": "Build",
        "runtime": {"default_emitter": "xarray"},
        "conditions": {"baseline": {"composite": "c", "params": {}}},
    }
    assert _by_check(_run(tmp_path, spec)) == []


def test_v3_baseline_list_rich_params_passes(tmp_path):
    spec = {
        "phase": "Build",
        "baseline": [{"name": "b", "composite": "c",
                      "params": {"interval_s": 10}}],
    }
    assert _by_check(_run(tmp_path, spec)) == []


def test_v3_baseline_list_bare_params_is_flagged(tmp_path):
    spec = {
        "phase": "Build",
        "baseline": [{"name": "b", "composite": "c",
                      "params": {"seed": 0, "cache_dir": "/tmp/c"}}],
    }
    assert len(_by_check(_run(tmp_path, spec))) == 1


# ---------------------------------------------------------------------------
# Silent when there is no baseline (missing_baseline owns that gap)
# ---------------------------------------------------------------------------


def test_no_baseline_is_silent(tmp_path):
    assert _by_check(_run(tmp_path, {"phase": "Build"})) == []


def test_workspace_pseudo_study_is_silent(tmp_path):
    spec = {"conditions": {"baseline": {"composite": "c", "params": {}}}}
    assert _by_check(_run(tmp_path, spec, slug="<workspace>")) == []
