"""A hypothesize request carries the typed Discrepancy it must explain (Wave B wiring)."""
from discovery_engine.phases.generation import GateConfig
from discovery_engine.phases.generation_emit import generation_requests


def _anomaly_opp(study, observable):
    return {"id": f"anomaly:{study}:{observable}", "source": f"{study}:{observable}",
            "action_type": "revise_model", "kind": "anomaly", "priority": 48,
            "question": f"{observable} outside every live band", "value": {"score": 1.0}}


def _disc(study, observable, observed):
    return {"id": f"dc:{study}:{observable}", "study": study, "observable": observable,
            "kind": "anomaly", "source_evidence_ref": f"{study}#{observable}", "observed": observed}


def test_hypothesize_request_carries_its_discrepancy():
    reqs = generation_requests([_anomaly_opp("s1", "obs.val")],
                               discrepancies=[_disc("s1", "obs.val", 0.95)],
                               gates=GateConfig.preset("free_run"))
    assert len(reqs) == 1 and reqs[0].kind == "hypothesize"
    d = reqs[0].context["discrepancy"]
    assert d["source_evidence_ref"] == "s1#obs.val" and d["observed"] == 0.95


def test_distinct_discrepancies_give_distinct_context():
    opps = [_anomaly_opp("s1", "obs.a"), _anomaly_opp("s1", "obs.b")]
    discs = [_disc("s1", "obs.a", 0.1), _disc("s1", "obs.b", 0.9)]
    reqs = generation_requests(opps, discrepancies=discs, gates=GateConfig.preset("free_run"))
    observed = sorted(r.context["discrepancy"]["observed"] for r in reqs)
    assert observed == [0.1, 0.9]        # each request is anchored to its own surprise


def test_no_discrepancies_leaves_context_clean():
    reqs = generation_requests([_anomaly_opp("s1", "obs.val")],
                               gates=GateConfig.preset("free_run"))
    assert reqs[0].kind == "hypothesize" and "discrepancy" not in reqs[0].context


def test_non_hypothesize_request_gets_no_discrepancy():
    # a pending_gate -> author_tests request must not pick up a discrepancy
    opp = {"id": "pending_gate:s1:t", "source": "s1:t", "action_type": "test",
           "kind": "pending_gate", "priority": 42, "question": "run the gate",
           "value": {"score": 0.5}}
    reqs = generation_requests([opp], discrepancies=[_disc("s1", "t", 1.0)],
                               gates=GateConfig.preset("free_run"))
    assert reqs[0].kind == "author_tests" and "discrepancy" not in reqs[0].context
