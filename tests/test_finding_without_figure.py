"""finding_without_figure: a study that headlines an evidenced, VISUALIZABLE finding (a measured
observable or a graded test) but declares no visualization is flagged — the conclusion is asserted
in prose with nothing a reviewer can SEE. Flags the ABSENCE (distinct from the viz-freshness/address
checks, which validate declared viz). Scoped to visualizable findings so it does not go noisy."""
from __future__ import annotations

from discovery_engine.reporting.linter.checks_findings import _check_finding_without_figure


class _Ctx:
    def __init__(self, spec):
        self.spec = spec
        self.found: list[dict] = []

    def add(self, **kw):
        self.found.append(kw)


def _run(spec):
    c = _Ctx(spec)
    _check_finding_without_figure(c)
    return c.found


def _study(findings, **extra):
    return {"name": "s1", "findings": findings, **extra}


def test_measured_finding_without_viz_is_flagged():
    out = _run(_study([{"id": "f1", "statement": "ppGpp ~51 uM",
                        "evidence": {"measure": "ppGpp", "observed": 51.0}}]))
    assert len(out) == 1
    assert out[0]["check"] == "finding_without_figure" and out[0]["level"] == "warning"
    assert "f1" in out[0]["message"]


def test_graded_test_finding_without_viz_is_flagged():
    assert _run(_study([{"id": "f1", "evidence": {"from_test": "enc_in_band"}}]))


def test_numeric_observed_without_viz_is_flagged():
    assert _run(_study([{"id": "f1", "evidence": {"observed": 1.72}}]))


def test_finding_with_declared_viz_is_not_flagged():
    assert _run(_study([{"id": "f1", "evidence": {"measure": "x", "observed": 1}}],
                       visualizations=[{"address": "chart"}])) == []
    assert _run(_study([{"id": "f1", "evidence": {"measure": "x", "observed": 1}}],
                       embed_visualizations=[{"name": "c"}])) == []


def test_prose_only_finding_is_not_flagged():
    # no measure / from_test / numeric observed -> not visualizable -> not a figure gap
    assert _run(_study([{"id": "f1", "statement": "a qualitative mechanism note",
                         "evidence": {"source": "reasoning"}}])) == []


def test_no_findings_no_flag():
    assert _run(_study([])) == []


def test_one_warning_per_study_listing_all_hits():
    out = _run(_study([{"id": "f1", "evidence": {"observed": 1}},
                       {"id": "f2", "evidence": {"measure": "y"}}]))
    assert len(out) == 1 and "f1" in out[0]["message"] and "f2" in out[0]["message"]
