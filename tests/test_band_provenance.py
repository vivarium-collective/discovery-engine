"""Tests for band provenance lint checks (spine stage 3a).

Task 2: _check_band_test_missing_cites — WARN when a numeric-band test/behavior_test
        has no cites.
Task 3: _check_band_cites_unknown_bib_key — ERROR when any cites on tests/behavior_tests/
        readouts reference an unknown bib_key (silent when no papers.bib).
Task 4: bands_missing_provenance(spec) — helper returning uncited band entries.

TDD: all tests written before implementation.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from discovery_engine.report_linter import (
    LintFinding,
    lint_workspace_report,
)


FIXTURES = Path(__file__).resolve().parent / "fixtures" / "lint-cases"


def _copy_fixture(name: str, dest: Path) -> Path:
    src = FIXTURES / name
    if not src.is_dir():
        raise FileNotFoundError(src)
    shutil.copytree(src, dest)
    return dest


def _findings_by_check(findings: list[LintFinding]) -> dict[str, list[LintFinding]]:
    out: dict[str, list[LintFinding]] = {}
    for f in findings:
        out.setdefault(f.check, []).append(f)
    return out


# ---------------------------------------------------------------------------
# Task 2: band_test_missing_cites — WARN on band with no cites
# ---------------------------------------------------------------------------


def test_band_test_missing_cites_fires_for_behavior_tests(tmp_path):
    """behavior_tests[] with numeric pass_if.low/high but no cites → warning per entry."""
    ws = _copy_fixture("band-test-missing-cites", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    warns = [
        f for f in by_check.get("band_test_missing_cites", [])
        if f.study_slug == "study-band-no-cites"
    ]
    # Two behavior_tests with low/high (mass-doubling has no low/high so no warn)
    # Plus one tests[] item with low/high. Total ≥ 3.
    assert len(warns) >= 3, f"expected ≥3 warnings, got {warns}"
    assert all(f.level == "warning" for f in warns)
    names = [f.field_path for f in warns]
    assert any("behavior_tests" in fp for fp in names)
    assert any("tests" in fp for fp in names)
    assert "add cites" in warns[0].message.lower() or "cites" in warns[0].message.lower()


def test_band_test_missing_cites_silent_for_regression_pin(tmp_path):
    """A numeric-band behavior_test declared `gate_class: regression_pin` is a
    threshold set after the run (never acceptance evidence), so it has no
    pre-stated external prior to cite — no band_test_missing_cites warning."""
    ws = _copy_fixture("band-test-missing-cites", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    flagged = {
        f.field_path
        for f in findings
        if f.check == "band_test_missing_cites" and f.study_slug == "study-band-no-cites"
    }
    # The pin band carries a numeric low/high but must NOT be flagged; the
    # acceptance bands in the fixture still are.
    pin_msgs = [
        f for f in findings
        if f.check == "band_test_missing_cites" and "charge-conservation-pin" in (f.message or "")
    ]
    assert pin_msgs == [], "regression_pin band must not be flagged for a missing cite"


def test_band_test_missing_cites_silent_for_tests_with_cites(tmp_path):
    """behavior_tests/tests[] items WITH resolvable cites → no warning."""
    ws = _copy_fixture("band-test-missing-cites", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    warns = [
        f for f in by_check.get("band_test_missing_cites", [])
        if f.study_slug == "study-band-with-cites"
    ]
    assert warns == [], f"expected no warnings for study-band-with-cites, got {warns}"


def test_band_test_missing_cites_check_name(tmp_path):
    """The check field is 'band_test_missing_cites' (stable)."""
    ws = _copy_fixture("band-test-missing-cites", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    assert "band_test_missing_cites" in by_check


def test_band_test_missing_cites_non_band_test_is_silent(tmp_path):
    """Tests with no low/high/threshold (e.g. periodic_doubling) don't fire."""
    ws = _copy_fixture("band-test-missing-cites", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    warns = [
        f for f in by_check.get("band_test_missing_cites", [])
        if "mass-doubling" in f.field_path
    ]
    assert warns == [], "mass-doubling test (no low/high) should not warn"


def test_band_test_missing_cites_silent_without_band_fields(tmp_path):
    """A workspace with no band tests at all produces no band_test_missing_cites findings."""
    # Use the clean-baseline fixture (no tests with low/high)
    ws = _copy_fixture("clean-baseline", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    assert by_check.get("band_test_missing_cites", []) == []


# ---------------------------------------------------------------------------
# Task 3: band_cites_unknown_bib_key — ERROR on unknown bib_key
# ---------------------------------------------------------------------------


def test_band_cites_unknown_bib_key_fires_on_behavior_tests(tmp_path):
    """behavior_tests[].cites with an unknown bib_key → error."""
    ws = _copy_fixture("band-test-unknown-bib", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    errors = by_check.get("band_cites_unknown_bib_key", [])
    assert errors, "expected at least one band_cites_unknown_bib_key error"
    assert all(f.level == "error" for f in errors)
    messages = [f.message for f in errors]
    assert any("FakeRef1999" in m for m in messages), (
        f"expected FakeRef1999 to be called out; messages: {messages}"
    )


def test_band_cites_unknown_bib_key_fires_on_calibration_anchor_cites(tmp_path):
    """behavior_tests[].calibration_anchor.cites with unknown key → error."""
    ws = _copy_fixture("band-test-unknown-bib", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    errors = by_check.get("band_cites_unknown_bib_key", [])
    messages = [f.message for f in errors]
    assert any("AnotherFake2001" in m for m in messages), (
        f"expected AnotherFake2001 to be called out; messages: {messages}"
    )


def test_band_cites_unknown_bib_key_fires_on_tests_array(tmp_path):
    """tests[].cites with unknown key → error."""
    ws = _copy_fixture("band-test-unknown-bib", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    errors = by_check.get("band_cites_unknown_bib_key", [])
    messages = [f.message for f in errors]
    assert any("GhostRef9999" in m for m in messages), (
        f"expected GhostRef9999 to be called out; messages: {messages}"
    )


def test_band_cites_unknown_bib_key_fires_on_readouts(tmp_path):
    """readouts[].cites with unknown key → error."""
    ws = _copy_fixture("band-test-unknown-bib", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    errors = by_check.get("band_cites_unknown_bib_key", [])
    messages = [f.message for f in errors]
    assert any("MissingRef2042" in m for m in messages), (
        f"expected MissingRef2042 to be called out; messages: {messages}"
    )


def test_band_cites_unknown_bib_key_known_cites_silent(tmp_path):
    """Known bib_keys (Boesen2024, KnownAuthor2020) in cites → no error for those."""
    ws = _copy_fixture("band-test-unknown-bib", tmp_path / "ws")
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    errors = by_check.get("band_cites_unknown_bib_key", [])
    messages = [f.message for f in errors]
    assert not any("Boesen2024" in m for m in messages), "Boesen2024 is known, should not error"
    assert not any("KnownAuthor2020" in m for m in messages)


def test_band_cites_unknown_bib_key_silent_without_papers_bib(tmp_path):
    """When no papers.bib exists in the workspace, the check is silent (same as finding check)."""
    # Build a minimal workspace with a band test citing an unknown key, but no papers.bib
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: x\n")
    study_dir = ws / "studies" / "s"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(
        "name: s\n"
        "baseline:\n"
        "  - name: b1\n"
        "    composite: pkg.composites.x\n"
        "behavior_tests:\n"
        "  - name: frac-test\n"
        "    pass_if:\n"
        "      op: in_range\n"
        "      low: 0.2\n"
        "      high: 0.5\n"
        "    cites:\n"
        "      - TotallyUnknownKey\n"
    )
    findings = lint_workspace_report(ws)
    by_check = _findings_by_check(findings)
    errors = by_check.get("band_cites_unknown_bib_key", [])
    assert errors == [], (
        "without papers.bib the check should be silent, got: "
        + str([f.message for f in errors])
    )


# ---------------------------------------------------------------------------
# Task 4: bands_missing_provenance helper
# ---------------------------------------------------------------------------


def test_bands_missing_provenance_import():
    """The helper is importable from discovery_engine.band_provenance."""
    from discovery_engine.band_provenance import bands_missing_provenance  # noqa: F401


def test_bands_missing_provenance_returns_uncited_band_entries():
    """Returns {name, kind, band, field_path} for every band entry lacking cites.

    The plan says: 'test on an inline spec mirroring real dnaa-2 (tests[] with
    pass_if {low:0.2,high:0.5} no cites; readouts[] with the prose "Boesen 2024"
    band in notes, no cites) → both returned.'
    """
    from discovery_engine.band_provenance import bands_missing_provenance

    spec = {
        "behavior_tests": [
            {
                "name": "frac-test",
                "pass_if": {"op": "in_range", "low": 0.2, "high": 0.5},
                # no cites — should be flagged
            },
            {
                "name": "frac-test-cited",
                "pass_if": {"op": "in_range", "low": 0.2, "high": 0.5},
                "cites": ["Boesen2024"],  # has cites — should NOT be flagged
            },
        ],
        "tests": [
            {
                "name": "v4-total-dnaa",
                "pass_if": {"op": "generation_average_in_range", "low": 300, "high": 800},
                # no cites — should be flagged
            },
        ],
        "readouts": [
            {
                "name": "DnaA-ATP fraction",
                "identifier": "bulk X[c]",
                "notes": "Boesen 2024 [0.2, 0.5] band.",
                # no cites field + prose band notation — should be flagged
            },
        ],
    }
    result = bands_missing_provenance(spec)
    assert isinstance(result, list)
    # The behavior_test without cites should be flagged
    frac_flagged = [r for r in result if r["name"] == "frac-test"]
    assert len(frac_flagged) == 1, f"frac-test should be flagged; result: {result}"
    assert frac_flagged[0]["kind"] == "behavior_test"
    assert frac_flagged[0]["band"] == {"low": 0.2, "high": 0.5}
    # The behavior_test WITH cites should NOT be flagged
    cited_flagged = [r for r in result if r["name"] == "frac-test-cited"]
    assert cited_flagged == [], "frac-test-cited has cites and should not be flagged"
    # The v4 tests[] item without cites should be flagged
    v4_flagged = [r for r in result if r["name"] == "v4-total-dnaa"]
    assert len(v4_flagged) == 1
    assert v4_flagged[0]["kind"] == "test"
    # The readout with prose band notation and no cites should be flagged (plan: "both returned")
    ro_flagged = [r for r in result if r["name"] == "DnaA-ATP fraction"]
    assert len(ro_flagged) == 1, (
        f"readout with prose '[0.2, 0.5] band' should be flagged; result: {result}"
    )
    assert ro_flagged[0]["kind"] == "readout"
    assert ro_flagged[0]["band"].get("low") == 0.2
    assert ro_flagged[0]["band"].get("high") == 0.5


def test_bands_missing_provenance_calibration_anchor():
    """calibration_anchor.literature_target without cites is also flagged."""
    from discovery_engine.band_provenance import bands_missing_provenance

    spec = {
        "behavior_tests": [
            {
                "name": "dnaa-level",
                "pass_if": {"op": "generation_average_in_range", "low": 300, "high": 800},
                "calibration_anchor": {
                    "literature_target": 550,
                    # no cites on calibration_anchor
                },
                # no cites on the test itself
            },
        ],
    }
    result = bands_missing_provenance(spec)
    flagged = [r for r in result if r["name"] == "dnaa-level"]
    assert len(flagged) >= 1, "dnaa-level with no cites at any level should be flagged"


def test_bands_missing_provenance_field_path():
    """Result includes field_path for each flagged entry."""
    from discovery_engine.band_provenance import bands_missing_provenance

    spec = {
        "behavior_tests": [
            {
                "name": "frac-test",
                "pass_if": {"op": "in_range", "low": 0.2, "high": 0.5},
            },
        ],
    }
    result = bands_missing_provenance(spec)
    assert len(result) == 1
    entry = result[0]
    assert "field_path" in entry
    assert "behavior_tests" in entry["field_path"]


def test_bands_missing_provenance_empty_spec():
    """An empty spec returns an empty list."""
    from discovery_engine.band_provenance import bands_missing_provenance

    assert bands_missing_provenance({}) == []
    assert bands_missing_provenance({"readouts": [], "tests": []}) == []


def test_bands_missing_provenance_non_band_test_not_flagged():
    """A test with no low/high/threshold is not a band test and is not flagged."""
    from discovery_engine.band_provenance import bands_missing_provenance

    spec = {
        "behavior_tests": [
            {
                "name": "mass-doubling",
                "pass_if": {"op": "periodic_doubling_every_generation", "tolerance": 0.15},
                # no low/high — not a band test
            },
        ],
    }
    result = bands_missing_provenance(spec)
    assert result == []


# ---------------------------------------------------------------------------
# Task 4: Golden test — real dnaa-2 bands flagged as missing provenance
# ---------------------------------------------------------------------------


DNAA2_STUDY = Path("/Users/eranagmon/code/v2e-invest/studies/dnaa-2-nucleotide-balance/study.yaml")
DNAA2_BIB = Path("/Users/eranagmon/code/v2e-invest/references/papers.bib")


@pytest.mark.skipif(
    not DNAA2_STUDY.is_file(),
    reason="dnaa-2 study.yaml not present (read-only golden fixture)",
)
def test_golden_dnaa2_bands_flagged_as_missing_provenance(tmp_path):
    """Real dnaa-2 study has [0.2,0.5] and [300,800] bands with no cites field.
    bands_missing_provenance should flag them. NEVER modifies v2e-invest."""
    from discovery_engine.band_provenance import bands_missing_provenance

    # Make a read-only TMP copy — never touch v2e-invest
    tmp_study = tmp_path / "study.yaml"
    tmp_study.write_text(DNAA2_STUDY.read_text())

    spec = yaml.safe_load(tmp_study.read_text()) or {}
    result = bands_missing_provenance(spec)

    # dnaa-2's tests[] has two band entries: [0.2,0.5] and [300,800]
    bands = {tuple(sorted((r["band"].get("low"), r["band"].get("high")))) for r in result}
    assert (0.2, 0.5) in bands, (
        f"[0.2,0.5] band should be flagged as missing provenance; got: {result}"
    )
    assert (300, 800) in bands, (
        f"[300,800] band should be flagged as missing provenance; got: {result}"
    )


@pytest.mark.skipif(
    not DNAA2_STUDY.is_file(),
    reason="dnaa-2 study.yaml not present (read-only golden fixture)",
)
def test_golden_dnaa2_study_file_not_modified_by_this_test(tmp_path):
    """Confirm our tmp copy is independent: editing the copy doesn't touch v2e-invest."""
    from discovery_engine.band_provenance import bands_missing_provenance

    original_mtime = DNAA2_STUDY.stat().st_mtime
    tmp_study = tmp_path / "study.yaml"
    tmp_study.write_text(DNAA2_STUDY.read_text())
    # Run the helper on the copy
    spec = yaml.safe_load(tmp_study.read_text()) or {}
    bands_missing_provenance(spec)
    # Verify the original was not touched
    assert DNAA2_STUDY.stat().st_mtime == original_mtime, (
        "v2e-invest study.yaml was modified — golden test must be read-only"
    )
