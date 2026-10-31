"""Tests for discovery_engine.discovery_engine — the formal discovery crank.

The engine turns one cycle projection (run_cycle's output) + the investigation spec
into a prioritized FRONTIER of unresolved items, each with a recommended operation;
a single NEXT action; a CLOSURE check; and validity-INVARIANT checks. It is pure and
deterministic (same evidence -> same frontier), so turning the crank is reproducible.
"""
from __future__ import annotations

from discovery_engine import discovery_engine as de


def _cycle(open_calls=None, edges=None, verdicts=None, behaviors=None, validations=None):
    return {
        "knowledge": {"verdicts": verdicts or {}, "behaviors": behaviors or {}},
        "argument": {"claims": {}, "edges": edges or []},
        "narrative": {"units": []},
        "audit": [],
        "gates": [],
        "open_calls": open_calls or [],
        "validations": validations or [],
    }


def _record(type="logical", scope="claim", target="claim:x", execution="failed",
            judgment=None, findings=None):
    return {"type": type, "scope": scope, "target": target,
            "result": {"execution_status": execution, "scientific_judgment": judgment},
            "findings": findings or ["something failed"],
            "recommendations": ["fix it"], "provenance": {"validator_id": f"{type}.{scope}.t"}}


def test_frontier_projects_a_failing_validation_record():
    rec = _record(target="claim:unbacked", findings=["claim is supported but no evidence"])
    cyc = _cycle(validations=[rec])
    items = {(i["kind"], i["target"]): i for i in de.frontier(cyc, {})}
    assert ("validation_finding", "claim:unbacked") in items
    assert "supported but no evidence" in items[("validation_finding", "claim:unbacked")]["rationale"]


def test_frontier_skips_passing_inconclusive_and_narrative_validation_records():
    passing = _record(execution="passed")
    inconclusive = _record(execution="inconclusive", target="claim:warn")  # standing feedback
    narrative = _record(type="narrative", scope="narrative", target="narrative")  # via open_calls
    cyc = _cycle(validations=[passing, inconclusive, narrative])
    kinds = {i["kind"] for i in de.frontier(cyc, {})}
    assert "validation_finding" not in kinds


def test_frontier_suppresses_standing_validation_records():
    # a failing 'standing' record (rigor/band feedback) must NOT flood the frontier.
    standing = {**_record(type="scientific", scope="study", target="s", execution="failed"),
                "surfaces": "standing"}
    cyc = _cycle(validations=[standing])
    assert "validation_finding" not in {i["kind"] for i in de.frontier(cyc, {})}


def test_frontier_projects_errored_validator_records():
    errored = _record(type="structural", scope="study", target="scientific.study.rigor",
                      execution="error", findings=["validator raised RuntimeError: boom"])
    cyc = _cycle(validations=[errored])
    assert ("validation_finding", "scientific.study.rigor") in {
        (i["kind"], i["target"]) for i in de.frontier(cyc, {})}


def test_frontier_validation_finding_is_resolvable():
    rec = _record(target="claim:unbacked")
    cyc = _cycle(validations=[rec])
    inv = {"resolutions": [{"kind": "validation_finding", "target": "claim:unbacked"}]}
    kinds = {(i["kind"], i["target"]) for i in de.frontier(cyc, inv)}
    assert ("validation_finding", "claim:unbacked") not in kinds


def test_frontier_flags_stale_validation_against_snapshot():
    from discovery_engine import validation as V
    old = V.validation_record(type="logical", scope="claim", target="c",
                              execution_status="failed", findings=["x"], validator_id="v")
    snap = V.snapshot_of([old])
    # the fresh record for the same validator|target now reads differently -> stale
    new = V.validation_record(type="logical", scope="claim", target="c",
                              execution_status="passed", validator_id="v")
    cyc = _cycle(validations=[new])
    items = {(i["kind"], i["target"]): i for i in de.frontier(cyc, {"validation_snapshot": snap})}
    assert ("stale_validation", "c") in items
    assert items[("stale_validation", "c")]["op"] == "revalidate"


def test_diagnose_input_limited_when_decisions_dominate():
    # a frontier of expert decisions -> the investigation is waiting on input, not planner work.
    cyc = _cycle(open_calls=[{"kind": "decision", "title": f"D{i}", "refs": [f"d{i}"]} for i in range(3)])
    d = de.diagnose(cyc, {})
    flags = {f["flag"] for f in d["flags"]}
    assert "input_limited" in flags


