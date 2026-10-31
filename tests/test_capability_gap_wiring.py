"""Task 2: capability gaps wired into the discovery cycle (mirrors the viz wiring tests)."""
import pytest
import yaml

from discovery_engine import discover as D
from discovery_engine import discovery_engine as de
from discovery_engine.investigation_import import investigations_dir, studies_dir

MECH = "plasmid replication"


def _fixture(tmp_path, monkeypatch, *, builds=None):
    ws = tmp_path
    inv = investigations_dir(ws) / "inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump(
        {"name": "inv", "slug": "inv", "studies": ["s1"], "required_mechanisms": [MECH]}))
    sd = studies_dir(ws) / "s1"
    sd.mkdir(parents=True)
    spec = {"name": "s1", "topic": "growth", "baseline": {"composite": "pkg.mod.GrowthComposite"}}
    if builds:
        spec["builds"] = [builds]
    (sd / "study.yaml").write_text(yaml.safe_dump(spec))
    monkeypatch.setattr(D, "_load_runs_by_study", lambda w, i, s: {k: [] for k in s})
    return ws


def _persist(ws):
    inv_path, inv, st = D.load_cycle_inputs(ws, "inv")
    res = D._cycle_with_evidence(ws, "inv", inv, st)
    D.persist_cycle(inv_path, inv, res, studies=st)
    return yaml.safe_load(inv_path.read_text()), res


def _gaps(ws):
    return yaml.safe_load((investigations_dir(ws) / "inv" / "investigation.yaml").read_text()).get("capability_gaps") or []


def _hook(gap, ws_root):
    # Register a study whose composite genuinely COVERS the mechanism, then reuse it.
    sd = studies_dir(ws_root) / "s2"
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "study.yaml").write_text(yaml.safe_dump(
        {"name": "s2", "topic": MECH, "baseline": {"composite": "pkg.mod.PlasmidReplication"}}))
    ip = investigations_dir(ws_root) / "inv" / "investigation.yaml"
    d = yaml.safe_load(ip.read_text())
    d["studies"] = ["s1", "s2"]
    ip.write_text(yaml.safe_dump(d))
    return {"status": "reuse", "process": "pkg.mod.PlasmidReplication", "gap_id": gap["id"], "detail": "fake"}


def test_cycle_writes_gaps_idempotent(tmp_path, monkeypatch):
    ws = _fixture(tmp_path, monkeypatch)
    d1, res = _persist(ws)
    assert len(d1["capability_gaps"]) == 1 and d1["capability_gaps"][0]["status"] == "proposed"
    assert res["capability_gaps"][0]["id"] == d1["capability_gaps"][0]["id"]
    d2, _ = _persist(ws)
    assert d2["capability_gaps"] == d1["capability_gaps"]          # no dup, history untouched


def test_real_catalog_gap_is_needs_author_unless_builds(tmp_path, monkeypatch):
    ws = _fixture(tmp_path, monkeypatch)
    assert _persist(ws)[0]["capability_gaps"][0]["derivable"] is False
    ws2 = _fixture(tmp_path / "b", monkeypatch, builds=MECH)
    assert _persist(ws2)[0]["capability_gaps"][0]["derivable"] is True


def test_accept_reject_wrappers(tmp_path, monkeypatch):
    ws = _fixture(tmp_path, monkeypatch)
    cg = _persist(ws)[0]["capability_gaps"][0]["id"]
    with pytest.raises(ValueError):
        D.accept_capability_gap(ws, "inv", cg, rationale="  ")
    D.accept_capability_gap(ws, "inv", cg, rationale="needed for the question")
    g = _gaps(ws)[0]
    assert g["status"] == "accepted" and g["accept"]["rationale"] == "needed for the question"
    with pytest.raises(ValueError):
        D.reject_capability_gap(ws, "inv", cg, rationale="x")       # no longer proposed
    with pytest.raises(ValueError):
        D.accept_capability_gap(ws, "inv", "cg:nope", rationale="x")


