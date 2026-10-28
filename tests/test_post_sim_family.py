"""Tests for the shared post-sim Step family (discovery_engine.post_sim):
registry auto-registration + funnel, write_card sanitization, and the
report-card gating verdict convention.
"""
import json

import pytest
import yaml

from discovery_engine.post_sim import (
    ANALYSIS_REGISTRY,
    KINDS,
    POST_SIM_REGISTRY,
    REPORT_CARD_REGISTRY,
    VISUALIZATION_REGISTRY,
    Analysis,
    AnalysisStep,
    ReportCardStep,
    StudyContext,
    VisualizationStep,
    applicable,
    iter_post_sim,
    prune,
    write_card,
)


@pytest.fixture
def core():
    """A bigraph-schema core for Step construction."""
    from bigraph_schema import allocate_core
    return allocate_core()


def _ctx(tmp_path, spec=None):
    sd = tmp_path / "workspace" / "studies" / "demo"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "study.yaml").write_text(yaml.safe_dump(spec or {"name": "demo"}))
    return StudyContext.load(tmp_path, "demo")


# ---------------------------------------------------------------------------
# (a) auto-registration into kind-specific registry + POST_SIM_REGISTRY;
#     abstract bases (no ``name``) register nowhere.
# ---------------------------------------------------------------------------

def test_analysis_step_subclass_auto_registers():
    class _ProbeAnalysisStep(AnalysisStep):
        name = "probe_analysis_step_demo"
        scale = "single"

        def analyze(self, rows):
            return {"n": len(rows)}

    assert ANALYSIS_REGISTRY["probe_analysis_step_demo"] is _ProbeAnalysisStep
    assert POST_SIM_REGISTRY["probe_analysis_step_demo"] == {
        "cls": _ProbeAnalysisStep, "kind": "analysis"}
    assert "probe_analysis_step_demo" in [n for n, _ in iter_post_sim("analysis")]


def test_analysis_live_conn_subclass_auto_registers():
    class _ProbeAnalysis(Analysis):
        name = "probe_analysis_demo"
        scale = "single"

        def analyze(self, **kw):
            return {"view": "<i></i>", "data": {}}

    assert ANALYSIS_REGISTRY["probe_analysis_demo"] is _ProbeAnalysis
    assert POST_SIM_REGISTRY["probe_analysis_demo"]["kind"] == "analysis"


def test_analysis_live_conn_surface_not_dropped(core):
    """Regression test: Analysis.update() must read conn/history_sql/sim_data/
    etc. straight off `state` (the surface v2ecoli's analysis_runner.py drives
    ~30 concrete subclasses through) and pass them to analyze() unmodified —
    NOT read a ResultsHandle from state["results"]. A prior collapse of
    Analysis into a thin AnalysisStep alias silently dropped these kwargs
    (analyze() saw None for everything), breaking every v2ecoli subclass."""
    received = {}

    class _ProbeLiveConnAnalysis(Analysis):
        name = "probe_live_conn_analysis_demo"
        scale = "single"

        def analyze(self, *, conn, history_sql, sim_data, **ctx):
            received["conn"] = conn
            received["history_sql"] = history_sql
            received["sim_data"] = sim_data
            received["config_sql"] = ctx.get("config_sql")
            received["success_sql"] = ctx.get("success_sql")
            received["validation_data"] = ctx.get("validation_data")
            received["variant_metadata"] = ctx.get("variant_metadata")
            return {"view": "<i>ok</i>", "data": {"n": 1}}

    conn_sentinel = object()
    sim_data_sentinel = object()
    validation_data_sentinel = object()
    variant_metadata_sentinel = object()

    step = _ProbeLiveConnAnalysis({}, core=core)
    out = step.update({
        "conn": conn_sentinel,
        "history_sql": "SELECT 1",
        "config_sql": "SELECT 2",
        "success_sql": "SELECT 3",
        "sim_data": sim_data_sentinel,
        "validation_data": validation_data_sentinel,
        "variant_metadata": variant_metadata_sentinel,
        # A ResultsHandle-shaped "results" key must NOT be consulted by
        # Analysis.update() — it isn't in Analysis.inputs(), so it must be
        # ignored entirely (proves Analysis and AnalysisStep stayed distinct).
        "results": object(),
    })

    assert received["conn"] is conn_sentinel
    assert received["history_sql"] == "SELECT 1"
    assert received["config_sql"] == "SELECT 2"
    assert received["success_sql"] == "SELECT 3"
    assert received["sim_data"] is sim_data_sentinel
    assert received["validation_data"] is validation_data_sentinel
    assert received["variant_metadata"] is variant_metadata_sentinel
    assert out == {"view": "<i>ok</i>", "data": {"n": 1}}


