"""Tests for the ``missing_variants`` completeness check's multi-baseline path.

A study that encodes its experimental variation as ≥2 baseline arms (a
multi-baseline comparison — composite-A vs -B vs -control) IS doing experimental
variation; it just carries it in ``baseline[]`` / ``conditions.baselines`` rather
than ``variants[]``. The check must not flag it as "design incomplete".

Uses the direct ``_LintContext`` call style of the neighbouring linter tests.
"""
from pathlib import Path

from discovery_engine.reporting.linter import (
    CHECKS,
    _check_missing_variants,
    _LintContext,
)


def _run(ws_root: Path, spec: dict, slug: str = "s1"):
    ctx = _LintContext(ws_root=ws_root, slug=slug, spec=spec)
    _check_missing_variants(ctx)
    return [f for f in ctx.findings if f.check == "missing_variants"]


def test_missing_variants_registered():
    assert "missing_variants" in CHECKS


def test_no_variants_no_baselines_is_flagged(tmp_path):
    # The original behaviour: an empty study with neither variants nor a
    # comparison design still reads as "design incomplete".
    spec = {"phase": "Build", "variants": []}
    assert len(_run(tmp_path, spec)) == 1


def test_single_baseline_no_variants_is_flagged(tmp_path):
    # One baseline arm is not experimental variation.
    spec = {
        "phase": "Build",
        "variants": [],
        "baseline": [{"name": "b", "composite": "c"}],
    }
    assert len(_run(tmp_path, spec)) == 1


def test_two_v3_baseline_arms_is_silent(tmp_path):
    # ≥2 baseline[] entries = a multi-baseline comparison = experimental variation.
    spec = {
        "phase": "Build",
        "variants": [],
        "baseline": [
            {"name": "protocell-autopoietic", "composite": "a"},
            {"name": "protocell-vesicle-control", "composite": "b"},
        ],
    }
    assert _run(tmp_path, spec) == []


def test_four_v3_baseline_arms_is_silent(tmp_path):
    spec = {
        "phase": "Build",
        "baseline": [
            {"name": "autopoietic", "composite": "a"},
            {"name": "vesicle-control", "composite": "b"},
            {"name": "v2", "composite": "c"},
            {"name": "v2-open", "composite": "d"},
        ],
    }
    assert _run(tmp_path, spec) == []


def test_two_v4_baselines_arms_is_silent(tmp_path):
    # v4 plural conditions.baselines list, ≥2 arms.
    spec = {
        "phase": "Build",
        "conditions": {"baselines": [
            {"name": "arm-a", "composite": "a"},
            {"name": "arm-b", "composite": "b"},
        ]},
    }
    assert _run(tmp_path, spec) == []


def test_variants_present_still_silent(tmp_path):
    # Unchanged: a declared variant satisfies the check regardless of baselines.
    spec = {"phase": "Build", "variants": [{"name": "sweep-1"}]}
    assert _run(tmp_path, spec) == []
