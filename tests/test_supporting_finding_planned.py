"""A `planned` finding is a future target, not evidence.

`is_supporting_finding` decides whether a finding backs an Accepted verdict
(the L2 supporting-provenance audit then demands a run_ids/from_test trail for
each supporting finding). A finding whose status is `planned` describes intended
future work — like `exploratory`, it cannot back a conclusion that has not been
reached yet, so it must not be treated as supporting.
"""
from discovery_engine.study_verdict import is_supporting_finding


def test_planned_finding_is_not_supporting():
    assert is_supporting_finding({"id": "f", "status": "planned"}) is False


def test_planned_kind_is_not_supporting():
    # the predicate checks both `status` and `kind`
    assert is_supporting_finding({"id": "f", "kind": "planned"}) is False


def test_confirms_finding_is_still_supporting():
    assert is_supporting_finding({"id": "f", "status": "confirms"}) is True


def test_exploratory_still_excluded():
    # guard the existing behaviour the new entry sits beside
    assert is_supporting_finding({"id": "f", "status": "exploratory"}) is False