def test_diagnose_planner_limited_on_a_large_same_priority_cluster():
    # many divergences at the same priority -> the crank cannot rank them by value.
    cyc = _cycle(open_calls=[{"kind": "divergence", "title": f"v{i}", "refs": [f"c{i}"]} for i in range(5)])
    d = de.diagnose(cyc, {})
    assert "planner_limited" in {f["flag"] for f in d["flags"]}


def test_diagnose_validation_limited_on_low_pass_rate():
    recs = [_record(type="empirical", scope="claim", target=f"claim:{i}", execution="failed")
            for i in range(4)]
    d = de.diagnose(_cycle(validations=recs), {})
    assert "validation_limited" in {f["flag"] for f in d["flags"]}
    assert d["validator_pass_rate"] == 0.0


def test_diagnose_capability_limited_on_a_validator_error():
    err = _record(type="structural", scope="study", target="v", execution="error")
    assert "capability_limited" in {f["flag"] for f in de.diagnose(_cycle(validations=[err]), {})["flags"]}


def test_diagnose_task_saturated_when_closed_and_empty():
    d = de.diagnose(_cycle(), {})  # nothing open, no validations
    assert d["closed"] is True
    assert "task_saturated" in {f["flag"] for f in d["flags"]}


def test_diagnose_flags_carry_evidence_and_intervention():
    cyc = _cycle(open_calls=[{"kind": "decision", "title": "D", "refs": ["d"]}])
    for f in de.diagnose(cyc, {})["flags"]:
        assert f["evidence"] and f["intervention"]  # every flag is actionable


def test_opportunity_carries_value_components_and_explicit_unknowns():
    cyc = _cycle(open_calls=[{"kind": "open_question", "title": "Q", "refs": ["claim:x"]}])
    o = de.opportunities(cyc, {})[0]
    assert set(o["value"]) >= {"info", "impact", "enable", "cost", "score"}
    assert "competing_predictions" in o          # the field is present (value may be None)
    assert o["action_type"] == "test"
    assert o["unknowns"]                          # no silent defaults — unknowns are explicit


def test_impact_ranks_a_foundational_target_above_a_leaf():
    edges = [{"from": "claim:found", "to": f"claim:d{i}", "type": "builds-on"} for i in range(3)]
    cyc = _cycle(edges=edges, open_calls=[
        {"kind": "divergence", "title": "f", "refs": ["claim:found"]},
        {"kind": "divergence", "title": "l", "refs": ["claim:leaf"]}])
    by = {o["source"]: o for o in de.opportunities(cyc, {})}
    assert by["claim:found"]["value"]["impact"] > by["claim:leaf"]["value"]["impact"]


def test_opportunities_ranked_by_value_break_the_priority_tie():
    # two divergences, same priority 50; the one more claims depend on ranks first,
    # regardless of id order — the planner_limited fix.
    edges = [{"from": "claim:b-high", "to": f"claim:d{i}", "type": "builds-on"} for i in range(4)]
    cyc = _cycle(edges=edges, open_calls=[
        {"kind": "divergence", "title": "a", "refs": ["claim:a-low"]},
        {"kind": "divergence", "title": "b", "refs": ["claim:b-high"]}])
    srcs = [o["source"] for o in de.opportunities(cyc, {})]
    assert srcs.index("claim:b-high") < srcs.index("claim:a-low")


def test_enable_scores_a_decision_that_unblocks_suggestions():
    cyc = _cycle(open_calls=[{"kind": "decision", "title": "Prior (og-x): supply", "refs": []}])
    cyc["suggestions"] = [{"id": "s1", "awaiting_expert": ["Prior (og-x): supply"],
                           "status": "awaiting_expert"}]
    dec = [o for o in de.opportunities(cyc, {}) if o["action_type"] == "steer"]
    assert dec and dec[0]["value"]["enable"] > 0


