"""The per-decision audit log: why each gated action was allowed to run."""
import yaml

from discovery_engine.discover._policy import AutonomyPolicy, decision_record
from discovery_engine.discover.actions import record_decision


def _inv(tmp_path):
    d = tmp_path / "investigations" / "demo"
    d.mkdir(parents=True)
    p = d / "investigation.yaml"
    p.write_text(yaml.safe_dump({"name": "demo", "studies": []}))
    return p


def test_record_decision_appends_with_timestamp(tmp_path):
    p = _inv(tmp_path)
    rec = decision_record(AutonomyPolicy(), actor="orchestrator", consequence="simulation",
                          target="study:s1", rationale="run-ready, in budget", result="ran",
                          input_hashes=["h1"])
    stored = record_decision(p, rec)
    assert stored["at"]                                   # stamped
    assert stored["consequence"] == "simulation" and stored["result"] == "ran"
    log = yaml.safe_load(p.read_text())["decision_log"]
    assert len(log) == 1 and log[0]["target"] == "study:s1"
    assert log[0]["policy_version"]                        # provenance carried through


def test_decision_log_is_append_only(tmp_path):
    p = _inv(tmp_path)
    for i in range(3):
        record_decision(p, decision_record(AutonomyPolicy(), actor="a", consequence="review",
                                           target=f"t{i}", rationale="r", result="ok"))
    log = yaml.safe_load(p.read_text())["decision_log"]
    assert [e["target"] for e in log] == ["t0", "t1", "t2"]
