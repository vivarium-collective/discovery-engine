"""Task 6: bind_model_versions + provenance.model_version slot."""
from __future__ import annotations

import copy

from discovery_engine import model_architect as M
from discovery_engine.finding_observations import _fill_finding

C = "discovery_engine.model_architect.build_catalog"


def _studies():
    return {
        "parent": {"name": "parent",
                   "baseline": [{"name": "baseline", "composite": C, "params": {"d": 0.1}}],
                   "variants": [{"name": "hi", "base": "baseline", "params": {"d": 0.9}}]},
        "child": {"name": "child", "seeded_from": {"study": "parent"},
                  "baseline": [{"name": "baseline", "composite": C, "params": {"d": 0.2}}]},
    }


def test_stable_id_per_arm_and_pure():
    s = _studies()
    snap = copy.deepcopy(s)
    a, b = M.bind_model_versions(s), M.bind_model_versions(s)
    assert a == b and s == snap
    assert a["parent"]["baseline"]["id"] == M.model_version(C, {"d": 0.1})["id"]
    assert a["parent"]["baseline"]["origin"]["kind"] == "authored"


def test_variant_base_inherits_composite_overlays_params():
    mv = M.bind_model_versions(_studies())["parent"]
    assert mv["hi"]["composite"] == C and mv["hi"]["params"] == {"d": 0.9}
    assert mv["hi"]["id"] != mv["baseline"]["id"]
    assert mv["hi"]["parent_ids"] == [mv["baseline"]["id"]]


def test_seeded_child_carries_parent_mv():
    out = M.bind_model_versions(_studies())
    cb = out["child"]["baseline"]
    assert cb["parent_ids"] == [out["parent"]["baseline"]["id"]]
    assert cb["origin"]["kind"] == "seeded"


def test_fill_finding_sets_model_version_only_when_absent():
    from ruamel.yaml import YAML
    y = YAML()
    f = y.load("id: f\nevidence: {from_test: t}\n")
    test = {"name": "t", "pass_if": {"low": 0, "high": 2}}
    assert _fill_finding(f, test, 1.0, "r1", [], model_version="mv:aaa")
    assert f["provenance"]["model_version"] == "mv:aaa"
    _fill_finding(f, test, 1.0, "r1", [], model_version="mv:bbb")
    assert f["provenance"]["model_version"] == "mv:aaa"


def test_backcompat_no_provenance(tmp_path):
    from discovery_engine.finding_observations import populate_finding_observations
    d = tmp_path / "s"
    d.mkdir()
    (d / "study.yaml").write_text(
        "name: s\nfindings:\n- id: f\n  statement: x\n")
    assert populate_finding_observations(d) == {"filled": 0, "skipped": 1}
    f = {"id": "f", "evidence": {"from_test": "t"}}
    _fill_finding(f, {"name": "t"}, None, "r1", [], model_version=None)
    assert "model_version" not in f.get("provenance", {})


def test_seeded_child_identical_to_parent_has_no_self_parent():
    s = _studies()
    s["child"]["baseline"][0]["params"] = {"d": 0.1}  # same composite+params as parent
    out = M.bind_model_versions(s)
    cb = out["child"]["baseline"]
    assert cb["id"] == out["parent"]["baseline"]["id"]
    assert cb["id"] not in cb["parent_ids"]


def test_backcompat_no_provenance_end_to_end(tmp_path):
    from discovery_engine.finding_observations import populate_finding_observations
    from discovery_engine.study_io import load_yaml
    d = tmp_path / "s"
    d.mkdir()
    (d / "study.yaml").write_text(
        "name: s\n"
        "baseline:\n- {name: baseline, composite: %s, params: {d: 1}}\n"
        "behavior_tests:\n- {name: t1, pass_if: {low: 0, high: 2}}\n"
        "runs:\n- name: r1\n  status: completed\n"
        "  computed_outcomes: {t1: {measured_value: 1.0}}\n"
        "findings:\n- id: f\n  statement: x\n  evidence: {from_test: t1}\n" % C)
    assert "provenance" not in load_yaml(d / "study.yaml")["findings"][0]
    assert populate_finding_observations(d)["filled"] == 1
    prov = load_yaml(d / "study.yaml")["findings"][0]["provenance"]
    assert prov["model_version"].startswith("mv:") and prov["run_ids"] == ["r1"]
