"""Tests for the L4 verdict re-grade (discovery_engine.study_audit)."""
import json

from discovery_engine.card_grade import grade_card, verdict_json
from discovery_engine.study_audit import audit_workspace

_STUDY = "name: s\n"


def _ws(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    d = tmp_path / "studies" / "s"
    d.mkdir(parents=True)
    (d / "study.yaml").write_text(_STUDY, encoding="utf-8")
    return d


def _write_verdict(sdir, measured, stored_overall):
    """Real grade_card output (reference 10, 5% tol) serialized via verdict_json,
    with each axis carrying its recompute inputs; `overall` overridden."""
    criterion = {"type": "rel_tol", "reference": 10.0, "tol_rel": 0.05}
    ref = {"axes": {"x": {"label": "X", "group": "G", "criterion": criterion}}}
    v = verdict_json(grade_card({"x": measured}, ref))
    for grp in v["groups"].values():
        for ax in grp["axes"]:
            ax["criterion"] = criterion
            ax["measured"] = measured
    v["overall"] = stored_overall
    rc = sdir / "viz" / "report_card"
    rc.mkdir(parents=True)
    (rc / "c.verdict.json").write_text(json.dumps(v), encoding="utf-8")


def _regrade(tmp_path):
    report = audit_workspace(tmp_path)
    return [c for a in report.studies for c in a.checks if c.name == "verdict-regrade"]


def test_stale_verdict_json_fails_regrade(tmp_path):
    sdir = _ws(tmp_path)
    _write_verdict(sdir, 20.0, "within_tol")  # 20 vs 10 -> mismatch
    checks = _regrade(tmp_path)
    assert len(checks) == 1
    c = checks[0]
    assert (c.level, c.status, c.tier) == ("L4", "fail", "hard")


def test_matching_verdict_passes(tmp_path):
    sdir = _ws(tmp_path)
    _write_verdict(sdir, 20.0, "mismatch")
    checks = _regrade(tmp_path)
    assert [c.status for c in checks] == ["pass"]


def test_no_run_abstains(tmp_path):
    _ws(tmp_path)
    assert _regrade(tmp_path) == []


# --- the recompute must genuinely run (not just roll up stored verdicts) -----

def _load(sdir):
    f = sdir / "viz" / "report_card" / "c.verdict.json"
    return f, json.loads(f.read_text(encoding="utf-8"))


def _axes(v):
    return [ax for g in v["groups"].values() for ax in g["axes"]]


def test_hand_edited_axis_verdict_fails_regrade(tmp_path):
    # Stored axis verdict AND overall both claim within_tol, but the stored
    # inputs (20 vs ref 10 +/-5%) recompute to mismatch. Passes only if the
    # recompute really runs.
    sdir = _ws(tmp_path)
    _write_verdict(sdir, 20.0, "mismatch")
    f, v = _load(sdir)
    for ax in _axes(v):
        ax["verdict"] = "within_tol"
    v["overall"] = "within_tol"
    f.write_text(json.dumps(v), encoding="utf-8")
    c = _regrade(tmp_path)
    assert [(x.status, x.tier) for x in c] == [("fail", "hard")]


def test_post_hoc_edited_measured_fails_regrade(tmp_path):
    # Honest verdict for measured=10.0 (within_tol); then measured is edited to
    # 20.0 without regenerating the verdict -> recompute differs from stored.
    sdir = _ws(tmp_path)
    _write_verdict(sdir, 10.0, "within_tol")
    assert [c.status for c in _regrade(tmp_path)] == ["pass"]
    f, v = _load(sdir)
    for ax in _axes(v):
        ax["measured"] = 20.0
    f.write_text(json.dumps(v), encoding="utf-8")
    assert [c.status for c in _regrade(tmp_path)] == ["fail"]


def test_verdict_without_recompute_inputs_abstains(tmp_path):
    # Today's real on-disk shape: truthy overall, axes carry no criterion/measured.
    sdir = _ws(tmp_path)
    rc = sdir / "viz" / "report_card"
    rc.mkdir(parents=True)
    v = {"schema": "report_card_verdict/v1", "overall": "within_tol",
         "groups": {"g": {"verdict": "within_tol", "axes": [
             {"id": "x", "verdict": "within_tol", "value": 1.0}]}}}
    (rc / "c.verdict.json").write_text(json.dumps(v), encoding="utf-8")
    assert _regrade(tmp_path) == []


def test_malformed_verdict_json_abstains(tmp_path):
    sdir = _ws(tmp_path)
    rc = sdir / "viz" / "report_card"
    rc.mkdir(parents=True)
    (rc / "c.verdict.json").write_text("{not json", encoding="utf-8")
    assert _regrade(tmp_path) == []


def test_stored_alias_overall_does_not_false_fail(tmp_path):
    # Stored "fail" is a vocab alias of mismatch; must not false-FAIL.
    sdir = _ws(tmp_path)
    _write_verdict(sdir, 20.0, "fail")
    assert [c.status for c in _regrade(tmp_path)] == ["pass"]


# --- H1b: verdict_json persists the recompute inputs --------------------------

_H1B_CRIT = {"type": "rel_tol", "reference": 10.0, "tol_rel": 0.05}
_H1B_REF = {"axes": {"x": {"label": "X", "group": "G", "criterion": _H1B_CRIT}}}


def test_verdict_json_carries_criterion_and_measured():
    v = verdict_json(grade_card({"x": 20.0}, _H1B_REF))
    (ax,) = _axes(v)
    assert ax["criterion"] == _H1B_CRIT
    assert ax["measured"] == 20.0
    assert v["schema"] == "report_card_verdict/v1"


def test_regrade_end_to_end_catches_mismatch(tmp_path):
    sdir = _ws(tmp_path)
    v = verdict_json(grade_card({"x": 20.0}, _H1B_REF))  # no manual patching
    v["overall"] = "within_tol"  # deliberately wrong; axis recomputes to mismatch
    rc = sdir / "viz" / "report_card"
    rc.mkdir(parents=True)
    (rc / "c.verdict.json").write_text(json.dumps(v), encoding="utf-8")
    (c,) = _regrade(tmp_path)
    assert (c.level, c.status, c.tier) == ("L4", "fail", "hard")
