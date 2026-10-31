"""Missing member studies must surface, not vanish silently."""
from discovery_engine import discover
from discovery_engine.study_io import save_yaml_atomic


def _ws(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    inv = tmp_path / "investigations" / "inv"
    inv.mkdir(parents=True)
    save_yaml_atomic(inv / "investigation.yaml",
                     {"name": "inv", "studies": ["present", "ghost"]})
    sd = tmp_path / "studies" / "present"
    sd.mkdir(parents=True)
    save_yaml_atomic(sd / "study.yaml", {"name": "present", "claim": "c"})
    return tmp_path


def test_missing_members_returns_slug_without_study_yaml(tmp_path):
    ws = _ws(tmp_path)
    assert discover.missing_members(ws, "inv") == ["ghost"]
    # load_cycle_inputs arity is unchanged
    assert len(discover.load_cycle_inputs(ws, "inv")) == 3


def test_run_cycle_surfaces_missing_member_as_validation_finding(tmp_path):
    ws = _ws(tmp_path)
    _, inv_spec, studies = discover.load_cycle_inputs(ws, "inv")
    recs = [r for r in discover.run_cycle(inv_spec, studies)["validations"]
            if r["provenance"]["validator_id"] == "structural.investigation.members"]
    assert len(recs) == 1
    r = recs[0]
    assert r["surfaces"] == "frontier"
    assert r["result"]["execution_status"] == "failed"
    assert r["result"]["scientific_judgment"] is None
    assert "ghost" in r["target"] and "ghost" in " ".join(r["findings"])


def test_cli_prints_cycle_state(tmp_path, capsys):
    ws = _ws(tmp_path)
    discover.main(["--workspace", str(ws), "cycle", "inv"])
    out = capsys.readouterr().out
    assert "persisted cycle_state" in out
    assert "knowledge_cycle" not in out
