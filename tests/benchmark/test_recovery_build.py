import yaml

from discovery_engine.benchmark.recovery_build import build_hook, built_study_slug
from discovery_engine.investigation.import_ import investigations_dir, studies_dir


def _ws(tmp_path):
    inv = investigations_dir(tmp_path) / "i"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump({"name": "i", "studies": ["s1"]}))
    return tmp_path


def test_build_hook_contract(tmp_path):
    ws = _ws(tmp_path)
    gap = {"id": "cg:x:1", "mechanism": "feedback_inhibition", "status": "accepted"}
    res = build_hook("feedback_inhibition")(gap, ws)
    assert res["status"] in ("built", "reuse") and res["gap_id"] == gap["id"] and res["process"]
    slug = built_study_slug("feedback_inhibition")
    doc = yaml.safe_load((studies_dir(ws) / slug / "study.yaml").read_text())
    assert doc["implements"] == "feedback_inhibition"
    assert doc["behavior_tests"][0]["measure"]["study"] == slug
    inv = yaml.safe_load((investigations_dir(ws) / "i" / "investigation.yaml").read_text())
    assert slug in inv["studies"]


def test_decoy_still_covers_declared_mechanism(tmp_path):
    ws = _ws(tmp_path)
    gap = {"id": "cg:x:1", "mechanism": "feedback_inhibition", "status": "accepted"}
    build_hook("substrate_depletion")(gap, ws)
    doc = yaml.safe_load((studies_dir(ws) / built_study_slug("feedback_inhibition") / "study.yaml").read_text())
    assert doc["topic"] == "feedback inhibition" and doc["implements"] == "substrate_depletion"
