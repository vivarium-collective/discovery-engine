"""R2 wired into the live build loop: a capability gap closes (-> built) only when the build
hook's model passes its declared behavioral + interface tests and certifies coverage (§2's
second acceptance). A hook that supplies no verification evidence keeps the prior lexical path."""
from __future__ import annotations

from discovery_engine.feedback.capability_gaps import build_capability_gap

MECH = "metabolite_exchange"


def _gap():
    return {"id": "cg:s:abc", "status": "accepted", "mechanism": MECH}


def _hook(status="built", *, tests, certified=True, runnable=True):
    """A build hook that returns R2 verification evidence alongside the built process."""
    backing = {"kind": "test", "ref": "test_exchange"} if certified else None
    caps = [{"mechanism": MECH, **({"backed_by": backing} if backing else {})}]

    def hook(gap, ws_root=None):
        return {"status": status, "process": "viva_x.Exchange", "gap_id": gap["id"],
                "capabilities": caps,
                "validation": {"runnable": runnable, "issues": []},
                "behavioral_tests": tests}
    return hook


def test_gap_built_when_evidence_passes_all_gates():
    out = build_capability_gap(_gap(), _hook(tests=[{"name": "exchange_in_band", "result": "PASS"}]),
                               catalog=None)
    assert out["status"] == "built"
    assert out["built"]["process"] == "viva_x.Exchange"


def test_gap_failed_when_a_declared_behavioral_test_fails():
    out = build_capability_gap(_gap(), _hook(tests=[{"name": "exchange_in_band", "result": "FAIL"}]),
                               catalog=None)
    assert out["status"] == "failed"
    assert "does not yet close the gap" in (out.get("detail") or "")


def test_gap_failed_when_coverage_only_suggested_not_certified():
    out = build_capability_gap(_gap(), _hook(tests=[{"name": "t", "result": "PASS"}], certified=False),
                               catalog=None)
    assert out["status"] == "failed"


def test_gap_failed_when_model_interface_invalid():
    out = build_capability_gap(_gap(), _hook(tests=[{"name": "t", "result": "PASS"}], runnable=False),
                               catalog=None)
    assert out["status"] == "failed"


def test_legacy_hook_without_evidence_keeps_lexical_path():
    """No capabilities/validation/behavioral_tests in the result -> the §2 gate does not fire;
    with catalog=None the lexical checks are skipped and the built status stands (prior behavior)."""
    def legacy(gap, ws_root=None):
        return {"status": "built", "process": "viva_x.Exchange", "gap_id": gap["id"]}
    out = build_capability_gap(_gap(), legacy, catalog=None)
    assert out["status"] == "built"
