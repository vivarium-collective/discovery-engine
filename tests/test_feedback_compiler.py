"""Tests for discovery_engine.feedback_compiler (P2/P3: feedback -> engine capability).

Synthetic, domain-neutral feedback — the compiler ships no domain vocabulary, so the
tests prove it works from STRUCTURED fields alone (never prose), with strict schema
validation and closure-history preservation.
"""
from __future__ import annotations

import copy

import pytest

from discovery_engine import feedback_compiler as fc
from discovery_engine.feedback_compiler import (
    FeedbackValidationError,
    compile_feedback,
    reconcile_and_reopen,
)


def _request(**over) -> dict:
    dr = {
        "schema_version": "1.0",
        "investigation_id": "inv-demo",
        "feedback_file": "feedback/reopening.yaml",
        "lifecycle": {"action": "reopen", "reason": "unresolved", "preserve_history": True},
        "execution": {"mode": "supervised_autonomous", "max_cycles": 5,
                      "require_approval_for": ["steer", "expensive_compute"]},
        "continuation": {"policy": "highest_information_gain", "stop_when": ["budget_exhausted"]},
    }
    dr.update(over)
    return {"discovery_request": dr}


def _feedback(**over) -> dict:
    fb = {
        "feedback_version": "1.0",
        "target": {"investigation": "inv-demo", "intent": "reopen_and_launch"},
        "status_reconciliation": {"requested_state": "active"},
        "scientific_challenge": {
            "id": "central-bridge",
            "question": "Does X cause Y?",
            "central_gap": "No causal chain demonstrated.",
            "mandate": "Find one pathway and one failure regime.",
            "competing_hypotheses": [
                {"id": "H-pos", "mechanism": "X selects for Y.",
                 "predictions": [{"observable": "obs_x", "op": ">", "value": 0,
                                  "regime": "under_treatment_vs_control"}]},
                {"id": "H-null", "mechanism": "No selection after costs.",
                 "predictions": [{"observable": "obs_x", "op": "<=", "value": 0}]},
            ],
            "nonnegotiable_controls": ["no_treatment", "shuffled_input"],
        },
        "report_diagnostics": [
            {"id": "lifecycle-mismatch", "severity": "blocking",
             "observation": "UI closed, export open.", "request": "Reconcile."},
        ],
        "next_cycle": {
            "title": "Does X select Y?",
            "objective": "Establish or reject the causal path.",
            "design": [
                {"id": "study-discriminating", "question": "Does X increase Y-fitness?",
                 "reuse": ["parent-a"], "extend_with": ["agent_growth"],
                 "measurements": ["obs_x"], "decision_rule": "Prefer H-pos only under treatment."},
                {"id": "study-open", "question": "What is the secondary cost?",
                 "reuse": [], "extend_with": [],
                 "measurements": ["obs_y"], "decision_rule": "Author bands before run."},
            ],
        },
        "following_cycle": {
            "new_composite_model": {
                "name": "bridge_hybrid", "architecture": "process_bigraph_composite",
                "modules": ["module_a", "module_b"],
                "coupling_contract": "typed stores, conserved mass/energy."},
        },
        "cycle_instructions": {"autonomy": "supervised"},
        "human_feedback_to_engine": "Reopen and run discriminating experiments.",
    }
    fb.update(over)
    return fb


def _inv_spec(**over) -> dict:
    s = {"name": "inv-demo", "status": "closed", "studies": ["parent-a"],
         "executive": {"verdict_status": "closed", "objective": "old objective"}}
    s.update(over)
    return s


def _studies() -> dict:
    return {"parent-a": {"name": "parent-a", "schema_version": 4,
                         "baseline": [{"name": "b", "composite": "pkg.mod.parent_composite"}],
                         "observables": ["obs_x"]}}


# ── compile_feedback: typed actions ───────────────────────────────────────────
def test_compile_emits_the_core_typed_actions():
    compiled = compile_feedback(_request(), _feedback())
    types = [a["type"] for a in compiled["actions"]]
    assert "reopen" in types
    assert "register_objective" in types
    assert types.count("register_hypothesis") == 2
    assert types.count("seed_study") == 2
    assert "propose_composite" in types
    # controls (2) + composite modules (2) -> 4 require_mechanism actions
    assert types.count("require_mechanism") == 4
    assert compiled["summary"]["bridge_composite"] == "bridge_hybrid"


def test_discriminating_study_gets_a_pass_if_from_structured_bands():
    compiled = compile_feedback(_request(), _feedback())
    seed = next(a for a in compiled["actions"]
                if a["type"] == "seed_study" and a["slug"] == "study-discriminating")
    primary = [t for t in seed["spec"]["behavior_tests"] if t["classification"] == "primary"]
    assert len(primary) == 1
    t = primary[0]
    assert t["measure"] == "obs_x"
    # pass_if is the first predicting hypothesis's band (H-pos: >0), from structured data
    assert t["pass_if"] == {"op": ">", "value": 0.0}
    assert seed["acceptance"] == [t["name"]]
    assert not seed["author_predictions"]          # fully covered -> no authoring needed