def test_execute_plan_resolves_each_op_to_an_executor_and_consequence():
    edges = [{"from": "claim:a", "to": f"claim:d{i}", "type": "builds-on"} for i in range(3)]
    cyc = _cycle(edges=edges, open_calls=[
        {"kind": "divergence", "title": "a", "refs": ["claim:a"]},
        {"kind": "open_question", "title": "q", "refs": ["claim:q"]}])
    plan = {p["action_type"]: p for p in de.execute_plan(cyc, {})}
    assert plan["reconcile"]["executor"] and plan["reconcile"]["consequence"] == "review"
    assert plan["test"]["consequence"] == "simulation"  # running a study is a sim
    assert plan["test"]["command"] and "run" in plan["test"]["command"]
    # each item carries its value score + a gate (auto_safe flag)
    assert "score" in plan["reconcile"] and plan["reconcile"]["auto_safe"] in (True, False)


def test_execute_plan_marks_revalidate_auto_safe_and_steer_human_only():
    cyc = _cycle(validations=[{"type": "logical", "scope": "claim", "target": "claim:x",
                               "result": {"execution_status": "failed"}, "surfaces": "frontier",
                               "findings": ["x"]}],
                 open_calls=[{"kind": "decision", "title": "D (og-x): decide", "refs": []}])
    # inject a stale_validation by snapshotting a differing record
    import discovery_engine.validation as V
    snap = V.snapshot_of([V.validation_record(type="logical", scope="claim", target="claim:x",
                                              execution_status="passed", validator_id="v")])
    cyc["validations"][0]["provenance"] = {"validator_id": "v"}
    plan = {p["action_type"]: p for p in de.execute_plan(cyc, {"validation_snapshot": snap})}
    if "revalidate" in plan:
        assert plan["revalidate"]["auto_safe"] is True
    if "steer" in plan:
        assert plan["steer"]["consequence"] == "human-only" and plan["steer"]["auto_safe"] is False


def test_execute_plan_is_value_ranked():
    edges = [{"from": "claim:big", "to": f"claim:d{i}", "type": "builds-on"} for i in range(4)]
    cyc = _cycle(edges=edges, open_calls=[
        {"kind": "divergence", "title": "s", "refs": ["claim:small"]},
        {"kind": "divergence", "title": "b", "refs": ["claim:big"]}])
    srcs = [p["source"] for p in de.execute_plan(cyc, {})]
    assert srcs.index("claim:big") < srcs.index("claim:small")


def test_invariants_flags_epistemic_separation_in_validation_records():
    smuggled = _record(type="computational", scope="study", target="s",
                       execution="passed", judgment="supported")
    cyc = _cycle(validations=[smuggled])
    inv = de.invariants(cyc)
    assert any(v["invariant"] == "epistemic_separation" and v["target"] == "s" for v in inv)


def test_frontier_prioritizes_criteria_over_gaps_over_divergence():
    cyc = _cycle(
        open_calls=[
            {"kind": "narrative_gap", "title": "gap A->B", "refs": ["claim:a", "claim:b"]},
            {"kind": "divergence", "title": "claim c diverges", "refs": ["claim:c"]},
        ],
        verdicts={"sref": {"verdict": "refuted"}, "sok": {"verdict": "supported"}},
    )
    inv = {"acceptance_criteria": [{"study": "sref", "behavior": "x"},
                                   {"study": "sok", "behavior": "y"}]}
    f = de.frontier(cyc, inv)
    kinds = [i["kind"] for i in f]
    # unaddressed criterion (sref refuted) ranks first; addressed (sok supported) absent
    assert kinds[0] == "unaddressed_criterion"
    assert f[0]["target"] == "sref"
    assert f[0]["op"] == "branch"
    # priority order: criterion > narrative_gap > divergence
    assert kinds.index("unaddressed_criterion") < kinds.index("narrative_gap") < kinds.index("divergence")
    # addressed criterion sok is met by its verdict → NOT an unaddressed_criterion, but
    # its named behavior never ran, so it surfaces as verification debt (a pending_gate).
    assert "sok" not in [i["target"] for i in f if i["kind"] == "unaddressed_criterion"]
    assert ("pending_gate", "sok") in {(i["kind"], i["target"]) for i in f}


