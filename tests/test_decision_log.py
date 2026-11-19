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


def test_record_decision_preserves_authored_comments_and_block_scalars(tmp_path):
    """Regression: appending to decision_log must NOT strip hand-authored comments
    or reflow block scalars. `discover investigate` calls record_decision on every
    decision (even in projection mode), so a plain re-dump silently mangled authored
    investigation.yaml files (block comments deleted, block scalars -> single-quoted)."""
    d = tmp_path / "investigations" / "demo"
    d.mkdir(parents=True)
    p = d / "investigation.yaml"
    p.write_text(
        "# This is a DEMONSTRATION investigation -- do not delete this header.\n"
        "name: demo\n"
        "description: |\n"
        "  A multi-line block scalar\n"
        "  that must stay in block style.\n"
        "studies: []\n"
    )
    rec = decision_record(AutonomyPolicy(), actor="orchestrator", consequence="generation:visualize",
                          target="fig:x", rationale="on-goal", result="applied")
    record_decision(p, rec)

    text = p.read_text()
    assert "# This is a DEMONSTRATION investigation" in text   # comment survived
    assert "description: |" in text                            # block scalar stayed block
    assert "that must stay in block style." in text
    # and the write still worked
    import yaml as _y
    doc = _y.safe_load(text)
    assert doc["decision_log"][0]["target"] == "fig:x"