def test_reject_is_terminal_never_reemitted(tmp_path, monkeypatch):
    ws = _fixture(tmp_path, monkeypatch)
    cg = _persist(ws)[0]["capability_gaps"][0]["id"]
    D.reject_capability_gap(ws, "inv", cg, rationale="out of scope")
    d, res = _persist(ws)
    assert d["capability_gaps"][0]["status"] == "rejected" and len(d["capability_gaps"]) == 1
    _, inv, _ = D.load_cycle_inputs(ws, "inv")
    assert not [i for i in de.frontier(res, inv) if i["kind"] == "capability_gap"]


def test_execute_builds_accepted_gap_with_fake_hook(tmp_path, monkeypatch):
    ws = _fixture(tmp_path, monkeypatch)
    cg = _persist(ws)[0]["capability_gaps"][0]["id"]
    D.accept_capability_gap(ws, "inv", cg, rationale="needed")
    rec = D.execute_action(ws, "inv", autonomous=True, target=cg, build_hook=_hook)
    assert rec["status"] == "executed", rec
    g = _gaps(ws)[0]
    assert g["status"] == "built" and g["built"]["process"] == "pkg.mod.PlasmidReplication"


def test_execute_does_not_build_unaccepted_or_hookless(tmp_path, monkeypatch):
    ws = _fixture(tmp_path, monkeypatch)
    cg = _persist(ws)[0]["capability_gaps"][0]["id"]
    calls = []
    rec = D.execute_action(ws, "inv", autonomous=True, target=cg, build_hook=lambda g, w: calls.append(g))
    assert rec["status"] == "gated-human" and calls == [] and _gaps(ws)[0]["status"] == "proposed"
    D.accept_capability_gap(ws, "inv", cg, rationale="needed")
    rec = D.execute_action(ws, "inv", autonomous=True, target=cg)   # no hook anywhere
    assert rec["status"] == "gated-human" and _gaps(ws)[0]["status"] == "accepted"


def test_needs_author_and_no_rationale_are_gated(tmp_path, monkeypatch):
    ws = _fixture(tmp_path, monkeypatch)                            # needs_author
    cg = _persist(ws)[0]["capability_gaps"][0]["id"]
    rec = D.execute_action(ws, "inv", autonomous=True, target=cg, decider=lambda *a: {"choice": "accept", "rationale": "ok"})
    assert rec["status"] == "gated-human" and _gaps(ws)[0]["status"] == "proposed"
    ws2 = _fixture(tmp_path / "b", monkeypatch, builds=MECH)        # derivable
    cg2 = _persist(ws2)[0]["capability_gaps"][0]["id"]
    rec = D.execute_action(ws2, "inv", autonomous=True, target=cg2, decider=lambda *a: {"choice": "accept", "rationale": ""})
    assert rec["status"] == "gated-human" and _gaps(ws2)[0]["status"] == "proposed"
    rec = D.execute_action(ws2, "inv", autonomous=True, target=cg2, decider=lambda *a: {"choice": "accept", "rationale": "ok"})
    assert rec["status"] == "executed" and _gaps(ws2)[0]["status"] == "accepted"   # accepted, NOT built


def test_persist_closes_accepted_gap_when_process_present(tmp_path, monkeypatch):
    ws = _fixture(tmp_path, monkeypatch)
    cg = _persist(ws)[0]["capability_gaps"][0]["id"]
    D.accept_capability_gap(ws, "inv", cg, rationale="needed")
    sp = studies_dir(ws) / "s1" / "study.yaml"
    spec = yaml.safe_load(sp.read_text())
    spec["baseline"] = {"composite": "pkg.mod.plasmid_replication"}
    sp.write_text(yaml.safe_dump(spec))
    d, _ = _persist(ws)
    assert d["capability_gaps"][0]["status"] == "built"
    assert d["capability_gaps"][0]["built"]["process"] == "pkg.mod.plasmid_replication"