def test_pending_gate_flags_a_criterion_met_by_verdict_with_an_unrun_behavior():
    # a gate whose study verdict is supported but whose named behavior never passed is
    # verification debt: a pending_gate on the frontier, but it does NOT block closure.
    cyc = _cycle(verdicts={"s": {"verdict": "supported"}}, behaviors={"s": {"g": "PENDING"}})
    inv = {"acceptance_criteria": [{"study": "s", "behavior": "g"}]}
    items = {(i["kind"], i["target"]): i for i in de.frontier(cyc, inv)}
    assert ("pending_gate", "s") in items
    assert items[("pending_gate", "s")]["op"] == "test"
    assert de.closure(cyc, inv)["closed"] is True  # verification debt doesn't block closure


def test_pending_gate_is_resolvable():
    cyc = _cycle(verdicts={"s": {"verdict": "supported"}}, behaviors={"s": {"g": "PENDING"}})
    inv = {"acceptance_criteria": [{"study": "s", "behavior": "g"}],
           "resolutions": [{"kind": "pending_gate", "target": "s"}]}
    assert ("pending_gate", "s") not in {(i["kind"], i["target"]) for i in de.frontier(cyc, inv)}


def test_criterion_met_when_named_behavior_passes_despite_refuted_verdict():
    # A null / adversarial-control study: refuting the null is the DESIGNED success,
    # and its acceptance behavior passed. Acceptance must track the behavior outcome,
    # not the verdict sign — so the criterion is MET and raises no frontier item.
    cyc = _cycle(verdicts={"nullctl": {"verdict": "refuted"}},
                 behaviors={"nullctl": {"rejects-generic": "PASS"}})
    inv = {"acceptance_criteria": [{"study": "nullctl", "behavior": "rejects-generic"}]}
    f = de.frontier(cyc, inv)
    assert "nullctl" not in [i["target"] for i in f if i["kind"] == "unaddressed_criterion"]
    assert de.closure(cyc, inv)["closed"] is True


def test_criterion_unmet_when_named_behavior_fails():
    # The named behavior failed → the criterion is unmet regardless of verdict.
    cyc = _cycle(verdicts={"s": {"verdict": "refuted"}},
                 behaviors={"s": {"b": "FAIL"}})
    inv = {"acceptance_criteria": [{"study": "s", "behavior": "b"}]}
    f = de.frontier(cyc, inv)
    assert "s" in [i["target"] for i in f if i["kind"] == "unaddressed_criterion"]


def test_criterion_falls_back_to_verdict_when_no_behavior_outcome():
    # Backward-compatible: with no behavior outcome recorded, a non-met verdict still flags.
    cyc = _cycle(verdicts={"s": {"verdict": "refuted"}})  # behaviors empty
    inv = {"acceptance_criteria": [{"study": "s", "behavior": "b"}]}
    assert "s" in [i["target"] for i in de.frontier(cyc, inv) if i["kind"] == "unaddressed_criterion"]


def test_calibrated_study_is_run_never_touched_is_branched():
    # A calibrated-but-unassessed study (recorded findings, no tracked verdict) already
    # EXISTS and is seeded: the frontier surfaces it to RUN (open_question -> test), so the
    # autonomous crank executes the study that is already built. A never-touched study (no
    # recorded evidence) has no real study behind the criterion, so it branches a new one.
    # Both still block closure.
    cyc = _cycle(verdicts={
        "cal": {"verdict": "unassessed", "calibrated": True},
        "raw": {"verdict": "unassessed", "calibrated": False},
    })
    inv = {"acceptance_criteria": [{"study": "cal", "behavior": "b"},
                                   {"study": "raw", "behavior": "b"}]}
    items = {i["target"]: i for i in de.frontier(cyc, inv)}
    assert "cal" in items and "raw" in items          # both still block closure
    assert items["cal"]["kind"] == "open_question" and items["cal"]["op"] == "test"
    assert items["raw"]["kind"] == "unaddressed_criterion" and items["raw"]["op"] == "branch"
    assert "run it" in items["cal"]["rationale"]


