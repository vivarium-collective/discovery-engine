"""Pass 10B tests for ``/viva-study seed-from-followup --from-finding`` helper.

The skill is prose-driven (Claude follows the steps in SKILL.md and shells
out to this Python module for the deterministic bits). These tests pin
the heuristic:

  - ``build_child_seed_from_finding`` derives ``purpose`` / ``key_assumptions``
    / ``seeded_from`` from a finding entry.
  - ``build_parent_proposal_patch`` flips proposal status to ``seeded`` and
    records ``seeded_study`` + ``linked_finding``.
  - The convenience ``apply_from_finding`` loads a real parent yaml,
    rejects a missing finding id, and produces a ChildSeed mergeable
    into a fresh child template.
  - The CLI smoke (``python -m discovery_engine.seed_from_followup``)
    prints a non-empty preview and exits 0 on the happy path; exits 2
    when the finding doesn't exist.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from discovery_engine import seed_from_followup as sff


# ---------------------------------------------------------------------------
# Fixture: a parent study with one finding + one proposal
# ---------------------------------------------------------------------------


def _make_parent_study(tmp_path: Path) -> Path:
    sd = tmp_path / "studies" / "dnaa-01"
    sd.mkdir(parents=True)
    spec = {
        "name": "dnaa-01",
        "baseline": [{"name": "b1", "composite": "pkg.composites.x"}],
        "behavior_tests": [
            {
                "name": "dnaA-atp-fraction",
                "classification": "supporting",
                "description": "DnaA-ATP fraction tracks literature band.",
                "measure": {"kind": "ratio", "numerator": "DnaA_ATP",
                            "denominator": "DnaA_total"},
                "pass_if": {"op": "between", "lo": 0.2, "hi": 0.3},
                "units": "dimensionless",
                "requires_simulation": "baseline",
                "cites": ["boesen2024"],
            },
            {
                "name": "unrelated-test",
                "classification": "diagnostic",
                "measure": {"kind": "scalar"},
                "pass_if": {"op": ">", "value": 0},
            },
        ],
        "findings": [
            {
                "id": "F-03",
                "kind": "biological",
                "status": "contradicts",
                "statement": (
                    "v2ecoli underestimates the DnaA-ATP fraction at "
                    "initiation by ~5x."
                ),
                "evidence": {
                    "from_test": "dnaA-atp-fraction",
                    "smoking_gun": (
                        "DnaA-ATP / DnaA-total stays at 0.05 across all "
                        "timepoints; literature reports 0.20-0.30."
                    ),
                },
                "explanation": (
                    "The current DnaA model treats ATP/ADP cycling as "
                    "instantaneous; in reality ATP-loading is gated by "
                    "DARS-mediated reactivation."
                ),
                "next_action": (
                    "Calibrate the DARS reactivation rate to match the "
                    "literature ATP fraction in range 0.20-0.30."
                ),
            },
            {  # second finding with no next_action / no smoking_gun
                "id": "F-04",
                "kind": "computational",
                "status": "novel",
                "statement": "Listener emits NaN when n_dnaA drops below 5.",
            },
        ],
        "followup_proposals": [
            {
                "id": "calibrate-dars",
                "title": "Calibrate DARS reactivation rate",
                "motivation": (
                    "F-03 shows we badly underestimate DnaA-ATP fraction."
                ),
                "status": "proposed",
            }
        ],
    }
    (sd / "study.yaml").write_text(yaml.safe_dump(spec, sort_keys=False))
    return sd / "study.yaml"


# ---------------------------------------------------------------------------
# Pure builders
# ---------------------------------------------------------------------------


def test_build_child_seed_from_finding_populates_all_three_blocks(tmp_path):
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    seed = sff.build_child_seed_from_finding(
        parent, "dnaa-01", "calibrate-dars", "F-03",
    )
    # purpose.mechanism comes from explanation verbatim
    assert "DARS-mediated reactivation" in seed.purpose["mechanism"]
    # purpose.question is derived from next_action ("Calibrate..." → "How do we calibrate...?")
    assert seed.purpose["question"].endswith("?")
    assert "calibrate" in seed.purpose["question"].lower()
    # purpose.expected_outcome trips on the "to match"/"in range" cue
    assert "0.20" in seed.purpose["expected_outcome"]
    # key_assumptions picks up the smoking_gun string
    assert len(seed.key_assumptions) == 1
    assert "DnaA-ATP" in seed.key_assumptions[0]
    # seeded_from carries study + proposal + finding + a copy of evidence
    assert seed.seeded_from["study"] == "dnaa-01"
    assert seed.seeded_from["proposal_id"] == "calibrate-dars"
    assert seed.seeded_from["finding"] == "F-03"
    assert seed.seeded_from["evidence"]["from_test"] == "dnaA-atp-fraction"


def test_build_child_seed_handles_finding_without_optional_fields(tmp_path):
    """F-04 has no explanation, no next_action, no smoking_gun. The seed
    should still produce a usable purpose.question (falling back to the
    statement) and an empty key_assumptions list."""
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    seed = sff.build_child_seed_from_finding(
        parent, "dnaa-01", "calibrate-dars", "F-04",
    )
    assert seed.purpose["question"].startswith("Investigate")
    assert "mechanism" not in seed.purpose
    assert "expected_outcome" not in seed.purpose
    assert seed.key_assumptions == []
    assert seed.seeded_from["finding"] == "F-04"
    # Without next_action / explanation / from_test, the enriched blocks
    # are empty — the seed degrades gracefully.
    assert seed.pipeline_gate == {}
    assert seed.behavior_tests == []
    assert seed.model_change == {}
    assert "evidence" not in seed.seeded_from


def test_build_child_seed_carries_parent_behavior_test(tmp_path):
    """The behavior_test named by finding.evidence.from_test is copied
    into the child as ``classification: primary`` — it's the test the
    follow-up exists to make pass."""
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    seed = sff.build_child_seed_from_finding(
        parent, "dnaa-01", "calibrate-dars", "F-03",
    )
    assert len(seed.behavior_tests) == 1
    bt = seed.behavior_tests[0]
    assert bt["name"] == "dnaA-atp-fraction"
    # Reclassified — this is the child's reason to exist.
    assert bt["classification"] == "primary"
    # Measure/pass_if/cites copied through.
    assert bt["measure"]["kind"] == "ratio"
    assert bt["pass_if"]["op"] == "between"
    assert "boesen2024" in bt["cites"]


def test_build_child_seed_populates_pipeline_gate_from_next_action(tmp_path):
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    seed = sff.build_child_seed_from_finding(
        parent, "dnaa-01", "calibrate-dars", "F-03",
    )
    assert seed.pipeline_gate["proceed_condition"].startswith(
        "Calibrate the DARS reactivation rate"
    )


def test_build_child_seed_populates_model_change_when_explanation_present(tmp_path):
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    seed = sff.build_child_seed_from_finding(
        parent, "dnaa-01", "calibrate-dars", "F-03",
    )
    # The model_change notes points the reader at purpose.mechanism so the
    # explanation isn't duplicated.
    assert "TBD" in seed.model_change["notes"]
    assert "purpose.mechanism" in seed.model_change["notes"]


def test_build_child_seed_skips_carryover_when_from_test_unknown(tmp_path):
    """If the finding names a test that doesn't exist in the parent's
    behavior_tests, we silently skip the carryover (the prose flow will
    still propose primary tests during Build)."""
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    # Mutate F-03 to point at a non-existent test name.
    for f in parent["findings"]:
        if f["id"] == "F-03":
            f["evidence"]["from_test"] = "no-such-test"
    seed = sff.build_child_seed_from_finding(
        parent, "dnaa-01", "calibrate-dars", "F-03",
    )
    assert seed.behavior_tests == []


def test_build_child_seed_supports_evidence_from_tests_list(tmp_path):
    """A finding can list multiple tests via evidence.from_tests; all
    matched parent tests are carried over."""
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    for f in parent["findings"]:
        if f["id"] == "F-03":
            f["evidence"].pop("from_test", None)
            f["evidence"]["from_tests"] = ["dnaA-atp-fraction", "unrelated-test"]
    seed = sff.build_child_seed_from_finding(
        parent, "dnaa-01", "calibrate-dars", "F-03",
    )
    names = [t["name"] for t in seed.behavior_tests]
    assert names == ["dnaA-atp-fraction", "unrelated-test"]
    assert all(t["classification"] == "primary" for t in seed.behavior_tests)


def test_build_child_seed_raises_on_unknown_finding(tmp_path):
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    with pytest.raises(ValueError, match="F-99"):
        sff.build_child_seed_from_finding(
            parent, "dnaa-01", "calibrate-dars", "F-99",
        )


def test_build_parent_proposal_patch_flips_status_and_adds_linked_finding():
    proposal = {
        "id": "calibrate-dars",
        "title": "x",
        "motivation": "y",
        "status": "proposed",
    }
    patched = sff.build_parent_proposal_patch(
        proposal, new_slug="dnaa-02-dars-calibration", finding_id="F-03",
    )
    assert patched["status"] == "seeded"
    assert patched["seeded_study"] == "dnaa-02-dars-calibration"
    assert patched["linked_finding"] == "F-03"
    # Original is untouched.
    assert proposal["status"] == "proposed"


def test_build_parent_proposal_patch_preserves_existing_linked_finding():
    """If the proposal already pointed at the finding, don't overwrite."""
    proposal = {
        "id": "calibrate-dars",
        "status": "proposed",
        "linked_finding": "F-03",
    }
    patched = sff.build_parent_proposal_patch(
        proposal, new_slug="dnaa-02", finding_id="F-03",
    )
    assert patched["linked_finding"] == "F-03"