def test_study_without_structured_bands_emits_author_predictions_no_fabrication():
    compiled = compile_feedback(_request(), _feedback())
    seed = next(a for a in compiled["actions"]
                if a["type"] == "seed_study" and a["slug"] == "study-open")
    # obs_y has no competing predictions -> no primary (pass_if) test is fabricated
    assert all(t["classification"] == "secondary" for t in seed["spec"]["behavior_tests"])
    assert all("pass_if" not in t for t in seed["spec"]["behavior_tests"])
    assert seed["author_predictions"] == ["obs_y"]


def test_hypotheses_use_canonical_expected_shape():
    compiled = compile_feedback(_request(), _feedback())
    h = next(a["hypothesis"] for a in compiled["actions"]
             if a["type"] == "register_hypothesis" and a["hypothesis"]["id"] == "H-pos")
    assert h["competing_with"] == ["H-null"]
    assert h["predictions"][0]["observable"] == "obs_x"
    assert h["predictions"][0]["expected"] == {"op": ">", "value": 0.0}
    assert h["predictions"][0]["when"] == {"regime": "under_treatment_vs_control"}


# ── strict validation ─────────────────────────────────────────────────────────
def test_unknown_field_in_discovery_request_is_rejected():
    with pytest.raises(FeedbackValidationError):
        compile_feedback(_request(surprise="nope"), _feedback())


def test_unknown_field_in_feedback_executable_subtree_is_rejected():
    fb = _feedback()
    fb["scientific_challenge"]["competing_hypotheses"][0]["predictions"][0]["typo"] = 1
    with pytest.raises(FeedbackValidationError):
        compile_feedback(_request(), fb)


def test_prediction_observable_must_be_measured_somewhere():
    fb = _feedback()
    fb["scientific_challenge"]["competing_hypotheses"][0]["predictions"][0]["observable"] = "never_measured"
    with pytest.raises(FeedbackValidationError, match="never_measured"):
        compile_feedback(_request(), fb)


def test_prediction_missing_operand_is_rejected():
    fb = _feedback()
    # op present but no value -> not inferable, must error (never guess)
    fb["scientific_challenge"]["competing_hypotheses"][0]["predictions"][0] = {"observable": "obs_x", "op": ">"}
    with pytest.raises(FeedbackValidationError):
        compile_feedback(_request(), fb)


# ── band disjointness ─────────────────────────────────────────────────────────
@pytest.mark.parametrize("b1,b2,disjoint", [
    ({"op": ">", "value": 0}, {"op": "<=", "value": 0}, True),
    ({"op": ">=", "value": 0.5}, {"op": "<", "value": 0.5}, True),
    ({"op": ">", "value": 0}, {"op": ">=", "value": 1}, False),   # both satisfiable at 2
    ({"low": 0.0, "high": 0.1}, {"op": ">=", "value": 0.2}, True),
    ({"low": 0.0, "high": 0.3}, {"op": ">=", "value": 0.2}, False),
])
def test_bands_disjoint(b1, b2, disjoint):
    assert fc._bands_disjoint(b1, b2) is disjoint


# ── reconcile_and_reopen ──────────────────────────────────────────────────────
def test_reconcile_reopens_and_preserves_closure_history():
    compiled = compile_feedback(_request(), _feedback())
    res = reconcile_and_reopen(compiled, _inv_spec(), _studies())
    inv = res["inv_spec"]
    assert inv["status"] == "active"
    assert inv["executive"]["verdict_status"] == "open"
    assert inv["executive"]["objective"] == "Does X cause Y?"
    # prior closed state preserved, not dropped
    hist = inv["lifecycle_history"]
    assert len(hist) == 1
    assert hist[0]["prior_status"] == "closed"
    assert hist[0]["prior_verdict_status"] == "closed"
    assert hist[0]["challenge_id"] == "central-bridge"


def test_reconcile_seeds_studies_hypotheses_mechanisms_and_reconciliation():
    compiled = compile_feedback(_request(), _feedback())
    res = reconcile_and_reopen(compiled, _inv_spec(), _studies())
    inv = res["inv_spec"]
    assert set(res["new_studies"]) == {"study-discriminating", "study-open"}
    assert {"study-discriminating", "study-open"} <= set(inv["studies"])
    assert {h["id"] for h in inv["hypotheses"]} == {"H-pos", "H-null"}
    assert {"no_treatment", "shuffled_input", "module_a", "module_b"} <= set(inv["required_mechanisms"])
    assert inv["bridge_composite"]["name"] == "bridge_hybrid"
    assert inv["reconciliation_log"][0]["id"] == "lifecycle-mismatch"
    assert inv["cycle_flags"] == {"autonomy": "supervised", "reopened": True}
    # the discriminating study's acceptance criterion is registered
    assert any(c["study"] == "study-discriminating" for c in inv["acceptance_criteria"])