def test_frontier_skips_items_resolved_in_the_investigation():
    # Uniform resolution: one recorded resolution discharges a frontier item of ANY kind,
    # via a single check in add() — the op that produced the artifact records a resolution.
    cyc = _cycle(
        edges=[{"type": "challenge", "from": "a", "to": "claim:x", "relation": "refutes"}],
        open_calls=[{"kind": "narrative_gap", "title": "gap p->q", "refs": ["claim:p", "claim:q"]}],
        verdicts={"sref": {"verdict": "refuted"}},
    )
    inv = {
        "acceptance_criteria": [{"study": "sref", "behavior": "b"}],
        "resolutions": [
            {"kind": "open_challenge", "target": "claim:x", "rationale": "adjudicated: concordant"},
            {"kind": "narrative_gap", "target": "claim:p", "rationale": "bridged by study z"},
            {"kind": "unaddressed_criterion", "target": "sref", "rationale": "met by follow-up"},
        ],
    }
    keys = {(i["kind"], i["target"]) for i in de.frontier(cyc, inv)}
    assert ("open_challenge", "claim:x") not in keys
    assert ("narrative_gap", "claim:p") not in keys
    assert ("unaddressed_criterion", "sref") not in keys


def test_frontier_resolution_is_specific_to_the_target():
    cyc = _cycle(edges=[{"type": "challenge", "from": "a", "to": "claim:x", "relation": "refutes"}])
    inv = {"resolutions": [{"kind": "open_challenge", "target": "claim:OTHER", "rationale": "x"}]}
    assert ("open_challenge", "claim:x") in {(i["kind"], i["target"]) for i in de.frontier(cyc, inv)}


def test_closure_closed_when_every_item_is_resolved():
    cyc = _cycle(edges=[{"type": "challenge", "from": "a", "to": "claim:x", "relation": "refutes"}])
    inv = {"resolutions": [{"kind": "open_challenge", "target": "claim:x", "rationale": "adjudicated"}]}
    assert de.closure(cyc, inv)["closed"] is True


def test_frontier_flags_open_challenge_edges():
    cyc = _cycle(edges=[{"from": "claim:alt", "to": "claim:main", "type": "challenge", "relation": "refutes"},
                        {"from": "claim:p", "to": "claim:main", "type": "prerequisite", "relation": "builds-on"}])
    f = de.frontier(cyc, {})
    ch = [i for i in f if i["kind"] == "open_challenge"]
    assert len(ch) == 1 and ch[0]["target"] == "claim:main" and ch[0]["op"] == "synthesize"


def test_next_action_is_highest_priority_or_none():
    cyc = _cycle(open_calls=[{"kind": "divergence", "title": "d", "refs": ["claim:c"]}])
    assert de.next_action(cyc, {})["kind"] == "divergence"
    # a fully-resolved cycle yields no action
    assert de.next_action(_cycle(), {}) is None


def test_closure_requires_no_criteria_gaps_or_challenges():
    # open: an unaddressed criterion
    cyc = _cycle(verdicts={"sref": {"verdict": "refuted"}})
    inv = {"acceptance_criteria": [{"study": "sref", "behavior": "x"}]}
    c = de.closure(cyc, inv)
    assert c["closed"] is False and c["unaddressed_criteria"] == ["sref"]
    # closed: criterion met, no gaps/challenges
    cyc2 = _cycle(verdicts={"sok": {"verdict": "qualified"}})
    inv2 = {"acceptance_criteria": [{"study": "sok", "behavior": "x"}]}
    assert de.closure(cyc2, inv2)["closed"] is True


def test_invariants_flags_unprovenanced_and_divergence_hidden():
    # a supported claim with no evidence_refs violates the provenance invariant
    cyc = _cycle()
    cyc["argument"]["claims"] = {
        "claim:a": {"verdict": "supported", "evidence_refs": []},
        "claim:b": {"verdict": "supported", "evidence_refs": ["f1"]},
    }
    v = de.invariants(cyc)
    assert any(x["invariant"] == "provenance" and x["target"] == "claim:a" for x in v)
    assert not any(x["target"] == "claim:b" for x in v)


def test_executor_for_revise_is_actionable():
    # a validation_finding (op `revise`) must resolve to a real executor + command, not the
    # `unknown`/None default (which rendered dead rows in the Decide plan).
    ex = de._executor_for("revise", "some-study")
    assert ex["consequence"] == "review"
    assert ex["command"] and "resolve" in ex["command"] and "some-study" in ex["command"]