def test_build_parent_proposal_patch_without_finding_skips_link():
    """If --from-finding wasn't passed (finding_id=None), no linked_finding key."""
    proposal = {"id": "x", "status": "proposed"}
    patched = sff.build_parent_proposal_patch(
        proposal, new_slug="child", finding_id=None,
    )
    assert "linked_finding" not in patched
    assert patched["status"] == "seeded"


# ---------------------------------------------------------------------------
# ChildSeed.merge_into — non-destructive merge onto a partial child
# ---------------------------------------------------------------------------


def test_child_seed_merge_into_preserves_existing_purpose_fields(tmp_path):
    """If propose-followup already populated child.purpose.question,
    --from-finding's seed must NOT clobber it."""
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    seed = sff.build_child_seed_from_finding(
        parent, "dnaa-01", "calibrate-dars", "F-03",
    )
    child = {
        "name": "dnaa-02-dars-calibration",
        "purpose": {"question": "Pre-existing question from proposal seed."},
    }
    merged = seed.merge_into(child)
    # existing question wins
    assert merged["purpose"]["question"] == "Pre-existing question from proposal seed."
    # but mechanism + expected_outcome get added
    assert merged["purpose"]["mechanism"]
    assert merged["purpose"]["expected_outcome"]
    # seeded_from + key_assumptions added
    assert merged["seeded_from"]["finding"] == "F-03"
    assert merged["key_assumptions"]


