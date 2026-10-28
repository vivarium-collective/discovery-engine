"""Tests for the L1 preregistration check (discovery_engine.study_audit)."""
import yaml

from discovery_engine.study_audit import audit_workspace

_PASS_IF = {"metric": "growth_rate", "op": ">=", "value": 0.5}


def _ws(tmp_path, spec):
    (tmp_path / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    d = tmp_path / "studies" / "s"
    d.mkdir(parents=True)
    (d / "study.yaml").write_text(yaml.safe_dump({"name": "s", **spec}), encoding="utf-8")


def _test(gate_class, pass_if=_PASS_IF):
    return {"name": "g1", "gate_class": gate_class, "pass_if": pass_if}


def _checks(tmp_path):
    report = audit_workspace(tmp_path)
    return [c for a in report.studies for c in a.checks if c.name == "preregistration"]


def test_unregistered_acceptance_fails(tmp_path):
    _ws(tmp_path, {"behavior_tests": [_test("acceptance_criterion")]})
    fails = [c for c in _checks(tmp_path) if c.status == "fail"]
    assert len(fails) == 1
    assert (fails[0].level, fails[0].tier) == ("L1", "hard")
    assert "g1" in fails[0].detail


def test_changed_band_fails(tmp_path):
    # preregistration_gate_alignment checks equality, not width: any pass_if that
    # differs from the preregistered threshold (here wider) is flagged.
    wider = {"metric": "growth_rate", "op": ">=", "value": 0.1}
    _ws(tmp_path, {"behavior_tests": [_test("acceptance_criterion", wider)],
                   "preregistered": {"thresholds": {"g1": _PASS_IF}}})
    assert [c.status for c in _checks(tmp_path)] == ["fail"]


def test_matching_prereg_passes(tmp_path):
    _ws(tmp_path, {"behavior_tests": [_test("acceptance_criterion")],
                   "preregistered": {"thresholds": {"g1": _PASS_IF}}})
    assert [c.status for c in _checks(tmp_path)] == ["pass"]


def test_regression_pin_exempt(tmp_path):
    _ws(tmp_path, {"behavior_tests": [_test("regression_pin")]})
    assert _checks(tmp_path) == []
