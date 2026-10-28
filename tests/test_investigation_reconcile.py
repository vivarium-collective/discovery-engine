"""Tests for investigation_reconcile.detect_drift / apply_mechanical.

detect_drift is the deterministic (AI-free) detector of drift after a structural
edit to an investigation (studies removed / added / merged); apply_mechanical
drops the structured references, leaving prose for the agent.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from discovery_engine import investigation_reconcile as IR


def _mk(ws: Path, inv: str, *, studies_key="studies", declared=(), on_disk=(), narrative=None):
    """Build a tmp workspace: workspace.yaml + investigations/<inv>/investigation.yaml
    (declared members under `studies_key`) + flat studies/<slug>/ with a back-ref."""
    (ws / "workspace.yaml").write_text("name: tmp\n")
    inv_dir = ws / "investigations" / inv
    inv_dir.mkdir(parents=True, exist_ok=True)
    spec = {"name": inv, studies_key: list(declared)}
    if narrative:
        spec.update(narrative)
    (inv_dir / "investigation.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
    for slug in on_disk:
        sd = ws / "studies" / slug
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "study.yaml").write_text(yaml.safe_dump({"name": slug, "investigation": inv}))
    return inv_dir / "investigation.yaml"


def _kinds(items):
    return sorted((i.kind, i.where) for i in items)


def test_detects_removed_member_listed_in_studies_key(tmp_path):
    # uses `studies:` (not `members:`) — the pre-canonicalization dogfood shape
    _mk(tmp_path, "inv", studies_key="studies", declared=["a", "b"], on_disk=["a"])
    drift = IR.detect_drift(tmp_path, "inv")
    assert any(d.kind == "member_missing_on_disk" and "b" in d.detail for d in drift)
    assert not any(d.kind == "orphan_on_disk" for d in drift)


def test_detects_orphan_study_on_disk(tmp_path):
    _mk(tmp_path, "inv", declared=["a"], on_disk=["a", "c"])
    drift = IR.detect_drift(tmp_path, "inv")
    assert any(d.kind == "orphan_on_disk" and "c" in d.detail for d in drift)


def test_prose_ref_to_removed_study_in_evidence_for(tmp_path):
    _mk(tmp_path, "inv", declared=["a", "gone"], on_disk=["a"],
        narrative={"scientific_argument": {"evidence_for": ["gone: it showed a thing",
                                                            "a: fine"]},
                   "executive": {"verdict": "driven by gone and a"}})
    drift = IR.detect_drift(tmp_path, "inv")
    prose = [d for d in drift if d.kind == "prose_ref_to_removed"]
    wheres = {d.where for d in prose}
    assert any("evidence_for" in w for w in wheres)
    assert any("verdict" in w for w in wheres)


def test_prose_match_is_word_boundary_no_substring_false_positive(tmp_path):
    # removed slug 'demo' must NOT match inside 'demographics'
    _mk(tmp_path, "inv", declared=["a", "demo"], on_disk=["a"],
        narrative={"biological_story": "demographics of the population improved"})
    drift = IR.detect_drift(tmp_path, "inv")
    assert not any(d.kind == "prose_ref_to_removed" and "biological_story" in d.where
                   for d in drift)


def test_missing_at_a_glance_and_acceptance_is_not_an_error(tmp_path):
    # no at_a_glance / acceptance_criteria / glossary sections at all
    _mk(tmp_path, "inv", declared=["a"], on_disk=["a"])
    drift = IR.detect_drift(tmp_path, "inv")  # must not raise
    assert drift == [] or all(isinstance(d, IR.DriftItem) for d in drift)


def test_both_at_a_glance_shapes(tmp_path):
    # shape 1: dict-with-studies
    _mk(tmp_path, "d1", declared=["a", "gone"], on_disk=["a"],
        narrative={"at_a_glance": {"studies": ["a", "gone"]}})
    assert any(d.kind == "structured_ref_to_removed" and "at_a_glance" in d.where
               for d in IR.detect_drift(tmp_path, "d1"))
    # shape 2: list-of-{study,role}
    _mk(tmp_path, "d2", declared=["a", "gone"], on_disk=["a"],
        narrative={"at_a_glance": [{"study": "a", "role": "x"}, {"study": "gone", "role": "y"}]})
    assert any(d.kind == "structured_ref_to_removed" and "at_a_glance" in d.where
               for d in IR.detect_drift(tmp_path, "d2"))


def test_clean_investigation_returns_no_drift(tmp_path):
    _mk(tmp_path, "inv", declared=["a", "b"], on_disk=["a", "b"],
        narrative={"scientific_argument": {"evidence_for": ["a: ok", "b: ok"]}})
    assert IR.detect_drift(tmp_path, "inv") == []


# ---- apply_mechanical (structured drops; prose untouched; comment-preserving) ----

def test_apply_drops_removed_from_members_and_at_a_glance(tmp_path):
    _mk(tmp_path, "inv", declared=["a", "gone"], on_disk=["a"],
        narrative={"at_a_glance": [{"study": "a", "role": "x"}, {"study": "gone", "role": "y"}]})
    rep = IR.apply_mechanical(tmp_path, "inv")
    assert rep["applied"] >= 1
    spec = yaml.safe_load((tmp_path / "investigations" / "inv" / "investigation.yaml").read_text())
    members = spec.get("members") or spec.get("studies") or []
    names = [m if isinstance(m, str) else m.get("study") for m in members]
    assert "gone" not in names
    aag = [e.get("study") for e in (spec.get("at_a_glance") or [])]
    assert "gone" not in aag


def test_apply_prunes_dangling_prerequisite(tmp_path):
    _mk(tmp_path, "inv", declared=["a", "gone"], on_disk=["a"])
    # surviving study 'a' lists removed 'gone' as a prerequisite
    sa = tmp_path / "studies" / "a" / "study.yaml"
    sa.write_text(yaml.safe_dump({"name": "a", "investigation": "inv",
                                  "pipeline_gate": {"prerequisites": ["gone", "x"]}}))
    IR.apply_mechanical(tmp_path, "inv")
    d = yaml.safe_load(sa.read_text())
    assert "gone" not in (d["pipeline_gate"]["prerequisites"])
    assert "x" in d["pipeline_gate"]["prerequisites"]


def test_apply_preserves_comments_and_leaves_prose(tmp_path):
    inv_dir = tmp_path / "investigations" / "inv"
    inv_dir.mkdir(parents=True)
    (tmp_path / "workspace.yaml").write_text("name: tmp\n")
    (tmp_path / "studies" / "a").mkdir(parents=True)
    (tmp_path / "studies" / "a" / "study.yaml").write_text("name: a\ninvestigation: inv\n")
    (inv_dir / "investigation.yaml").write_text(
        "name: inv\n"
        "# keep this comment\n"
        "studies:\n  - a\n  - gone\n"
        "scientific_argument:\n  evidence_for:\n    - 'gone: showed a thing'\n")
    rep = IR.apply_mechanical(tmp_path, "inv")
    text = (inv_dir / "investigation.yaml").read_text()
    assert "# keep this comment" in text          # comment preserved
    assert "gone: showed a thing" in text          # prose NOT touched
    assert rep["skipped_prose"] >= 1               # prose ref reported, not deleted
    spec = yaml.safe_load(text)
    assert "gone" not in (spec.get("studies") or spec.get("members") or [])  # structured drop done


def test_apply_is_idempotent(tmp_path):
    _mk(tmp_path, "inv", declared=["a", "gone"], on_disk=["a"])
    IR.apply_mechanical(tmp_path, "inv")
    rep2 = IR.apply_mechanical(tmp_path, "inv")
    assert rep2["applied"] == 0


# ---- CLI ----

def test_cli_reports_drift_and_applies(tmp_path, capsys):
    import json
    _mk(tmp_path, "inv", declared=["a", "gone"], on_disk=["a"])
    rc = IR.main(["--ws", str(tmp_path), "inv"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "gone" in out and "member_missing_on_disk" in out
    rc2 = IR.main(["--ws", str(tmp_path), "inv", "--apply"])
    assert rc2 == 0
    capsys.readouterr()  # drain the --apply output before the --json run
    spec = yaml.safe_load((tmp_path / "investigations" / "inv" / "investigation.yaml").read_text())
    assert "gone" not in (spec.get("studies") or spec.get("members") or [])
    rc3 = IR.main(["--ws", str(tmp_path), "inv", "--json"])
    assert rc3 == 0
    json.loads(capsys.readouterr().out)  # valid JSON (now clean → [])


def test_flat_member_without_backref_not_missing(tmp_path):
    # a declared member exists as studies/<slug>/study.yaml but has NO investigation
    # back-ref (the common case for older workspaces) -> must NOT be flagged missing.
    ws = str(tmp_path)
    (tmp_path / "workspace.yaml").write_text("name: tmp\n")
    inv = tmp_path / "investigations" / "inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text("name: inv\nstudies:\n  - a\n  - b\n")
    for s in ("a", "b"):
        sd = tmp_path / "studies" / s
        sd.mkdir(parents=True)
        (sd / "study.yaml").write_text(f"name: {s}\n")   # no 'investigation:' back-ref
    drift = IR.detect_drift(ws, "inv")
    assert not any(x.kind == "member_missing_on_disk" for x in drift)