def test_child_seed_merge_into_dedups_key_assumptions(tmp_path):
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    seed = sff.build_child_seed_from_finding(
        parent, "dnaa-01", "calibrate-dars", "F-03",
    )
    existing_ka = list(seed.key_assumptions)  # same string already present
    child = {"key_assumptions": existing_ka}
    merged = seed.merge_into(child)
    assert merged["key_assumptions"] == existing_ka


def test_child_seed_merge_into_merges_seeded_from_with_existing(tmp_path):
    """If the propose-followup step already wrote {study, proposal_id},
    the --from-finding helper adds `finding:` + `evidence:` without
    nuking the rest."""
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    seed = sff.build_child_seed_from_finding(
        parent, "dnaa-01", "calibrate-dars", "F-03",
    )
    child = {"seeded_from": {"study": "dnaa-01", "proposal_id": "calibrate-dars"}}
    merged = seed.merge_into(child)
    assert merged["seeded_from"]["study"] == "dnaa-01"
    assert merged["seeded_from"]["proposal_id"] == "calibrate-dars"
    assert merged["seeded_from"]["finding"] == "F-03"
    assert merged["seeded_from"]["evidence"]["from_test"] == "dnaA-atp-fraction"


def test_child_seed_merge_into_preserves_existing_pipeline_gate_keys(tmp_path):
    """If a prior step wrote pipeline_gate.proceed_condition, the seed
    must not overwrite it. New keys still add."""
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    seed = sff.build_child_seed_from_finding(
        parent, "dnaa-01", "calibrate-dars", "F-03",
    )
    child = {
        "pipeline_gate": {
            "proceed_condition": "Hand-written condition wins.",
            "prerequisites": ["dnaa-01"],
        }
    }
    merged = seed.merge_into(child)
    assert merged["pipeline_gate"]["proceed_condition"] == (
        "Hand-written condition wins."
    )
    assert merged["pipeline_gate"]["prerequisites"] == ["dnaa-01"]


