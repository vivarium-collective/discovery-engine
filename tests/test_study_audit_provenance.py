"""Tests for the L2 supporting-provenance check (discovery_engine.study_audit)."""
import yaml

from discovery_engine.study_audit import audit_workspace

# Authored verdict "accepted" -> derive_confidence == Accepted (no coded tests needed
# for the verdict; the test below exists only so from_test can resolve).
_TEST = {"name": "t1", "pass_if": {"metric": "m", "op": ">=", "value": 1}}


def _ws(tmp_path, spec):
    (tmp_path / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    d = tmp_path / "studies" / "s"
    d.mkdir(parents=True)
    (d / "study.yaml").write_text(yaml.safe_dump({"name": "s", **spec}), encoding="utf-8")


def _checks(tmp_path):
    report = audit_workspace(tmp_path)
    return [c for a in report.studies for c in a.checks if c.name == "supporting-provenance"]


def _spec(findings, verdict="accepted"):
    return {"report": {"verdict": verdict}, "behavior_tests": [_TEST], "findings": findings}


def _finding(status="confirms", run_ids=("r1",), from_test="t1", **extra):
    f = {"id": "F-01", "kind": "biological", "status": status, "statement": "x",
         "evidence": {"from_test": from_test} if from_test else {},
         "provenance": {"run_ids": list(run_ids)}, **extra}
    return f


def test_accepted_finding_without_runid_fails(tmp_path):
    _ws(tmp_path, _spec([_finding(run_ids=())]))
    fails = [c for c in _checks(tmp_path) if c.status == "fail"]
    assert len(fails) == 1
    assert (fails[0].level, fails[0].tier) == ("L2", "hard")
    assert "F-01" in fails[0].detail


def test_accepted_finding_with_unresolvable_from_test_fails(tmp_path):
    _ws(tmp_path, _spec([_finding(from_test="nope")]))
    assert [c.status for c in _checks(tmp_path)] == ["fail"]


def test_caveat_without_evidence_abstains(tmp_path):
    bare = {"id": "F-02", "kind": "biological", "status": "contradicts", "statement": "y"}
    caveat = {"id": "F-03", "kind": "caveat", "statement": "z"}
    _ws(tmp_path, _spec([bare, caveat]))
    assert _checks(tmp_path) == []


def test_non_supporting_study_abstains(tmp_path):
    _ws(tmp_path, _spec([_finding(run_ids=())], verdict="inconclusive"))
    assert _checks(tmp_path) == []


def test_fully_provenanced_passes(tmp_path):
    _ws(tmp_path, _spec([_finding()]))
    assert [c.status for c in _checks(tmp_path)] == ["pass"]


def test_status_completed_without_run_or_verdict_abstains(tmp_path):
    _ws(tmp_path, {"status": "completed", "behavior_tests": [_TEST],
                   "findings": [_finding(run_ids=())]})
    assert _checks(tmp_path) == []


def test_null_alongside_real_run_id_fails(tmp_path):
    _ws(tmp_path, _spec([_finding(run_ids=(None, "r1"))]))
    assert [c.status for c in _checks(tmp_path)] == ["fail"]
