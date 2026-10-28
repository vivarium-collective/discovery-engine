# tests/test_test_vocab.py
import pytest
from discovery_engine import test_vocab as v

def test_canonical_set_is_exact():
    assert v.CANONICAL == ("within_tol", "drift", "mismatch", "ungraded")
    assert v.SEVERITY == ("hard", "soft", "directional")

@pytest.mark.parametrize("raw,expected", [
    ("PASS", "within_tol"), ("passed", "within_tol"), ("pass", "within_tol"), ("ok", "within_tol"),
    ("FAIL", "mismatch"), ("failed", "mismatch"), ("mismatch", "mismatch"),
    ("PARTIAL", "drift"), ("drift", "drift"), ("warn", "drift"),
    ("SKIP", "ungraded"), ("PENDING", "ungraded"), ("GAP", "ungraded"),
    ("within_tol", "within_tol"), (None, "ungraded"), ("", "ungraded"), ("bogus", "ungraded"),
])
def test_normalize_verdict(raw, expected):
    assert v.normalize_verdict(raw) == expected

def test_worst():
    assert v.worst(["within_tol", "drift", "mismatch"]) == "mismatch"
    assert v.worst(["within_tol", "within_tol"]) == "within_tol"
    assert v.worst([]) == "ungraded"
    assert v.worst(["PASS", "FAIL"]) == "mismatch"   # normalizes first

def test_agent_and_display_status():
    assert v.agent_status("within_tol") == "pass"
    assert v.agent_status("mismatch") == "fail"
    assert v.agent_status("drift") == "warn"
    assert v.agent_status("ungraded") == "no-data"
    assert v.display_status("within_tol") == "met"
    assert v.display_status("mismatch") == "not met"
    assert v.display_status("drift") == "conditional-pass"
    assert v.display_status("ungraded") == "not assessable"

def test_verdict_to_result_maps_all_verdicts():
    from discovery_engine.test_vocab import verdict_to_result
    assert verdict_to_result("within_tol") == "PASS"
    assert verdict_to_result("drift") == "PASS"        # a soft/directional warning still passes the gate
    assert verdict_to_result("mismatch") == "FAIL"
    assert verdict_to_result("ungraded") == "SKIP"
    assert verdict_to_result("pass") == "PASS"          # aliases normalize first
    assert verdict_to_result(None) == "SKIP"
