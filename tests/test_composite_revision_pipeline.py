"""The composite-SWAP revision candidate must be reachable through the REAL cycle entry
(`_cycle_with_evidence`, which has ws_root), not only through a hand-built catalog."""
from __future__ import annotations

from discovery_engine import discover
from discovery_engine.study_io import load_yaml, save_yaml_atomic
from tests.test_closed_loop_two_cycle import C2D, C3D, _ws


def _ws_with_alt(tmp_path):
    ip, sp = _ws(tmp_path)
    inv = load_yaml(ip)
    inv["studies"] = ["s3", "s3-3d"]
    save_yaml_atomic(ip, inv)
    sd = tmp_path / "studies" / "s3-3d"
    sd.mkdir(parents=True)
    save_yaml_atomic(sd / "study.yaml", {
        "name": "s3-3d", "investigation": "og", "schema_version": 4, "fidelity": "continuum",
        "baseline": [{"name": "baseline", "composite": C3D, "params": {}}],
        "observables": ["obs.val"]})
    return ip


def test_composite_swap_candidate_through_real_pipeline(tmp_path):
    ip = _ws_with_alt(tmp_path)
    _, inv, studies = discover.load_cycle_inputs(tmp_path, "og")
    cyc = discover._cycle_with_evidence(tmp_path, "og", inv, studies)
    props = [p for p in cyc["revision_proposals"] if p["source"]["study"] == "s3"]
    assert props
    kinds = [c["kind"] for p in props for c in p["candidate_changes"]]
    assert "composite" in kinds
    comp = next(c for p in props for c in p["candidate_changes"] if c["kind"] == "composite")
    assert comp["replace_with"] == C3D
    # the pure projection with no catalog stays param-only (unchanged behaviour)
    assert "composite" not in [c["kind"] for p in discover.run_cycle(inv, studies)["revision_proposals"]
                               for c in p["candidate_changes"]]


def test_anomaly_proposal_uses_slug_not_spec_name(tmp_path):
    from discovery_engine.model_revision import propose_model_revisions
    inv = {"studies": ["slug-a"], "hypotheses": [
        {"id": "h1", "study": "slug-a", "status": "open",
         "predictions": [{"observable": "x", "expected": {"op": "range", "low": 0, "high": 1}}]},
        {"id": "h2", "study": "slug-a", "status": "open",
         "predictions": [{"observable": "x", "expected": {"op": "range", "low": 5, "high": 6}}]}]}
    spec = {"name": "different-name", "observables": ["x"],
            "baseline": [{"name": "b", "composite": "a.b.c", "params": {"k": 1}}],
            "findings": [{"id": "f", "evidence": {"measure": "x", "observed": 3.0}}]}
    props = propose_model_revisions(inv, {"slug-a": spec}, {}, None, [], [])
    an = [p for p in props if p["source"]["kind"] == "anomaly"]
    assert an and all(p["source"]["study"] == "slug-a" for p in an)


def test_composite_swap_applies_to_child_study(tmp_path):
    ip = _ws_with_alt(tmp_path)
    _, inv, studies = discover.load_cycle_inputs(tmp_path, "og")
    cyc = discover._cycle_with_evidence(tmp_path, "og", inv, studies)
    prop = next(p for p in cyc["revision_proposals"] if p["source"]["study"] == "s3")
    discover.persist_cycle(ip, inv, cyc, studies=studies)
    ws = ["--workspace", str(tmp_path)]
    assert discover.main([*ws, "revise", "og", prop["id"], "--approve", "--rationale", "swap"]) == 0
    idx = [c["kind"] for c in prop["candidate_changes"]].index("composite")
    assert discover.main([*ws, "revise", "og", prop["id"], "--apply", "--change", str(idx)]) == 0
    done = load_yaml(ip)["revision_proposals"][0]
    child = load_yaml(tmp_path / "studies" / done["history"][-1]["child_study"] / "study.yaml")
    assert any(a.get("composite") == C3D for a in child["baseline"] + child.get("variants", []))
