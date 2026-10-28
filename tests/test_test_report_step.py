import json
from discovery_engine.post_sim import (
    TestReportStep, StudyContext, build_report, tests_dir as _tests_dir, history_dir,
)
# Imported under a leading-underscore alias: pytest's default python_functions
# ("test") prefix-matches any bare module-level name starting with "test",
# so a plain `tests_dir` import here would get collected as a test item.


def _doc(overall, axes):  # axes: list[(id, verdict, margin)]
    return {"schema": "report_card_verdict/v2", "overall": overall,
            "groups": {"g": {"verdict": overall,
                             "axes": [{"id": i, "verdict": v, "margin": m} for i, v, m in axes]}}}


def _ctx(tmp_path):
    # Mirror StudyContext.load's path scheme (ws_root/workspace/studies/<name>)
    # so the directory this test writes/reads matches the one TestReportStep
    # resolves internally via StudyContext.load(ws_root, study_name).
    ctx = StudyContext.load(tmp_path, "demo")
    ctx.study_dir.mkdir(parents=True, exist_ok=True)
    return ctx


def test_build_report_overall_and_counts():
    cards = {"c1": _doc("within_tol", [("a", "within_tol", 0.2)]),
             "c2": _doc("mismatch", [("b", "mismatch", -0.1)])}
    rep = build_report("demo", "run1", cards)
    assert rep["schema"] == "test_report/v1"
    assert rep["overall"] == "mismatch"       # worst of the two cards
    assert rep["counts"]["cards"] == 2 and rep["counts"]["axes"] == 2
    assert rep["counts"]["mismatch"] == 1 and rep["counts"]["within_tol"] == 1
    assert rep["cards"]["c1"]["overall"] == "within_tol"


def test_write_report_and_step_writes_report_and_diff(tmp_path):
    ctx = _ctx(tmp_path)
    # seed a prior report into history so the diff has a baseline
    history_dir(ctx).mkdir(parents=True, exist_ok=True)
    prev = build_report("demo", "run0",
                        {"c1": _doc("mismatch", [("a", "mismatch", -0.5)])})
    (history_dir(ctx) / "run0.json").write_text(json.dumps(prev))
    # run the step on a curr where 'a' is now fixed
    step = TestReportStep.__new__(TestReportStep)
    step.config = {"ws_root": str(tmp_path), "study_name": "demo", "run_id": "run1",
                   "cards": {"c1": _doc("within_tol", [("a", "within_tol", 0.3)])}}
    out = step.update({})
    assert out["gate"] == "pass"  # severity_gate status (no hard mismatch)
    assert (_tests_dir(ctx) / "report.json").exists()
    diff = json.loads((_tests_dir(ctx) / "diff.json").read_text())
    entry = next(p for p in diff["per"] if p["id"] == "a")
    assert entry["change"] == "fixed"
    # history rotated: run1 present
    assert (history_dir(ctx) / "run1.json").exists()


def test_step_no_prior_history_diff_all_new(tmp_path):
    ctx = _ctx(tmp_path)
    step = TestReportStep.__new__(TestReportStep)
    step.config = {"ws_root": str(tmp_path), "study_name": "demo", "run_id": "r1",
                   "cards": {"c1": _doc("within_tol", [("a", "within_tol", 0.1)])}}
    out = step.update({})
    assert out["gate"] == "pass"  # first run, all within_tol → severity_gate pass
    diff = json.loads((_tests_dir(ctx) / "diff.json").read_text())
    assert diff["rollup"]["new"] == 1 and diff["per"][0]["change"] == "new"
