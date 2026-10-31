"""Task 8: propose_model_revisions — the typed feedback object (pure)."""
from __future__ import annotations

import copy

from discovery_engine import evaluation as E
from discovery_engine import hypotheses as H
from discovery_engine import model_architect as M
from discovery_engine.model_revision import propose_model_revisions

C2D = "viva_x.composites.membrane_2d.build"
C3D = "viva_x.composites.membrane_3d.build"
NOW = "2026-10-30T00:00:00Z"


def _studies(finding_ev=None, status=None):
    ev = finding_ev or {"measure": "enclosure_probability", "by_topology": {"2d": 0.12, "3d": 0.71},
                        "from_test": "enclosure_prob_in_band"}
    return {"s3": {
        "name": "s3", "topic": "membrane", "fidelity": "reduced",
        "observables": ["enclosure_probability"],
        "baseline": [{"name": "baseline", "composite": C2D, "params": {"membrane_bending": 1.0}}],
        "behavior_tests": [{"name": "enclosure_prob_in_band", "pass_if": {"low": 0.3, "high": 0.8}}],
        "findings": [{"id": "f-s3", "evidence": ev}],
    }}


def _catalog(resolves=True):
    return [
        {"id": "membrane_2d", "composite": C2D, "fidelity": "reduced", "fidelities": ["reduced"],
         "capability": ["membrane", "enclosure"], "observables": ["enclosure_probability"], "resolves": True},
        {"id": "membrane_3d", "composite": C3D, "fidelity": "mesh", "fidelities": ["mesh"],
         "capability": ["membrane", "enclosure"], "observables": ["enclosure_probability"], "resolves": resolves},
    ]


def _inv(extra=None):
    return {"studies": ["s3"], "hypotheses": [], **(extra or {})}


def _mvs(studies):
    out = {}
    for slug, arms in M.bind_model_versions(studies).items():
        for arm, mv in arms.items():
            row = out.setdefault(mv["id"], {**mv, "bound_to": []})
            row["bound_to"].append({"study": slug, "arm": arm})
    return list(out.values())


def _run(studies, inv=None, catalog=None, ev_state=None):
    inv = inv or _inv()
    return propose_model_revisions(inv, studies, E.compare_hypotheses(inv, studies, ev_state),
                                   ev_state or {}, _mvs(studies),
                                   _catalog() if catalog is None else catalog, now=NOW)


def test_s3_disagreement_yields_one_full_proposal():
    s = _studies()
    ps = _run(s)
    assert len(ps) == 1
    p = ps[0]
    mv = _mvs(s)[0]["id"]
    assert p["affected_model_version"] == mv
    assert p["id"].startswith(f"rp:s3:{mv}:") and len(p["id"].rsplit(":", 1)[1]) == 8
    assert p["status"] == "proposed" and p["approval"] == "review"
    assert p["source"]["kind"] == "model_disagreement" and p["source"]["finding"] == "f-s3"
    assert p["source"]["observable"] == "enclosure_probability"
    kinds = {c["kind"]: c for c in p["candidate_changes"]}
    assert kinds["composite"]["replace_with"] == C3D
    assert kinds["param"]["name"] == "membrane_bending" and kinds["param"]["from"] == 1.0
    assert p["discriminating_test"]["behavior_test"] == "enclosure_prob_in_band"
    assert p["discriminating_test"]["pass_if"] == {"low": 0.3, "high": 0.8}
    assert p["predicted_effects"][0]["direction"] == "converge"
    assert p["history"] == [{"at": NOW, "event": "proposed", "by": "engine"}]
    assert p["evaluated"] is None


def test_no_composite_candidate_if_unresolved_or_not_higher():
    ps = _run(_studies(), catalog=_catalog(resolves=False))
    assert "composite" not in {c["kind"] for c in ps[0]["candidate_changes"]}
    ps = _run(_studies(), catalog=[_catalog()[0]])
    assert "composite" not in {c["kind"] for c in ps[0]["candidate_changes"]}


def test_contradicted_finding_gets_no_proposal():
    s = _studies()
    ev_state = {"ledger": [{"study": "s3", "finding": "f-s3", "status": "contradicted"}],
                "by_study": {"s3": {"status": "contradicted"}}}
    assert _run(s, ev_state=ev_state) == []


def test_deterministic_id_pure_and_changes_with_values():
    s = _studies()
    snap = copy.deepcopy(s)
    a, b = _run(s), _run(s)
    assert a == b and s == snap
    s2 = _studies({"measure": "enclosure_probability", "by_topology": {"2d": 0.10, "3d": 0.71},
                   "from_test": "enclosure_prob_in_band"})
    assert _run(s2)[0]["id"] != a[0]["id"]


def _anomaly_inv():
    return _inv({"hypotheses": [
        {"id": "ha", "study": "s3", "predictions": [{"observable": "enclosure_probability",
                                                      "expected": {"low": 0.0, "high": 0.2}}]},
        {"id": "hb", "study": "s3", "predictions": [{"observable": "enclosure_probability",
                                                      "expected": {"low": 0.8, "high": 1.0}}]}]})


