"""Task 12: apply_revision — approved proposal -> child study + model version + hypotheses."""
from __future__ import annotations

import copy

import pytest

from discovery_engine import discover
from discovery_engine import discovery_engine as de
from discovery_engine import hypotheses as H
from discovery_engine import model_architect as M
from discovery_engine.study_io import load_yaml, save_yaml_atomic

PID = "rp:s3:mv:old:abcd1234"
C2D = "json.tool.main"  # resolves (stdlib), stands in for the 2D composite
C3D = "discovery_engine.model_architect.build_catalog"  # resolves
BAD = "nonexistent_pkg_xyz.mod.build"


def _prop(composite=C3D, status="approved"):
    return {"id": PID, "status": status, "approval": "review",
            "source": {"kind": "model_disagreement", "study": "s3", "finding": "f-s3",
                       "observable": "enclosure_probability"},
            "affected_model_version": "mv:old",
            "candidate_changes": [{"kind": "composite", "replace_with": composite},
                                  {"kind": "param", "name": "membrane_bending", "to": [0.5, 2.0]}],
            "predicted_effects": [{"observable": "enclosure_probability", "direction": "converge"}],
            "discriminating_test": {"behavior_test": "enc_in_band", "pass_if": {"low": 0.3, "high": 0.8}},
            "history": [{"at": "t", "event": "proposed", "by": "engine"},
                        {"at": "t", "event": "approved", "by": "human", "rationale": "ok"}]}