def test_child_seed_merge_into_dedups_behavior_tests_by_name(tmp_path):
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    seed = sff.build_child_seed_from_finding(
        parent, "dnaa-01", "calibrate-dars", "F-03",
    )
    child = {
        "behavior_tests": [
            {"name": "dnaA-atp-fraction", "classification": "primary",
             "description": "Hand-edited; should win."},
        ]
    }
    merged = seed.merge_into(child)
    # Existing entry kept; carryover suppressed because name matches.
    assert len(merged["behavior_tests"]) == 1
    assert merged["behavior_tests"][0]["description"] == (
        "Hand-edited; should win."
    )


def test_child_seed_merge_into_skips_model_change_when_child_has_string_shape(tmp_path):
    """If the child already has a string-shape model_change (terse summary),
    leave it alone — the operator can promote to object form manually."""
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    seed = sff.build_child_seed_from_finding(
        parent, "dnaa-01", "calibrate-dars", "F-03",
    )
    child = {"model_change": "Recalibrate DARS rate (1 param change)."}
    merged = seed.merge_into(child)
    assert merged["model_change"] == (
        "Recalibrate DARS rate (1 param change)."
    )


# ---------------------------------------------------------------------------
# apply_from_finding — convenience wrapper
# ---------------------------------------------------------------------------


def test_apply_from_finding_end_to_end(tmp_path):
    parent_yaml = _make_parent_study(tmp_path)
    plan = sff.apply_from_finding(
        parent_yaml,
        proposal_id="calibrate-dars",
        finding_id="F-03",
        new_slug="dnaa-02-dars-calibration",
    )
    assert plan.child_seed.seeded_from["finding"] == "F-03"
    assert plan.updated_proposal["status"] == "seeded"
    assert plan.updated_proposal["linked_finding"] == "F-03"
    assert plan.finding["id"] == "F-03"
    # The original proposal is untouched
    assert plan.proposal["status"] == "proposed"


def test_apply_from_finding_rejects_unknown_proposal(tmp_path):
    parent_yaml = _make_parent_study(tmp_path)
    with pytest.raises(ValueError, match="bogus"):
        sff.apply_from_finding(
            parent_yaml, "bogus", "F-03", "child",
        )


def test_apply_from_finding_rejects_missing_parent(tmp_path):
    with pytest.raises(FileNotFoundError):
        sff.apply_from_finding(
            tmp_path / "nonexistent.yaml", "p", "F-01", "child",
        )