def test_cli_gap_verb(tmp_path, monkeypatch, capsys):
    ws = _fixture(tmp_path, monkeypatch)
    assert D.main(["--workspace", str(ws), "gap", "inv", "--propose"]) == 0
    cg = _gaps(ws)[0]["id"]
    assert cg in capsys.readouterr().out
    assert D.main(["--workspace", str(ws), "gap", "inv", "--accept", cg, "--rationale", "yes"]) == 0
    assert _gaps(ws)[0]["status"] == "accepted"


def _write_gaps(ws, mutate):
    p = investigations_dir(ws) / "inv" / "investigation.yaml"
    d = yaml.safe_load(p.read_text())
    mutate(d["capability_gaps"][0])
    p.write_text(yaml.safe_dump(d))


@pytest.mark.parametrize("bad", [{"id": "vp:wrong"}, {"status": "bogus"}])
def test_persist_rejects_malformed_gap(tmp_path, monkeypatch, bad):
    import jsonschema
    ws = _fixture(tmp_path, monkeypatch)
    _, res = _persist(ws)
    res["capability_gaps"] = [{**res["capability_gaps"][0], "id": "cg:inv:zz", **bad}]
    p = investigations_dir(ws) / "inv" / "investigation.yaml"
    d = yaml.safe_load(p.read_text())
    d.pop("capability_gaps")
    p.write_text(yaml.safe_dump(d))
    inv_path, inv, st = D.load_cycle_inputs(ws, "inv")
    with pytest.raises(jsonschema.ValidationError):
        D.persist_cycle(inv_path, inv, res, studies=st)


def test_non_autonomous_never_builds_accepted_gap(tmp_path, monkeypatch):
    ws = _fixture(tmp_path, monkeypatch)
    cg = _persist(ws)[0]["capability_gaps"][0]["id"]
    D.accept_capability_gap(ws, "inv", cg, rationale="needed")
    calls = []
    monkeypatch.setattr(D, "load_workspace_build_hook", lambda w: (lambda g, r: calls.append(g)))
    rec = D.execute_action(ws, "inv", autonomous=False, target=cg)
    assert rec["status"] == "gated-human" and calls == [] and _gaps(ws)[0]["status"] == "accepted"


def test_hand_edited_accepted_gap_without_rationale_is_gated(tmp_path, monkeypatch):
    ws = _fixture(tmp_path, monkeypatch)
    cg = _persist(ws)[0]["capability_gaps"][0]["id"]
    _write_gaps(ws, lambda g: g.update(status="accepted"))        # no accept.rationale
    calls = []
    rec = D.execute_action(ws, "inv", autonomous=True, target=cg, build_hook=lambda g, r: calls.append(g))
    assert rec["status"] == "gated-human" and calls == [] and _gaps(ws)[0]["status"] == "accepted"


def test_derivable_proposed_gap_without_decider_is_gated(tmp_path, monkeypatch):
    ws = _fixture(tmp_path, monkeypatch, builds=MECH)
    cg = _persist(ws)[0]["capability_gaps"][0]["id"]
    calls = []
    rec = D.execute_action(ws, "inv", autonomous=True, target=cg, build_hook=lambda g, r: calls.append(g))
    assert rec["status"] == "gated-human" and calls == [] and _gaps(ws)[0]["status"] == "proposed"


def test_cli_accept_and_reject_together_is_an_error(tmp_path, monkeypatch):
    ws = _fixture(tmp_path, monkeypatch)
    cg = _persist(ws)[0]["capability_gaps"][0]["id"]
    with pytest.raises(SystemExit):
        D.main(["--workspace", str(ws), "gap", "inv", "--accept", cg, "--reject", cg, "--rationale", "x"])
    assert _gaps(ws)[0]["status"] == "proposed"
