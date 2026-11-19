"""Hard release gates over a run summary."""
from discovery_engine.benchmark.gates import (
    MISSING_HOLDOUT_REPORT,
    SNAPSHOT_REPLAY,
    UNREVIEWED_PROMOTION,
    check_gates,
    passes_gates,
)


def test_clean_run_has_no_violations():
    assert check_gates({"inv": {"name": "demo"}}) == []
    assert passes_gates({"inv": {}})


def test_unreviewed_discovery_promotion_is_blocked():
    run = {"inv": {"discoveries": [{"id": "disc:1", "finding_candidate_id": "fc:1"}]},
           "holdout": {"n": 3}}            # candidate never reviewed/promoted
    gates = {v.gate for v in check_gates(run)}
    assert UNREVIEWED_PROMOTION in gates


def test_reviewed_discovery_with_holdout_passes():
    run = {"inv": {
        "discoveries": [{"id": "disc:1", "finding_candidate_id": "fc:1"}],
        "finding_candidates": [{"id": "fc:1", "status": "promoted"}],
        "review_decisions": [{"finding_candidate_id": "fc:1"}],
    }, "holdout": {"n": 3}}
    assert passes_gates(run)


def test_discovery_without_holdout_report_is_blocked():
    run = {"inv": {
        "discoveries": [{"id": "disc:1", "finding_candidate_id": "fc:1"}],
        "finding_candidates": [{"id": "fc:1", "status": "promoted"}],
    }}                                     # no holdout report
    gates = {v.gate for v in check_gates(run)}
    assert MISSING_HOLDOUT_REPORT in gates


def test_snapshot_must_replay_identically():
    assert any(v.gate == SNAPSHOT_REPLAY
               for v in check_gates({"inv": {}, "snapshot_fingerprints": ["a", "b"]}))
    assert check_gates({"inv": {}, "snapshot_fingerprints": ["a", "a"]}) == []