def test_seeded_study_binds_parent_composite_as_first_fidelity_arm():
    compiled = compile_feedback(_request(), _feedback())
    res = reconcile_and_reopen(compiled, _inv_spec(), _studies())
    disc = res["new_studies"]["study-discriminating"]
    assert disc["baseline"][0]["composite"] == "pkg.mod.parent_composite"
    # study-open reuses nothing -> no baseline arm
    assert res["new_studies"]["study-open"]["baseline"] == []


def test_reconcile_is_idempotent():
    compiled = compile_feedback(_request(), _feedback())
    once = reconcile_and_reopen(compiled, _inv_spec(), _studies())["inv_spec"]
    twice = reconcile_and_reopen(compiled, once, {})["inv_spec"]
    assert len(twice["hypotheses"]) == len(once["hypotheses"]) == 2
    assert len(twice["studies"]) == len(once["studies"])
    assert len(twice["lifecycle_history"]) == 1          # not re-appended
    assert len(twice["acceptance_criteria"]) == len(once["acceptance_criteria"])
    assert len(twice["required_mechanisms"]) == len(once["required_mechanisms"])


def test_preserve_history_false_skips_the_audit():
    compiled = compile_feedback(_request(lifecycle={"action": "reopen", "preserve_history": False}),
                                _feedback())
    res = reconcile_and_reopen(compiled, _inv_spec(), _studies())
    assert "lifecycle_history" not in res["inv_spec"]
    assert res["inv_spec"]["status"] == "active"          # still reopens


# ── end-to-end via ingest_feedback (tmp workspace) ────────────────────────────
def _write_ws(tmp_path):
    import yaml
    ws = tmp_path / "ws"
    inv_dir = ws / "investigations" / "inv-demo"
    (inv_dir / "feedback").mkdir(parents=True)
    (ws / "studies" / "parent-a").mkdir(parents=True)
    yaml.safe_dump(_inv_spec(), (inv_dir / "investigation.yaml").open("w"))
    yaml.safe_dump(_studies()["parent-a"], (ws / "studies" / "parent-a" / "study.yaml").open("w"))
    yaml.safe_dump(_feedback(), (inv_dir / "feedback" / "reopening.yaml").open("w"))
    yaml.safe_dump(_request(), (inv_dir / "discovery_request.yaml").open("w"))
    return ws, inv_dir / "discovery_request.yaml"


def test_ingest_feedback_dry_run_does_not_write(tmp_path):
    ws, req = _write_ws(tmp_path)
    res = fc.ingest_feedback(req, ws, write=False)
    assert res["wrote"] is False
    assert res["summary"]["reopened"] is True
    # nothing written: investigation still closed on disk
    import yaml
    on_disk = yaml.safe_load((ws / "investigations" / "inv-demo" / "investigation.yaml").read_text())
    assert on_disk["status"] == "closed"


def test_ingest_feedback_write_reopens_and_seeds(tmp_path):
    ws, req = _write_ws(tmp_path)
    res = fc.ingest_feedback(req, ws, write=True)
    assert res["wrote"] is True
    import yaml
    on_disk = yaml.safe_load((ws / "investigations" / "inv-demo" / "investigation.yaml").read_text())
    assert on_disk["status"] == "active"
    assert on_disk["executive"]["verdict_status"] == "open"
    assert (ws / "studies" / "study-discriminating" / "study.yaml").is_file()
    assert (ws / "studies" / "study-open" / "study.yaml").is_file()


# ── auto-bind competing hypotheses to their discriminating study ───────────────
def test_competing_hypotheses_bind_to_their_discriminating_study():
    """The compiler binds each competing hypothesis to the seeded study measuring its observable,
    so discriminating_hypotheses (which groups by `study:`) reads the study as discriminable out of
    the box — no manual per-study binding after a reopen (a feedback-driven reopen gap)."""
    from discovery_engine.hypotheses import discriminating_hypotheses
    compiled = compile_feedback(_request(), _feedback())
    bind = {a["hypothesis"]["id"]: a["hypothesis"].get("study")
            for a in compiled["actions"] if a["type"] == "register_hypothesis"}
    # H-pos / H-null both predict obs_x, which study-discriminating measures -> bound to it
    assert bind == {"H-pos": "study-discriminating", "H-null": "study-discriminating"}
    # end-to-end: the reopened investigation reads that study as discriminable (disjoint bands)
    inv = reconcile_and_reopen(compiled, _inv_spec(), _studies())["inv_spec"]
    disc = discriminating_hypotheses(inv)
    assert disc.get("study-discriminating", {}).get("discriminable") is True
