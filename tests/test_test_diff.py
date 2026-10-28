# tests/test_test_diff.py
from discovery_engine.test_diff import diff_reports

def _doc(axes):  # axes: list of (group, id, verdict, margin)
    groups = {}
    for g, i, v, m in axes:
        groups.setdefault(g, {"verdict": v, "axes": []})["axes"].append(
            {"id": i, "label": i, "verdict": v, "margin": m})
    return {"schema": "report_card_verdict/v2", "overall": "ungraded", "groups": groups}

def test_diff_transitions():
    prev = {"card": _doc([
        ("g", "a", "mismatch", -1.0),   # will fix
        ("g", "b", "within_tol", 0.2),  # will break
        ("g", "c", "within_tol", 0.1),  # will improve
        ("g", "d", "within_tol", 0.5),  # will regress
        ("g", "e", "within_tol", 0.3),  # will go away
    ])}
    curr = {"card": _doc([
        ("g", "a", "within_tol", 0.4),
        ("g", "b", "mismatch", -0.2),
        ("g", "c", "within_tol", 0.6),
        ("g", "d", "within_tol", 0.2),
        ("g", "f", "within_tol", 0.9),  # new
    ])}
    d = diff_reports(prev, curr)
    got = {(p["id"]): p["change"] for p in d["per"]}
    assert got == {"a": "fixed", "b": "broke", "c": "improved",
                   "d": "regressed", "e": "gone", "f": "new"}
    assert d["rollup"] == {"fixed": 1, "broke": 1, "improved": 1,
                           "regressed": 1, "new": 1, "gone": 1, "unchanged": 0}
    a = next(p for p in d["per"] if p["id"] == "a")
    assert a["margin_delta"] == 1.4   # 0.4 - (-1.0)

def test_diff_empty_prev():
    curr = {"card": _doc([("g", "a", "within_tol", 0.1)])}
    d = diff_reports({}, curr)
    assert d["per"][0]["change"] == "new" and d["rollup"]["new"] == 1

def test_diff_handles_none_id():
    curr = {"card": _doc([("g", None, "within_tol", 0.1), ("g", "a", "within_tol", 0.2)])}
    d = diff_reports({}, curr)   # must not raise TypeError from sorting None vs str
    assert len(d["per"]) == 2
    ids = {p["id"] for p in d["per"]}
    assert ids == {None, "a"}