def test_closure_blocking_opportunity_outranks_a_non_blocking_one_without_edges():
    # On an edgeless graph (impact=0 for all), a closure-blocking criterion (branch) must
    # rank ABOVE a non-blocking item (a divergence) via the closure-leverage term, instead
    # of being buried by its higher cost.
    cyc = _cycle(verdicts={"crit": {"verdict": "refuted"}},
                 open_calls=[{"kind": "divergence", "title": "d diverges", "refs": ["claim:d"]}])
    inv = {"acceptance_criteria": [{"study": "crit", "behavior": "x"}]}
    by_kind = {o["kind"]: o for o in de.opportunities(cyc, inv)}
    assert by_kind["unaddressed_criterion"]["value"]["closure"] == 1.0
    assert by_kind["divergence"]["value"]["closure"] == 0.0
    assert by_kind["unaddressed_criterion"]["value"]["score"] > by_kind["divergence"]["value"]["score"]


def test_diagnose_pass_rate_excludes_standing_feedback():
    # the validator pass-rate must measure ACTIONABLE (frontier) validators, not `standing`
    # feedback (rigor/band), which otherwise washes the rate out.
    fr_pass = {**_record(target="a", execution="passed"), "surfaces": "frontier"}
    fr_fail = {**_record(target="b", execution="failed"), "surfaces": "frontier"}
    st_fail1 = {**_record(target="c", execution="failed"), "surfaces": "standing"}
    st_fail2 = {**_record(target="d", execution="failed"), "surfaces": "standing"}
    d = de.diagnose(_cycle(validations=[fr_pass, fr_fail, st_fail1, st_fail2]), {})
    assert d["validator_pass_rate"] == 0.5     # 1 of 2 FRONTIER passed; 2 standing excluded
    assert d["standing_feedback"] == 2


def test_diagnose_flags_argument_disconnected_when_no_edges_either_channel():
    # edges are the union of investigation scientific_argument + study parent_studies;
    # zero edges means NEITHER channel was authored.
    cyc = _cycle(edges=[]); cyc["argument"]["claims"] = {"c1": {}}
    assert "argument_disconnected" in [f["flag"] for f in de.diagnose(cyc, {})["flags"]]


def test_diagnose_no_disconnected_flag_when_edges_present():
    cyc = _cycle(edges=[{"from": "a", "to": "b"}]); cyc["argument"]["claims"] = {"c1": {}}
    assert "argument_disconnected" not in [f["flag"] for f in de.diagnose(cyc, {})["flags"]]


def test_diagnose_census_splits_frontier_and_standing():
    recs = [{**_record(target="a", execution="failed"), "surfaces": "standing"},
            {**_record(target="b", execution="failed"), "surfaces": "frontier"},
            {**_record(target="c", execution="passed"), "surfaces": "frontier"}]
    d = de.diagnose(_cycle(validations=recs), {})
    assert d["census_standing"]["failed"] == 1
    assert d["census_frontier"]["failed"] == 1 and d["census_frontier"]["passed"] == 1


def test_competing_hypotheses_boost_opportunity_value_and_fill_competing_predictions():
    """A test/branch whose study carries competing hypotheses one run would
    separate gets a discrimination boost and its competing_predictions filled
    (no longer a declared unknown) — the stage-3 information-value signal."""
    cyc = _cycle(open_calls=[{"kind": "open_question", "title": "Q", "refs": ["claim:s"]}])
    inv = {"hypotheses": [
        {"id": "hi", "study": "s", "status": "open",
         "predictions": [{"observable": "x", "expected": {"op": ">=", "value": 0.5}}]},
        {"id": "lo", "study": "s", "status": "open",
         "predictions": [{"observable": "x", "expected": {"op": "<=", "value": 0.5}}]},
    ]}
    o = next(o for o in de.opportunities(cyc, inv) if o["source"] in ("s", "claim:s"))
    assert o["action_type"] in ("test", "branch")
    assert o["value"]["discriminate"] == 1.0
    assert {c["hypothesis"] for c in o["competing_predictions"]} == {"hi", "lo"}
    assert "competing_predictions" not in o["unknowns"]


def test_without_competing_hypotheses_discriminate_is_zero_and_predictions_unknown():
    cyc = _cycle(open_calls=[{"kind": "open_question", "title": "Q", "refs": ["claim:s"]}])
    o = de.opportunities(cyc, {})[0]
    assert o["value"]["discriminate"] == 0.0
    assert o["competing_predictions"] is None
    assert "competing_predictions" in o["unknowns"]
