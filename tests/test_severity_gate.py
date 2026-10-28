"""severity_gate: a study gate that respects axis severity.

Only HARD-severity axis mismatches fail the study; soft mismatches + drift are a
non-failing 'warn' (calibration outstanding); directional axes never emit
'mismatch' so they never gate. Operates on a build_report() test_report/v1 doc.
"""
from discovery_engine.study_verdict import severity_gate
from discovery_engine.post_sim import build_report


def _card(overall, axes):  # axes: list[(id, verdict, severity)]
    return {"schema": "report_card_verdict/v2", "overall": overall,
            "groups": {"g": {"verdict": overall,
                             "axes": [{"id": i, "verdict": v, "severity": s}
                                      for i, v, s in axes]}}}


def test_hard_mismatch_fails():
    rep = build_report("s", "r1", {"c": _card("mismatch", [("a", "mismatch", "hard")])})
    g = severity_gate(rep)
    assert g["status"] == "fail"
    assert g["hard_mismatch"] == 1
    assert g["gated_by"] == [{"card": "c", "group": "g", "id": "a"}]


def test_soft_mismatch_only_warns_not_fails():
    rep = build_report("s", "r1", {"c": _card("mismatch", [("a", "mismatch", "soft")])})
    g = severity_gate(rep)
    assert g["status"] == "warn"          # soft miss records, never gates
    assert g["hard_mismatch"] == 0
    assert g["gated_by"] == []


def test_drift_warns():
    rep = build_report("s", "r1", {"c": _card("drift", [("a", "drift", "directional")])})
    assert severity_gate(rep)["status"] == "warn"


def test_all_within_tol_passes():
    rep = build_report("s", "r1", {"c": _card("within_tol", [("a", "within_tol", "hard")])})
    g = severity_gate(rep)
    assert g["status"] == "pass" and g["hard_mismatch"] == 0 and g["gated_by"] == []


def test_hard_dominates_a_mixed_report():
    rep = build_report("s", "r1", {
        "c1": _card("within_tol", [("ok", "within_tol", "hard")]),
        "c2": _card("mismatch", [("soft", "mismatch", "soft"),
                                 ("bad", "mismatch", "hard")]),
    })
    g = severity_gate(rep)
    assert g["status"] == "fail" and g["hard_mismatch"] == 1
    assert g["gated_by"] == [{"card": "c2", "group": "g", "id": "bad"}]


def test_empty_report_passes():
    assert severity_gate({})["status"] == "pass"
    assert severity_gate(build_report("s", "r1", {}))["status"] == "pass"
