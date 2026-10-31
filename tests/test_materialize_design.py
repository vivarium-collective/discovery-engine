from __future__ import annotations

import copy

from discovery_engine import experiment_design as D
from discovery_engine.study_seeding import _inherit_arm

PARENT = {"name": "s2", "investigation": "inv", "observables": [{"path": "flux"}],
          "behavior_tests": [{"measure": {"path": "flux"}}],
          "baseline": [{"name": "baseline", "composite": "pkg.A", "params": {"k": 1, "d": 2}}]}
INV = {"name": "inv", "studies": ["s2"], "hypotheses": [
    {"id": "h-spatial", "study": "s2", "status": "open", "predictions": [
        {"observable": "flux", "when": {"structure": "proximate"}, "expected": {"op": ">=", "value": 1}}]},
    {"id": "h-well-mixed-null", "study": "s2", "status": "open", "predictions": [
        {"observable": "flux", "when": {"structure": "proximate"}, "expected": {"op": "<=", "value": 0.5}}]}]}


def test_inherit_arm_composite_override_and_params():
    arm = _inherit_arm(PARENT, {"composite": "pkg.B"})[0]
    assert arm["composite"] == "pkg.B" and arm["params"] == {"k": 1, "d": 2}
    arm = _inherit_arm(PARENT, {"composite": "pkg.B", "params": {"k": 9}})[0]
    assert arm["composite"] == "pkg.B" and arm["params"]["k"] == 9
    assert _inherit_arm(PARENT, {})[0]["composite"] == "pkg.A"


def test_materialize_variants_controls_hypotheses_origin():
    d = D.design_discriminating_study(INV, "s2")
    before = copy.deepcopy((d, PARENT, INV))
    st = D.materialize_design(d, PARENT, INV, {"composite": "pkg.B", "params": {}})
    assert (d, PARENT, INV) == before
    assert st["baseline"][0]["composite"] == "pkg.B"
    assert len(st["variants"]) == len(d["interventions"])
    assert any(v.get("control") for v in st["variants"])
    assert st["hypotheses"] and all(h["study"] == st["name"] and
                                    h["origin"] == {"kind": "design", "ref": d["id"]}
                                    for h in st["hypotheses"])


def test_non_parameter_when_needs_author_parameter_when_overrides():
    d = D.design_discriminating_study(INV, "s2")
    st = D.materialize_design(d, PARENT, INV, None)
    assert all(v.get("needs_author") for v in st["variants"])
    d2 = {"id": "design:x", "study": "s2", "controls": [], "predictions": [],
          "interventions": [{"hypothesis": "h", "when": {"k": 5}}]}
    v = D.materialize_design(d2, PARENT, INV, None)["variants"][0]
    assert v["params"] == {"k": 5} and not v.get("needs_author")
