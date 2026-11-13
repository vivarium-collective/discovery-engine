"""verdict_overreach: a critique trigger (a verdict/confidence that outruns its
evidence) surfaces on the frontier and routes to the `critique` skill — the advisory
counterpart to anomaly -> hypothesize. It is non-blocking, deduped against an existing
critique report, and NEVER dispatched by advance (critique is filled, not executed)."""
from __future__ import annotations

from discovery_engine import discover
from discovery_engine.projection import engine as de
from discovery_engine.phases.generation_emit import generation_requests


def _trigger(study="s3", kind="verdict_divergence"):
    return {"id": f"ct:{study}:{kind}", "kind": kind, "study": study,
            "authored": "passed", "derived": "refuted",
            "note": "authored gate_status disagrees with the computed verdict"}


def _kinds(items):
    return {i["kind"] for i in items}


def test_verdict_overreach_item_emitted():
    items = de.frontier({"signals": {"verdict_overreach": [_trigger()]}}, {})
    (it,) = [i for i in items if i["kind"] == "verdict_overreach"]
    assert it["target"] == "s3"
    assert it["op"] == "critique"
    assert it["priority"] == de._PRIORITY["verdict_overreach"] == 46


def test_executor_for_critique_is_advisory_review():
    ex = de._executor_for("critique", "s3")
    assert ex["executor"] == "discover critique"
    assert ex["consequence"] == "review"
    assert ex["auto_safe"] is False
    assert "discover critique" in ex["command"]


def test_dedup_against_existing_critique_report():
    sig = {"signals": {"verdict_overreach": [_trigger(study="s3")]}}
    inv = {"critique_reports": [{"id": "cr:1", "target": "s3", "status": "open"}]}
    assert "verdict_overreach" not in _kinds(de.frontier(sig, inv))
    # a different study is still surfaced
    inv2 = {"critique_reports": [{"id": "cr:2", "target": "other", "status": "open"}]}
    assert "verdict_overreach" in _kinds(de.frontier(sig, inv2))


def test_resolution_record_discharges_the_item():
    sig = {"signals": {"verdict_overreach": [_trigger(study="s3")]}}
    inv = {"resolutions": [{"kind": "verdict_overreach", "target": "s3", "rationale": "critiqued"}]}
    assert "verdict_overreach" not in _kinds(de.frontier(sig, inv))


def test_verdict_overreach_routes_to_critique_request():
    opps = de.opportunities({"signals": {"verdict_overreach": [_trigger()]}}, {})
    reqs = generation_requests(opps)
    assert [r.kind for r in reqs] == ["critique"]
    assert reqs[0].produces == "critique_report/v1"
    assert reqs[0].target == "s3"


def test_verdict_overreach_not_dispatched_by_advance():
    from discovery_engine.discover.cycle import _ADVANCE_OPS
    assert "critique" not in _ADVANCE_OPS


def test_verdict_overreach_is_non_blocking_and_resolvable():
    assert "verdict_overreach" not in de._CLOSURE_BLOCKING
    assert "verdict_overreach" in discover.RESOLUTION_KINDS
    # a lone verdict_overreach item does not keep the investigation open
    clo = de.closure({"signals": {"verdict_overreach": [_trigger()]}}, {})
    assert clo["closed"] is True


def test_contradicted_study_excluded_from_verdict_overreach(monkeypatch):
    # A `bad` (contradicted -> REPAIR) study is excluded from the signal, mirroring the other
    # signals: a critique there would be redundant noise. Guards the `bad` filter.
    from discovery_engine.discover import cycle as _cyc
    monkeypatch.setattr(_cyc, "critique_triggers", lambda specs: [_trigger(study="s3")])
    studies = {"s3": {"name": "s3", "topic": "t", "studies": [], "hypotheses": [],
                      "baseline": [{"name": "b", "composite": "x.y.z", "params": {}}]}}
    inv = {"studies": ["s3"], "hypotheses": []}
    ev = {"ledger": [{"study": "s3", "status": "contradicted"}], "by_study": {}, "summary": {}}
    res = discover.run_cycle(inv, studies, evidence_state=ev)
    assert res["signals"]["verdict_overreach"] == []
    assert "verdict_overreach" not in _kinds(de.frontier(res, inv))


def test_run_cycle_wires_critique_triggers_into_signals(monkeypatch):
    # The wiring: run_cycle must fold critique_triggers() into signals['verdict_overreach'],
    # whence the frontier surfaces it. critique_triggers' own logic is covered elsewhere.
    from discovery_engine.discover import cycle as _cyc
    monkeypatch.setattr(_cyc, "critique_triggers", lambda specs: [_trigger(study="s3")])
    studies = {"s3": {"name": "s3", "topic": "t", "studies": [], "hypotheses": [],
                      "baseline": [{"name": "b", "composite": "x.y.z", "params": {}}]}}
    inv = {"studies": ["s3"], "hypotheses": []}
    res = discover.run_cycle(inv, studies)
    assert "verdict_overreach" in res["signals"]
    assert [t["study"] for t in res["signals"]["verdict_overreach"]] == ["s3"]
    assert "verdict_overreach" in _kinds(de.frontier(res, inv))