def test_visualization_step_subclass_auto_registers():
    class _ProbeViz(VisualizationStep):
        name = "probe_viz_demo"

        def render(self, study):
            return "<b></b>", {}

    assert VISUALIZATION_REGISTRY["probe_viz_demo"] is _ProbeViz
    assert POST_SIM_REGISTRY["probe_viz_demo"] == {
        "cls": _ProbeViz, "kind": "visualization"}
    assert "probe_viz_demo" in [n for n, _ in iter_post_sim("visualization")]


def test_report_card_step_subclass_auto_registers():
    class _ProbeCard(ReportCardStep):
        name = "probe_card_demo"

        def build(self, study):
            return {"status": "pass", "checks": [], "summary": "ok"}, "<div></div>"

    assert REPORT_CARD_REGISTRY["probe_card_demo"] is _ProbeCard
    assert POST_SIM_REGISTRY["probe_card_demo"] == {
        "cls": _ProbeCard, "kind": "test"}
    assert "probe_card_demo" in [n for n, _ in iter_post_sim("report_card")]


def test_abstract_bases_without_name_do_not_register():
    before = dict(POST_SIM_REGISTRY)

    class _AbstractAnalysisStep(AnalysisStep):
        scale = "single"

        def analyze(self, rows):
            return {}

    class _AbstractViz(VisualizationStep):
        def render(self, study):
            return None

    class _AbstractCard(ReportCardStep):
        def build(self, study):
            return None

    assert POST_SIM_REGISTRY == before
    assert "" not in ANALYSIS_REGISTRY
    assert "" not in VISUALIZATION_REGISTRY
    assert "" not in REPORT_CARD_REGISTRY


def test_kinds_constant():
    assert KINDS == ("analysis", "visualization", "test")


# ---------------------------------------------------------------------------
# (b) write_card writes <name>.html + <name>.verdict.json, sanitized,
#     allow_nan=False.
# ---------------------------------------------------------------------------

def test_write_card_writes_both_files_and_sanitizes(tmp_path):
    ctx = _ctx(tmp_path)
    p = write_card(ctx, "tests", {"overall": "drift", "x": float("inf")}, "<i>hi</i>")
    assert p.name == "tests.html"
    assert p.read_text() == "<i>hi</i>"
    vj_path = ctx.card_dir / "tests.verdict.json"
    vj = json.loads(vj_path.read_text())
    assert vj["overall"] == "drift"
    assert vj["x"] is None  # inf -> null (bundle-safe)
    # allow_nan=False: raw file text must not contain a literal Infinity token
    assert "Infinity" not in vj_path.read_text()


def test_prune_removes_stale_only(tmp_path):
    ctx = _ctx(tmp_path)
    write_card(ctx, "keep", {"overall": "within_tol"}, "<i></i>")
    write_card(ctx, "stale", {"overall": "within_tol"}, "<i></i>")
    assert prune(ctx, keep={"keep"}) == ["stale"]
    assert (ctx.card_dir / "keep.html").is_file()
    assert not (ctx.card_dir / "stale.html").is_file()
    assert not (ctx.card_dir / "stale.verdict.json").is_file()


def test_applicable_selects_by_applies_and_allowlist(core, tmp_path):
    class _GatedCard(ReportCardStep):
        name = "gated_card_demo"

        def applies(self, study):
            return bool(study.spec.get("demo"))

        def build(self, study):
            return {"status": "pass", "checks": [], "summary": "ok"}, "<div></div>"

    on = _ctx(tmp_path, {"name": "demo", "demo": True})
    assert [s.name for s in applicable(on, core, only="gated_card_demo")] == [
        "gated_card_demo"]
    off = _ctx(tmp_path, {"name": "demo"})
    assert applicable(off, core, only="gated_card_demo") == []


# ---------------------------------------------------------------------------
# (c) a ReportCardStep returning a gating verdict yields
#     data.status in {pass, fail, warn}.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", ["pass", "fail", "warn"])
def test_report_card_step_gating_verdict_status(core, status):
    class _GatingCard(ReportCardStep):
        name = f"gating_card_{status}"

        def build(self, study):
            return (
                {"status": status, "checks": [{"name": "c1", "ok": status == "pass"}],
                 "summary": f"{status} summary"},
                "<div>card</div>",
            )

    step = _GatingCard({}, core=core)
    out = step.update({"study": object()})
    assert out["data"]["status"] in {"pass", "fail", "warn"}
    assert out["data"]["status"] == status
    assert "checks" in out["data"] and "summary" in out["data"]


def test_report_card_step_invoke_swallows_errors(core):
    class _BoomCard(ReportCardStep):
        name = "boom_card_demo"

        def build(self, study):
            raise RuntimeError("boom")

    step = _BoomCard({}, core=core)
    result = step.invoke({"study": object()})
    assert result.get() == {}


def test_analysis_step_invoke_fails_loudly(core):
    class _BoomAnalysisStep(AnalysisStep):
        name = "boom_analysis_step_demo"
        scale = "single"

        def analyze(self, rows):
            raise RuntimeError("boom")

    step = _BoomAnalysisStep({}, core=core)
    with pytest.raises(RuntimeError):
        step.invoke({"results": []})
