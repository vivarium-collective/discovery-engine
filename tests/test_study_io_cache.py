"""study_io's per-process mtime-keyed YAML parse cache.

A single investigation roll-up loads each member study.yaml many times; caching the parse
(keyed by path + mtime) parses each file once per request. These pin the correctness
properties: deep-copy isolation, mtime invalidation, write-eviction, and that the two public
loaders keep their old contracts (lenient vs mapping-asserting; missing-file raises)."""
import time

import pytest

from discovery_engine.study import io as study_io
def test_load_yaml_deep_copy_isolation(tmp_path):
    f = tmp_path / "s.yaml"
    f.write_text("name: a\nruns: [{name: r1}]\n")
    a = study_io.load_yaml(f)
    a["x"] = 1
    a["runs"][0]["name"] = "mutated"
    b = study_io.load_yaml(f)
    assert "x" not in b and b["runs"][0]["name"] == "r1"


def test_mtime_invalidation(tmp_path):
    f = tmp_path / "s.yaml"
    f.write_text("v: 1\n")
    assert study_io.load_yaml(f)["v"] == 1
    time.sleep(0.01)
    f.write_text("v: 2\n")
    assert study_io.load_yaml(f)["v"] == 2


def test_atomic_write_evicts_cache(tmp_path):
    f = tmp_path / "s.yaml"
    study_io.save_yaml_atomic(f, {"v": 1})
    assert study_io.load_yaml(f)["v"] == 1
    study_io.save_yaml_atomic(f, {"v": 2})      # same-tick write must not serve a stale parse
    assert study_io.load_yaml(f)["v"] == 2


def test_load_yaml_mapping_still_raises_on_non_mapping(tmp_path):
    f = tmp_path / "list.yaml"
    f.write_text("- a\n- b\n")
    with pytest.raises(ValueError):
        study_io.load_yaml_mapping(f)
    # empty file is a lenient {} for both
    e = tmp_path / "e.yaml"
    e.write_text("")
    assert study_io.load_yaml(e) == {} and study_io.load_yaml_mapping(e) == {}


def test_missing_file_still_raises(tmp_path):
    with pytest.raises(OSError):
        study_io.load_yaml(tmp_path / "nope.yaml")