def _ws(tmp_path, composite=C3D, status="approved"):
    (tmp_path / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    inv = tmp_path / "investigations" / "og"
    inv.mkdir(parents=True)
    save_yaml_atomic(inv / "investigation.yaml", {
        "name": "og", "studies": ["s3"], "hypotheses": [{"id": "h-s3", "study": "s3", "status": "open"}],
        "revision_proposals": [_prop(composite, status)]})
    sd = tmp_path / "studies" / "s3"
    sd.mkdir(parents=True)
    save_yaml_atomic(sd / "study.yaml", {
        "name": "s3", "investigation": "og", "schema_version": 4,
        "baseline": [{"name": "baseline", "composite": C2D, "params": {"membrane_bending": 1.0}}],
        "observables": ["enclosure_probability"],
        "behavior_tests": [{"name": "enc_in_band", "measure": {"kind": "final", "path": "enclosure_probability"},
                            "pass_if": {"low": 0.3, "high": 0.8}}],
        "findings": [{"id": "f-s3", "statement": "2D vs 3D disagree",
                      "evidence": {"measure": "enclosure_probability", "from_test": "enc_in_band",
                                   "by_topology": {"2d": 0.12, "3d": 0.71}}}]})
    return inv / "investigation.yaml", sd / "study.yaml"


def test_apply_composite_candidate(tmp_path):
    ip, sp = _ws(tmp_path)
    res = discover.apply_revision(tmp_path, "og", PID)
    assert res["status"] == "applied"
    child = load_yaml(res["study_path"])
    assert child["baseline"][0]["composite"] == C3D
    assert {"study": "s3", "relation": "alternative-to"} in child["pipeline_gate"]["prerequisites"]
    assert child["model_change"]["ref"] == PID
    assert child["observables"] == ["enclosure_probability"] and child["behavior_tests"]
    (stub,) = child["findings"]
    assert stub["evidence"]["from_test"] == "enc_in_band" and stub["statement"] is None

    inv = load_yaml(ip)
    assert res["study"] in inv["studies"]
    new = [h for h in inv["hypotheses"] if h.get("study") == res["study"]]
    assert len(new) == 2 and all(h["origin"] == {"kind": "revision", "ref": PID} for h in new)
    assert all(h["statement"] is None for h in new)
    assert H._discriminating(new[0]["predictions"][0]["expected"], new[1]["predictions"][0]["expected"])
    assert H.discriminating_hypotheses(inv)[res["study"]]["discriminable"]

    (mv,) = [m for m in inv["model_versions"] if m["id"] == res["model_version"]]
    assert mv["parent_ids"] == ["mv:old"] and mv["origin"] == {"kind": "revision", "ref": PID}
    assert mv["composite"] == C3D
    prop = inv["revision_proposals"][0]
    assert prop["status"] == "applied"
    ev = prop["history"][-1]
    assert ev["event"] == "applied" and ev["child_study"] == res["study"] and ev["model_version"] == mv["id"]
    # lineage is REGENERABLE: survives a persist round-trip (bind recomputes parent_ids)
    _, inv_spec, studies = discover.load_cycle_inputs(tmp_path, "og")
    parent_mv = M.bind_model_versions(studies)["s3"]["baseline"]["id"]
    rt = {m["id"]: m for m in discover.project_model_versions(studies, inv_spec.get("model_versions"))}
    assert rt[mv["id"]]["parent_ids"] == [parent_mv]
    assert rt[mv["id"]]["origin"] == {"kind": "revision", "ref": PID}
    discover.persist_cycle(ip, inv_spec, discover.run_cycle(inv_spec, studies), studies=studies)
    again = {m["id"]: m for m in load_yaml(ip)["model_versions"]}
    assert again[mv["id"]]["parent_ids"] == [parent_mv]
    # child is run-ready
    assert discover.run_readiness(child, tmp_path)["ready"]


def test_apply_param_candidate_sweeps(tmp_path):
    _ws(tmp_path)
    res = discover.apply_revision(tmp_path, "og", PID, change_index=1)
    child = load_yaml(res["study_path"])
    assert child["baseline"][0]["composite"] == C2D and child["baseline"][0]["params"]["membrane_bending"] == 0.5
    assert len(child["variants"]) == 2
    assert child["model_change"]["ref"] == PID
    inv = load_yaml(tmp_path / "investigations" / "og" / "investigation.yaml")
    (mv,) = [m for m in inv["model_versions"] if m["id"] == res["model_version"]]
    assert mv["origin"]["kind"] == "revision"
    _, _, studies = discover.load_cycle_inputs(tmp_path, "og")
    assert M.bind_model_versions(studies)[res["study"]]["baseline"]["origin"]["kind"] == "revision"


@pytest.mark.parametrize("idx", [0, 1])
def test_explicit_change_underived_is_needs_build(tmp_path, idx):
    ip, _ = _ws(tmp_path)
    spec = load_yaml(ip)
    spec["revision_proposals"][0]["candidate_changes"] = [
        {"kind": "composite"}, {"kind": "param", "name": "x"}]
    save_yaml_atomic(ip, spec)
    before = ip.read_text()
    res = discover.apply_revision(tmp_path, "og", PID, change_index=idx)
    assert res["status"] == "needs_build" and res["mechanism"]
    assert ip.read_text() == before


def test_apply_build_route_refuses(tmp_path):
    ip, sp = _ws(tmp_path, composite=BAD)
    before = (ip.read_text(), sorted(p.name for p in (tmp_path / "studies").iterdir()))
    res = discover.apply_revision(tmp_path, "og", PID)
    assert res["status"] == "needs_build" and BAD in res["mechanism"]
    assert res["study_path"] is None and res["model_version"] is None
    assert (ip.read_text(), sorted(p.name for p in (tmp_path / "studies").iterdir())) == before
    assert load_yaml(ip)["revision_proposals"][0]["status"] == "approved"


def test_only_approved_applies(tmp_path):
    _ws(tmp_path, status="proposed")
    with pytest.raises(ValueError, match="approved"):
        discover.apply_revision(tmp_path, "og", PID)
    with pytest.raises(SystemExit):
        discover.main(["--workspace", str(tmp_path), "revise", "og", PID, "--apply"])


def test_cli_apply(tmp_path):
    ip, _ = _ws(tmp_path)
    assert discover.main(["--workspace", str(tmp_path), "revise", "og", PID, "--apply"]) == 0
    assert load_yaml(ip)["revision_proposals"][0]["status"] == "applied"


def test_alternative_to_blocks_closure(tmp_path):
    ip, _ = _ws(tmp_path)
    res = discover.apply_revision(tmp_path, "og", PID)
    _, inv_spec, studies = discover.load_cycle_inputs(tmp_path, "og")
    cyc = discover.run_cycle(inv_spec, studies)
    assert any(e["type"] == "challenge" and e["to"] == f"claim:{res['study']}"
               for e in cyc["argument"]["edges"])
    assert de.closure(cyc, inv_spec)["closed"] is False
    assert de.closure(cyc, inv_spec)["open_challenges"] >= 1


def test_invalidate_marks_parent_review(tmp_path):
    ip, sp = _ws(tmp_path)
    parent_before = sp.read_text()
    res = discover.apply_revision(tmp_path, "og", PID)
    arts = {(a["artifact"], a["status"]) for a in res["invalidated"]}
    assert ("hypothesis:h-s3", "REVIEW_REQUIRED") in arts and ("claim:s3", "REVIEW_REQUIRED") in arts
    assert sp.read_text() == parent_before  # parent never edited
    inv = load_yaml(ip)
    h = next(h for h in inv["hypotheses"] if h["id"] == "h-s3")
    assert h == {"id": "h-s3", "study": "s3", "status": "open"}
