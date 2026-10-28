"""Tests for investigation canonicalization (studies: -> members:)."""
from discovery_engine.investigation_canonicalize import canonicalize_investigation, migrate_investigation_file


def test_studies_renamed_to_members():
    spec = {"name": "inv", "studies": ["a", "b"]}
    report = canonicalize_investigation(spec)
    assert spec["members"] == ["a", "b"] and "studies" not in spec
    assert report["changed"] is True


def test_members_already_present_is_noop():
    spec = {"name": "inv", "members": ["a"]}
    report = canonicalize_investigation(spec)
    assert report["changed"] is False and spec["members"] == ["a"]


def test_both_keys_flagged():
    spec = {"name": "inv", "members": ["a"], "studies": ["a", "b"]}
    report = canonicalize_investigation(spec)
    assert "both_keys_present" in report["flags"] and "studies" in spec


def test_file_dry_run_byte_identical(tmp_path):
    d = tmp_path / "inv"
    d.mkdir()
    (d / "investigation.yaml").write_text("# keep\nname: inv\nstudies:\n- a\n- b\n")
    before = (d / "investigation.yaml").read_text()
    migrate_investigation_file(d, write=False)
    assert (d / "investigation.yaml").read_text() == before


def test_file_write_preserves_comments(tmp_path):
    d = tmp_path / "inv"
    d.mkdir()
    (d / "investigation.yaml").write_text("# KEEPME\nname: inv\nstudies:\n- a\n- b\n")
    migrate_investigation_file(d, write=True)
    text = (d / "investigation.yaml").read_text()
    assert "KEEPME" in text and "members:" in text and "studies:" not in text