def test_anomaly_mechanism_is_needs_author_never_filled():
    s = _studies({"measure": "enclosure_probability", "observed": 0.5, "from_test": "enclosure_prob_in_band"})
    inv = _anomaly_inv()
    assert H.anomalies(inv, list(s.values()))
    ps = _run(s, inv=inv)
    assert len(ps) == 1 and ps[0]["source"]["kind"] == "anomaly"
    mech = [c for c in ps[0]["candidate_changes"] if c["kind"] == "mechanism"]
    assert len(mech) == 1 and mech[0]["needs_author"] is True and mech[0]["question"]
    assert set(mech[0]) == {"kind", "add", "needs_author", "question"} and mech[0]["add"] is None
    assert any(c["kind"] == "param" for c in ps[0]["candidate_changes"])


def test_cap_one_per_study_highest_spread_first():
    s = _studies()
    s["s3"]["findings"].append({"id": "f-big", "evidence": {
        "measure": "other_obs", "by_topology": {"2d": 0.0, "3d": 0.95}}})
    ps = _run(s)
    assert len(ps) == 1 and ps[0]["source"]["finding"] == "f-big"


def test_rejected_not_reemitted_while_unchanged():
    s = _studies()
    pid = _run(s)[0]["id"]
    inv = _inv({"revision_proposals": [{"id": pid, "status": "rejected"}]})
    assert _run(s, inv=inv) == []
    inv = _inv({"revision_proposals": [{"id": pid, "status": "proposed"}]})
    assert [p["id"] for p in _run(s, inv=inv)] == [pid]
    s2 = _studies({"measure": "enclosure_probability", "by_topology": {"2d": 0.05, "3d": 0.71},
                   "from_test": "enclosure_prob_in_band"})
    assert len(_run(s2, inv=_inv({"revision_proposals": [{"id": pid, "status": "rejected"}]}))) == 1


def test_contested_gives_param_only_never_retire():
    s = _studies({"measure": "enclosure_probability", "observed": 0.5, "from_test": "enclosure_prob_in_band"})
    inv = _inv()
    mv = _mvs(s)[0]["id"]
    ev = {"s3": {"status": "contested", "observable": "enclosure_probability", "model_version": mv,
                 "hypotheses": [{"id": "ha", "support": {"net": -2}}]}}
    ps = propose_model_revisions(inv, s, ev, {}, _mvs(s), _catalog(), now=NOW)
    assert len(ps) == 1 and ps[0]["source"]["kind"] == "contested"
    assert [c["kind"] for c in ps[0]["candidate_changes"]] == ["param"]


def test_not_higher_fidelity_resolving_alternative_gives_no_composite():
    cat = _catalog()
    cat[1]["fidelity"], cat[1]["fidelities"] = "reduced", ["reduced"]  # equal rank, resolves
    cat.append({"id": "m_heur", "composite": "viva_x.composites.m_heur.build", "fidelity": "heuristic",
                "fidelities": ["heuristic"], "capability": ["membrane"], "observables": ["enclosure_probability"],
                "resolves": True})  # lower rank, resolves
    assert "composite" not in {c["kind"] for c in _run(_studies(), catalog=cat)[0]["candidate_changes"]}


def test_capability_mismatch_gives_no_composite():
    cat = _catalog()
    cat[1].update(capability=["glycolysis"], observables=["flux"])
    assert "composite" not in {c["kind"] for c in _run(_studies(), catalog=cat)[0]["candidate_changes"]}


def test_disagreement_and_contested_emit_no_mechanism():
    assert all(c["kind"] != "mechanism" for c in _run(_studies())[0]["candidate_changes"])
    s = _studies({"measure": "enclosure_probability", "observed": 0.5})
    mv = _mvs(s)[0]["id"]
    ev = {"s3": {"status": "contested", "observable": "enclosure_probability", "model_version": mv,
                 "hypotheses": [{"id": "ha", "support": {"net": -2}}]}}
    p = propose_model_revisions(_inv(), s, ev, {}, _mvs(s), _catalog(), now=NOW)[0]
    assert all(c["kind"] != "mechanism" for c in p["candidate_changes"])


def test_evaluation_status_contradicted_no_proposal():
    s = _studies()
    inv = _inv()
    ev = E.compare_hypotheses(inv, s, None)
    ev["s3"] = {**ev.get("s3", {}), "status": "contradicted"}
    assert propose_model_revisions(inv, s, ev, {}, _mvs(s), _catalog(), now=NOW) == []


def test_by_study_contradicted_no_proposal_even_without_finding_match():
    s = _studies()
    assert _run(s, ev_state={"by_study": {"s3": {"status": "contradicted"}}}) == []
    s2 = _studies({"measure": "other", "by_topology": {"a": 0.0, "b": 0.9}})
    assert _run(s2, ev_state={"ledger": [{"study": "s3", "finding": "zzz", "status": "contradicted"}]}) == []


def test_cap_cross_kind_disagreement_beats_anomaly():
    # one study, two different-kind triggers: rule = kind priority disagreement > anomaly > contested
    s = _studies({"measure": "enclosure_probability", "observed": 0.5, "from_test": "enclosure_prob_in_band"})
    s["s3"]["findings"].append({"id": "f-dis", "evidence": {"measure": "other_obs",
                                                           "by_topology": {"2d": 0.3, "3d": 0.6}}})
    inv = _anomaly_inv()
    assert H.anomalies(inv, list(s.values()))
    ps = _run(s, inv=inv)
    assert len(ps) == 1 and ps[0]["source"]["kind"] == "model_disagreement"


def test_discriminating_test_pass_if_is_a_copy():
    s = _studies()
    p = _run(s)[0]
    p["discriminating_test"]["pass_if"]["low"] = 99
    assert s["s3"]["behavior_tests"][0]["pass_if"]["low"] == 0.3