# ---------------------------------------------------------------------------
# CLI smoke
# ---------------------------------------------------------------------------


def test_cli_happy_path_exits_zero_and_prints_diffs(tmp_path):
    parent_yaml = _make_parent_study(tmp_path)
    cp = subprocess.run(
        [sys.executable, "-m", "discovery_engine.seed_from_followup",
         str(parent_yaml), "calibrate-dars", "F-03",
         "--new-slug", "dnaa-02-dars-calibration"],
        capture_output=True, text=True,
    )
    assert cp.returncode == 0, cp.stderr
    assert "Child seed" in cp.stdout
    assert "F-03" in cp.stdout
    assert "calibrate-dars" in cp.stdout
    assert "dnaa-02-dars-calibration" in cp.stdout
    # Enrichment surfaces in the preview.
    assert "pipeline_gate" in cp.stdout
    assert "behavior_tests" in cp.stdout
    assert "dnaA-atp-fraction" in cp.stdout
    assert "model_change" in cp.stdout


def test_cli_unknown_finding_exits_2(tmp_path):
    parent_yaml = _make_parent_study(tmp_path)
    cp = subprocess.run(
        [sys.executable, "-m", "discovery_engine.seed_from_followup",
         str(parent_yaml), "calibrate-dars", "F-99"],
        capture_output=True, text=True,
    )
    assert cp.returncode == 2
    assert "F-99" in cp.stderr


# ---------------------------------------------------------------------------
# resolve_seed_source — the unifying resolver across the 4 followup families
# ---------------------------------------------------------------------------


def test_resolve_seed_source_synthesizes_proposal_from_finding(tmp_path):
    """A finding with a next_action seeds STANDALONE — no pre-existing
    followup_proposals[] row. resolve_seed_source synthesizes an inline
    proposal stub stamped with the finding id."""
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    # Drop the followup_proposals so the finding has to seed standalone.
    parent.pop("followup_proposals", None)
    src = sff.resolve_seed_source(parent, finding_id="F-03")
    assert src.finding_id == "F-03"
    assert src.synthesized is True
    assert src.proposal  # a stub dict with an id
    assert src.proposal["id"]
    assert src.proposal.get("linked_finding") == "F-03"
    # The stub's title/motivation are derived from the finding.
    assert src.proposal.get("title")


def test_resolve_seed_source_standalone_finding_builds_seed(tmp_path):
    """resolve_seed_source + build_child_seed_from_finding yields a child
    seed stamped seeded_from.finding with a derived purpose.question — with
    NO pre-existing proposal."""
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    parent.pop("followup_proposals", None)
    src = sff.resolve_seed_source(parent, finding_id="F-03")
    seed = sff.build_child_seed_from_finding(
        parent, "dnaa-01", src.proposal["id"], src.finding_id,
    )
    assert seed.seeded_from.get("finding") == "F-03"
    assert seed.purpose.get("question")  # derived from next_action/statement


def test_resolve_seed_source_prefers_existing_proposal(tmp_path):
    """When a finding already has a resolvable proposal_id, resolve uses it
    (not a synthesized stub)."""
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    src = sff.resolve_seed_source(
        parent, finding_id="F-03", proposal_id="calibrate-dars",
    )
    assert src.synthesized is False
    assert src.proposal["id"] == "calibrate-dars"
    assert src.finding_id == "F-03"


def test_resolve_seed_source_by_proposal_id(tmp_path):
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    src = sff.resolve_seed_source(parent, proposal_id="calibrate-dars")
    assert src.proposal["id"] == "calibrate-dars"
    assert src.finding_id is None
    assert src.synthesized is False


