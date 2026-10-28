from discovery_engine import study_status


def test_count_uses_canonical_flag_not_last_run():
    spec = {
        "tests": [{"name": "t1"}, {"name": "t2"}],
        "runs": [
            {"name": "good", "status": "completed", "canonical": True,
             "outcomes": {"t1": {"result": "PASS"}, "t2": {"result": "PASS"}}},
            {"name": "later-scratch", "status": "completed",
             "outcomes": {"t1": {"result": "FAIL"}}},
        ],
    }
    counts = study_status.count_test_outcomes(spec, spec["runs"])
    # canonical (good) → both PASS, not the last array entry (scratch → 1 FAIL)
    assert counts["pass"] == 2
    assert counts["fail"] == 0
