from discovery_engine import discover
from discovery_engine.benchmark.scenario import build_scenario
from discovery_engine.phases import evidence_integrity as ei
from discovery_engine.projection import engine as de


def test_scenario_raises_anomaly_not_revision(tmp_path):
    sc = build_scenario(tmp_path)
    gt = sc.ground_truth
    _ip, inv, studies = discover.load_cycle_inputs(sc.ws_root, sc.inv_slug)
    evs = ei.reconcile_evidence_state(
        inv, studies, discover._load_runs_by_study(sc.ws_root, sc.inv_slug, studies))
    cyc = discover.run_cycle(inv, studies, evidence_state=evs)
    print(cyc["signals"], [(i["kind"], i.get("target")) for i in de.frontier(cyc, inv)])
    an = cyc["signals"]["anomalies"]
    assert len(an) == 1 and an[0]["observable"] == gt.observable
    assert an[0]["observed"] == gt.base_value
    # ENGINE FACT (see task-1 report): the engine converts every anomaly into an
    # anomaly-sourced revision proposal, and the frontier suppresses the raw `anomaly` item
    # once a proposal covers (study, observable). So the frontier shows `revision_proposed`
    # (source.kind == "anomaly", mechanism candidate needs_author), never a raw `anomaly`.
    kinds = [i["kind"] for i in de.frontier(cyc, inv)]
    assert "revision_proposed" in kinds and "anomaly" not in kinds
    (rp,) = cyc["revision_proposals"]
    assert rp["source"]["kind"] == "anomaly" and rp["source"]["observable"] == gt.observable
    assert any(c.get("kind") == "mechanism" and c.get("needs_author")
               for c in rp["candidate_changes"])
    assert not cyc["signals"]["model_disagreements"]
    b = gt.band
    assert b["low"] <= gt.true_value <= b["high"]
    assert not b["low"] <= gt.base_value <= b["high"]
