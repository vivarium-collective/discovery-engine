"""Reorg #45 moved post_sim / card_criteria / card_grade into subpackages without
a shim, breaking external consumers (v2ecoli's WCM workflow) that import the old
flat paths. These back-compat shims restore the old import surface.
"""
import importlib
import warnings

import pytest

# The exact surfaces v2ecoli imports from each old flat path (incl. private names).
SURFACES = {
    ("discovery_engine.post_sim", "discovery_engine.integration.post_sim"): [
        "ANALYSIS_REGISTRY", "ANALYSIS_SCALES", "Analysis", "AnalysisStep", "KINDS", "POST_SIM_REGISTRY",
        "REPORT_CARD_REGISTRY", "VISUALIZATION_REGISTRY", "ReportCardStep",
        "VisualizationStep", "iter_post_sim", "register_post_sim", "StudyContext",
        "prune", "write_card",
    ],
    ("discovery_engine.card_criteria", "discovery_engine.reporting.card_criteria"): [
        "VERDICTS", "_SEVERITY", "_band", "_fit_threshold_linear", "_r2", "_ungraded", "_welch", "grade_axis",
    ],
    ("discovery_engine.card_grade", "discovery_engine.reporting.card_grade"): [
        "dig", "grade_card", "verdict_json", "render_verdict_html", "_slug_group",
    ],
}


@pytest.mark.parametrize("old,new", [k for k in SURFACES])
def test_flat_path_re_exports_every_name_identically(old, new):
    shim = importlib.import_module(old)
    canonical = importlib.import_module(new)
    for name in SURFACES[(old, new)]:
        assert hasattr(shim, name), f"{old} is missing {name}"
        assert getattr(shim, name) is getattr(canonical, name), f"{old}.{name} is not the moved object"


@pytest.mark.parametrize("old,new", [k for k in SURFACES])
def test_flat_path_warns_deprecated(old, new):
    # a fresh import of the shim module emits exactly one DeprecationWarning
    import sys
    sys.modules.pop(old, None)
    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        importlib.import_module(old)
    assert any(issubclass(x.category, DeprecationWarning) and new in str(x.message) for x in w)