def test_resolve_seed_source_legacy_follow_up_studies_idx(tmp_path):
    """The legacy follow_up_studies[] family resolves by index into a
    proposal-shaped SeedSource."""
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    parent["follow_up_studies"] = [
        {"title": "Probe DARS reactivation", "why": "Because F-03.",
         "kind": "calibration"},
    ]
    src = sff.resolve_seed_source(parent, followup_idx=0)
    assert src.proposal["title"] == "Probe DARS reactivation"
    assert src.family == "follow_up_studies"
    assert src.finding_id is None


def test_resolve_seed_source_discovery_implications_proposal(tmp_path):
    """The discovery_implications.followup_study_proposals[] family resolves
    by proposal_id."""
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    parent["discovery_implications"] = {
        "followup_study_proposals": [
            {"id": "di-1", "title": "DI follow-up", "study_type": "mechanism"},
        ]
    }
    src = sff.resolve_seed_source(parent, proposal_id="di-1")
    assert src.proposal["id"] == "di-1"
    assert src.family == "discovery_implications.followup_study_proposals"


def test_resolve_seed_source_requires_a_selector(tmp_path):
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    with pytest.raises(ValueError):
        sff.resolve_seed_source(parent)


def test_resolve_seed_source_unknown_finding_raises(tmp_path):
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    with pytest.raises(ValueError, match="F-99"):
        sff.resolve_seed_source(parent, finding_id="F-99")


# ---------------------------------------------------------------------------
# write_child_study — the callable atomic writer + parent stamp
# ---------------------------------------------------------------------------


def test_write_child_study_creates_child_and_stamps_parent_finding(tmp_path):
    """A STANDALONE finding seed (no pre-existing proposal) creates the child
    study.yaml and stamps the parent finding's seeded_study."""
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    parent.pop("followup_proposals", None)  # standalone
    parent_yaml.write_text(yaml.safe_dump(parent, sort_keys=False))

    src = sff.resolve_seed_source(parent, finding_id="F-03")
    res = sff.write_child_study(tmp_path, "dnaa-01", src, new_slug="dnaa-02")

    child_yaml = tmp_path / "studies" / "dnaa-02" / "study.yaml"
    assert child_yaml.is_file()
    assert res["new_slug"] == "dnaa-02"

    child = yaml.safe_load(child_yaml.read_text())
    assert child["name"] == "dnaa-02"
    assert child["seeded_from"]["finding"] == "F-03"
    assert child["seeded_from"]["study"] == "dnaa-01"
    assert child["purpose"]["question"]
    # pipeline_gate points back at the parent so the DAG draws the edge.
    assert "dnaa-01" in child["pipeline_gate"]["prerequisites"]

    # Parent finding stamped with seeded_study.
    parent_after = yaml.safe_load(parent_yaml.read_text())
    f = next(f for f in parent_after["findings"] if f["id"] == "F-03")
    assert f["seeded_study"] == "dnaa-02"


def test_write_child_study_stamps_existing_proposal_status(tmp_path):
    """When seeding from a real followup_proposals[] entry, that proposal's
    status flips to seeded + seeded_study is recorded."""
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    src = sff.resolve_seed_source(
        parent, finding_id="F-03", proposal_id="calibrate-dars",
    )
    res = sff.write_child_study(tmp_path, "dnaa-01", src, new_slug="dnaa-02")
    assert res["new_slug"] == "dnaa-02"

    parent_after = yaml.safe_load(parent_yaml.read_text())
    prop = next(p for p in parent_after["followup_proposals"]
                if p["id"] == "calibrate-dars")
    assert prop["status"] == "seeded"
    assert prop["seeded_study"] == "dnaa-02"
    # finding also stamped
    f = next(f for f in parent_after["findings"] if f["id"] == "F-03")
    assert f["seeded_study"] == "dnaa-02"


def test_write_child_study_default_slug_from_proposal(tmp_path):
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    src = sff.resolve_seed_source(parent, proposal_id="calibrate-dars")
    res = sff.write_child_study(tmp_path, "dnaa-01", src)
    assert res["new_slug"]
    assert (tmp_path / "studies" / res["new_slug"] / "study.yaml").is_file()


