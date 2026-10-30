"""Autonomous study authoring + launch policy (DE 2.0).

Gate: the engine can author a complete, run-ready study.yaml from a parent + proposal
without the host prose flow; and the autonomy policy decides which proposals launch.
"""
from __future__ import annotations

from discovery_engine import study_seeding as S


def _parent():
    return {
        "schema_version": 4, "name": "s4-energetic", "investigation": "eukaryogenesis",
        "topic": "bioenergetics", "tags": ["energy"],
        "baseline": [{"name": "baseline",
                      "composite": "viva_genesis.composites.bioenergetic_internalization.bioenergetic_internalization",
                      "params": {"catalyst_efficiency": 1.0, "v_start": 0.4}}],
        "observables": ["pop/survivors"],
        "behavior_tests": [{"name": "net-energy-positive",
                            "measure": {"kind": "derived_scalar", "path": "settled_internalization_survivors"},
                            "pass_if": {"op": ">", "value": 0.5}}],
        "findings": [{"id": "f-s4", "statement": "internalization pays off above a pump threshold",
                      "next_action": "Investigate the pump-efficiency threshold more finely",
                      "evidence": {"observed": 1.0, "from_test": "net-energy-positive"}}],
    }


def test_authored_study_is_run_ready():
    study = S.build_seeded_study(_parent(), {"name": "eukaryogenesis"}, new_slug="s4b-pump-threshold",
                                 proposal={"id": "p1", "title": "Pump-efficiency threshold sweep",
                                           "motivation": "find the survivors threshold vs catalyst_efficiency"},
                                 model_change={"params": {"catalyst_efficiency": 0.5}})
    assert S.is_run_ready(study) == []                        # complete + runnable
    assert study["baseline"][0]["composite"].endswith("bioenergetic_internalization")
    assert study["baseline"][0]["params"]["catalyst_efficiency"] == 0.5   # model_change applied
    assert study["observables"] == ["pop/survivors"]          # inherited emit path
    assert study["behavior_tests"][0]["measure"]["path"] == "settled_internalization_survivors"
    assert study["seeded_from"]["study"] == "s4-energetic"
    assert study["name"] == "s4b-pump-threshold"


def test_finding_seed_folds_in_purpose():
    study = S.build_seeded_study(_parent(), {"name": "eukaryogenesis"}, new_slug="s4c",
                                 proposal={"id": "p2"}, finding_id="f-s4")
    assert study.get("purpose")                               # ChildSeed populated purpose
    assert study["seeded_from"].get("finding") == "f-s4"


def test_autonomy_policy_gates_selection():
    suggs = [
        {"id": "ready-1", "awaiting_expert": [], "status": "ready"},
        {"id": "gated-1", "awaiting_expert": ["which scenario?"], "status": "awaiting_expert"},
    ]
    # human-in-the-loop: nothing launches without approval; expert-gated held
    hil = S.select_for_launch(suggs, autonomy=S.HUMAN_IN_THE_LOOP)
    assert hil["launch"] == [] and len(hil["held"]) == 2
    # human-in-the-loop with approval: the approved ready one launches
    appr = S.select_for_launch(suggs, autonomy=S.HUMAN_IN_THE_LOOP, approved_ids=["ready-1"])
    assert [s["id"] for s in appr["launch"]] == ["ready-1"]
    # autonomous: every ready one launches; expert-gated still held
    auto = S.select_for_launch(suggs, autonomy=S.AUTONOMOUS)
    assert [s["id"] for s in auto["launch"]] == ["ready-1"]
    assert any(h["suggestion"]["id"] == "gated-1" for h in auto["held"])
