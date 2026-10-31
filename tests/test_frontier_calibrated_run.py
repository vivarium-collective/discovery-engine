"""A calibrated-but-unrun member study behind an acceptance criterion must be
surfaced to RUN (a `test` op on the existing study), not branched into a brand-new
study. This is the gap that left seeded/calibrated arcs stuck on 'planned/pending':
the frontier told `advance` to branch (a human-gated placeholder, never executed)
instead of to run the study that already exists and is run-ready."""
import discovery_engine as de


def _cyc(verdict_rec):
    return {"knowledge": {"verdicts": {"sA": verdict_rec}, "behaviors": {}},
            "argument": {"edges": []}, "open_calls": [], "validations": [],
            "revision_proposals": [], "signals": {}}


INV = {"acceptance_criteria": [{"study": "sA", "behavior": "b1"}]}


def test_calibrated_unrun_criterion_is_runnable_not_branch():
    items = {(i["kind"], i["target"]): i
             for i in de.frontier(_cyc({"verdict": "unassessed", "calibrated": True}), INV)}
    # the existing calibrated study is surfaced to RUN (a test op), not branched
    assert ("open_question", "sA") in items
    assert items[("open_question", "sA")]["op"] == "test"
    assert ("unaddressed_criterion", "sA") not in items


def test_uncalibrated_unmet_criterion_still_branches():
    # no real study behind it (never seeded / no recorded evidence) → propose a new one
    items = {(i["kind"], i["target"]): i
             for i in de.frontier(_cyc({"verdict": "unassessed", "calibrated": False}), INV)}
    assert ("unaddressed_criterion", "sA") in items
    assert items[("unaddressed_criterion", "sA")]["op"] == "branch"
    assert ("open_question", "sA") not in items


def test_met_verdict_unrun_behavior_still_pending_gate():
    # regression: a MET verdict whose named behavior never ran stays pending_gate (test)
    items = {(i["kind"], i["target"]): i
             for i in de.frontier(_cyc({"verdict": "supported", "calibrated": False}), INV)}
    assert ("pending_gate", "sA") in items
    assert items[("pending_gate", "sA")]["op"] == "test"