def test_write_child_study_idempotent_parent_stamp(tmp_path):
    """Re-stamping a parent finding that already has seeded_study leaves the
    existing value (fill-absent), and a second write to a fresh slug still
    succeeds."""
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    src = sff.resolve_seed_source(parent, finding_id="F-03")
    sff.write_child_study(tmp_path, "dnaa-01", src, new_slug="dnaa-02")

    parent_after = yaml.safe_load(parent_yaml.read_text())
    f = next(f for f in parent_after["findings"] if f["id"] == "F-03")
    assert f["seeded_study"] == "dnaa-02"
    # Second seed of the SAME finding -> fill-absent keeps the first stamp.
    src2 = sff.resolve_seed_source(parent_after, finding_id="F-03")
    sff.write_child_study(tmp_path, "dnaa-01", src2, new_slug="dnaa-03")
    parent_after2 = yaml.safe_load(parent_yaml.read_text())
    f2 = next(f for f in parent_after2["findings"] if f["id"] == "F-03")
    assert f2["seeded_study"] == "dnaa-02"  # unchanged


def test_write_child_study_refuses_existing_slug(tmp_path):
    parent_yaml = _make_parent_study(tmp_path)
    parent = yaml.safe_load(parent_yaml.read_text())
    src = sff.resolve_seed_source(parent, finding_id="F-03")
    sff.write_child_study(tmp_path, "dnaa-01", src, new_slug="dnaa-02")
    with pytest.raises((FileExistsError, ValueError)):
        src2 = sff.resolve_seed_source(parent, finding_id="F-03")
        sff.write_child_study(tmp_path, "dnaa-01", src2, new_slug="dnaa-02")


# ---------------------------------------------------------------------------
# Golden — a real v2e-invest study with a finding next_action seeds to a
# TMP COPY (the real workspace is READ-ONLY and must stay untouched).
# ---------------------------------------------------------------------------

_V2E_INVEST = Path("/Users/eranagmon/code/v2e-invest")
_GOLDEN_PARENT = "dnaa-00-stage1-baseline"
_GOLDEN_FINDING = "F-S4"


@pytest.mark.skipif(
    not (_V2E_INVEST / "studies" / _GOLDEN_PARENT / "study.yaml").is_file(),
    reason="v2e-invest workspace not present",
)
def test_golden_real_study_finding_seeds_to_tmp_copy(tmp_path):
    """A real v2e-invest study with a finding next_action seeds a child +
    stamps the parent — operating entirely on a TMP COPY so the real
    workspace stays byte-for-byte untouched."""
    real_parent_yaml = _V2E_INVEST / "studies" / _GOLDEN_PARENT / "study.yaml"
    real_before = real_parent_yaml.read_bytes()

    # Build a minimal tmp workspace: workspace.yaml (no studies-layout
    # override) + a copy of the parent study.
    ws = tmp_path / "ws"
    (ws / "studies" / _GOLDEN_PARENT).mkdir(parents=True)
    # A flat-layout workspace.yaml (studies/ at root) is enough for the writer.
    (ws / "workspace.yaml").write_text(
        "schema_version: 2\nname: v2ecoli\ncreated: \"2026-05-16\"\n"
        "plugin_version: 0.6.1\npackage_path: v2ecoli\n"
    )
    shutil.copy(real_parent_yaml,
                ws / "studies" / _GOLDEN_PARENT / "study.yaml")

    parent_spec = yaml.safe_load(real_parent_yaml.read_text())
    src = sff.resolve_seed_source(parent_spec, finding_id=_GOLDEN_FINDING)
    res = sff.write_child_study(ws, _GOLDEN_PARENT, src, new_slug="dnaa-00f-seed")

    # Child created with the finding lineage.
    child_yaml = ws / "studies" / "dnaa-00f-seed" / "study.yaml"
    assert child_yaml.is_file()
    child = yaml.safe_load(child_yaml.read_text())
    assert child["seeded_from"]["finding"] == _GOLDEN_FINDING
    assert child["seeded_from"]["study"] == _GOLDEN_PARENT
    assert child["purpose"]["question"]

    # Parent (the TMP COPY) stamped.
    tmp_parent = yaml.safe_load(
        (ws / "studies" / _GOLDEN_PARENT / "study.yaml").read_text())
    f = next(f for f in tmp_parent["findings"] if f.get("id") == _GOLDEN_FINDING)
    assert f["seeded_study"] == "dnaa-00f-seed"
    assert res["new_slug"] == "dnaa-00f-seed"

    # The REAL workspace is byte-for-byte untouched.
    assert real_parent_yaml.read_bytes() == real_before


