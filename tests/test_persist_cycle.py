"""Task 6: persist_cycle writes schema-valid model_versions[]."""
from __future__ import annotations

import json
from pathlib import Path

import jsonschema

from discovery_engine import discover
from discovery_engine.study_io import load_yaml, save_yaml_atomic

C = "discovery_engine.model_architect.build_catalog"
SCHEMA = Path(discover.__file__).parent / "schemas" / "investigation-extensions.schema.json"


def _studies():
    return {
        "a": {"name": "a", "claim": "a", "baseline": [{"name": "baseline", "composite": C, "params": {"d": 1}}],
              "behavior_tests": [{"name": "t1"}],
              "runs": [{"name": "r1", "status": "completed", "outcomes": {"t1": {"result": "PASS"}}}]},
        "b": {"name": "b", "seeded_from": {"study": "a"},
              "baseline": [{"name": "baseline", "composite": C, "params": {"d": 2}}]},
    }


def test_persist_writes_model_versions_valid(tmp_path):
    p = tmp_path / "investigation.yaml"
    inv = {"question": "Q?", "studies": ["a", "b"]}
    save_yaml_atomic(p, inv)
    studies = _studies()
    out = discover.run_cycle(inv, studies)
    state = discover.persist_cycle(p, inv, out, studies=studies)
    disk = load_yaml(p)
    mvs = disk["model_versions"]
    assert len(mvs) == 2
    schema = json.loads(SCHEMA.read_text())
    jsonschema.validate({"model_versions": json.loads(json.dumps(mvs))}, schema)
    by = {m["bound_to"][0]["study"]: m for m in mvs}
    assert by["b"]["parent_ids"] == [by["a"]["id"]]
    assert by["a"]["resolves"] is True
    assert sorted(state["model_versions"]) == sorted(m["id"] for m in mvs)


def test_origin_written_once_and_rebind_regenerates(tmp_path):
    p = tmp_path / "investigation.yaml"
    inv = {"question": "Q?", "studies": ["a"]}
    studies = {"a": _studies()["a"]}
    save_yaml_atomic(p, inv)
    out = discover.run_cycle(inv, studies)
    discover.persist_cycle(p, inv, out, studies=studies)
    disk = load_yaml(p)
    disk["model_versions"][0]["origin"] = {"kind": "revision", "ref": "rp:x"}
    disk["model_versions"][0]["bound_to"] = []  # stale projection
    save_yaml_atomic(p, disk)
    discover.persist_cycle(p, inv, out, studies=studies)
    mv = load_yaml(p)["model_versions"][0]
    assert mv["origin"] == {"kind": "revision", "ref": "rp:x"}
    assert mv["bound_to"] == [{"study": "a", "arm": "baseline"}]


def test_persist_without_studies_leaves_model_versions_alone(tmp_path):
    p = tmp_path / "investigation.yaml"
    inv = {"question": "Q?", "studies": []}
    save_yaml_atomic(p, inv)
    discover.persist_cycle(p, inv, discover.run_cycle(inv, {}))
    assert "model_versions" not in load_yaml(p)


def test_parent_ids_regenerated_when_lineage_changes(tmp_path):
    p = tmp_path / "investigation.yaml"
    inv = {"question": "Q?", "studies": ["a", "c", "b"]}
    save_yaml_atomic(p, inv)
    studies = _studies()
    studies["c"] = {"name": "c", "baseline": [{"name": "baseline", "composite": C, "params": {"d": 3}}]}
    discover.persist_cycle(p, inv, discover.run_cycle(inv, studies), studies=studies)
    ids = {m["bound_to"][0]["study"]: m["id"] for m in load_yaml(p)["model_versions"]}
    assert [m for m in load_yaml(p)["model_versions"] if m["id"] == ids["b"]][0]["parent_ids"] == [ids["a"]]
    studies["b"]["seeded_from"] = {"study": "c"}
    discover.persist_cycle(p, inv, discover.run_cycle(inv, studies), studies=studies)
    mv = [m for m in load_yaml(p)["model_versions"] if m["id"] == ids["b"]][0]
    assert mv["parent_ids"] == [ids["c"]]
