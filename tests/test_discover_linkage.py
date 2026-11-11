"""Tests for the relocated linkage modules + the `discover linkage` CLI verb.

`investigation_members` + `linkage_index` moved from vivarium-workbench into the
engine (Phase 3 of the engine-native-skills migration). These assert the modules
are engine-pure (no workbench import) and the CLI emits the build_index payload.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

from discovery_engine import discover
from discovery_engine.investigation.linkage import build_index
from discovery_engine.investigation.members import investigation_member_slugs, member_slug

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "lint-cases"


def _ws(tmp_path: Path) -> tuple[Path, list[str]]:
    dest = tmp_path / "ws"
    shutil.copytree(FIXTURES / "clean-baseline", dest)
    return dest, ["--workspace", str(dest)]


# --- investigation_members (studies|members back-compat + normalization) -----


def test_member_slugs_prefers_studies_then_members():
    assert investigation_member_slugs({"studies": ["a", "b"]}) == ["a", "b"]
    assert investigation_member_slugs({"members": ["c"]}) == ["c"]
    # studies wins when both present (authoritative pre-migration list)
    assert investigation_member_slugs({"studies": ["a"], "members": ["z"]}) == ["a"]
    assert investigation_member_slugs({}) == []
    assert investigation_member_slugs("not a dict") == []


def test_member_slug_normalizes_string_and_dict():
    assert member_slug("x") == "x"
    assert member_slug({"study": "s"}) == "s"
    assert member_slug({"slug": "s2"}) == "s2"
    assert member_slug({"name": "s3"}) == "s3"
    assert member_slug(123) is None


# --- build_index + discover linkage ------------------------------------------


def test_build_index_shape(tmp_path):
    ws, _ = _ws(tmp_path)
    index = build_index(ws)
    assert set(index) >= {"nodes", "edges"}
    assert isinstance(index["nodes"], list)
    assert isinstance(index["edges"], list)


def test_discover_linkage_json_matches_build_index(tmp_path, capsys):
    ws, argv = _ws(tmp_path)
    rc = discover.main([*argv, "linkage", "--json"])
    assert rc == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed == build_index(ws)


def test_discover_linkage_human_summary(tmp_path, capsys):
    ws, argv = _ws(tmp_path)
    rc = discover.main([*argv, "linkage"])
    assert rc == 0
    assert "linkage graph:" in capsys.readouterr().out