# ---------------------------------------------------------------------------
# Wave 3a — next_action_type carry-through (#7) + diagnose family (#19)
# ---------------------------------------------------------------------------


def test_seed_source_carries_next_action_type():
    study = {"findings": [
        {"id": "F1", "statement": "x", "next_action": "Calibrate kS",
         "next_action_type": "calibrate"},
    ]}
    src = sff.resolve_seed_source(study, finding_id="F1")
    assert src.next_action_type == "calibrate"
    assert src.family == "finding.next_action"


def test_resolve_diagnose_family():
    study = {
        "name": "p",
        "behavior_tests": [{"name": "t1"}],
        "runs": [{"name": "r", "status": "complete", "timestamp": "2026-05-01",
                  "outcomes": {"t1": {"result": "fail"}}}],
        "conclusion_logic": {"if_primary_tests_fail": {
            "diagnose": [{"hypothesis": "membrane leaks too fast"}]}},
    }
    src = sff.resolve_seed_source(study, diagnose_idx=0)
    assert src.family == "conclusion_logic.if_primary_tests_fail.diagnose"
    assert src.study_type == "diagnostic"
    assert src.proposal["study_type"] == "diagnostic"
    assert src.proposal["diagnose_idx"] == 0
    assert "t1" in src.proposal["failing_tests"]


def test_resolve_diagnose_idx_out_of_range():
    study = {"conclusion_logic": {"if_primary_tests_fail": {"diagnose": []}}}
    with pytest.raises(ValueError):
        sff.resolve_seed_source(study, diagnose_idx=0)


def test_write_child_study_diagnose_family(tmp_path):
    ws = tmp_path / "ws"
    (ws).mkdir()
    (ws / "workspace.yaml").write_text("schema_version: 2\nname: ws\npackage_path: pbg_ws\n")
    sd = ws / "studies" / "p"
    sd.mkdir(parents=True)
    parent = {
        "name": "p",
        "behavior_tests": [{"name": "t1"}],
        "runs": [{"name": "r", "status": "complete", "timestamp": "2026-05-01",
                  "outcomes": {"t1": {"result": "fail"}}}],
        "conclusion_logic": {"if_primary_tests_fail": {
            "diagnose": [{"hypothesis": "membrane leaks too fast"}]}},
    }
    sff.study_io.save_yaml_atomic(sd / "study.yaml", parent)

    src = sff.resolve_seed_source(parent, diagnose_idx=0)
    res = sff.write_child_study(ws, "p", src, new_slug="p-diag")
    assert res["parent_stamped"] is True

    child = yaml.safe_load((ws / "studies" / "p-diag" / "study.yaml").read_text())
    assert child["study_type"] == "diagnostic"
    assert "t1" in child["seeded_from"]["failing_tests"]

    # parent diagnose[0] now stamped with seeded_study (re-scan stops flagging)
    reloaded = yaml.safe_load((sd / "study.yaml").read_text())
    diag = reloaded["conclusion_logic"]["if_primary_tests_fail"]["diagnose"][0]
    assert diag["seeded_study"] == "p-diag"
