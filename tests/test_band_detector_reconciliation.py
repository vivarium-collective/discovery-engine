"""Reconciliation tests: "does this test carry a numeric acceptance band" was
reimplemented three times — band_provenance.py (bands_missing_provenance's
inline detection), rigor.py (_has_numeric_band), report_linter.py
(_is_numeric_band) — and they disagreed on ``pass_if.value``: rigor.py counted
a numeric ``pass_if.value`` comparator band (e.g. ``{op: "<", value: 0.05}``)
as a band; band_provenance.py and report_linter.py did not. That made a
value-style test invisible to the /viva-cite-bands citation-gap worklist
(band_provenance) while still counting as a "sourced"/"unsourced" numeric band
in rigor's gate — an incoherent surface for a reviewer.

Fix: one canonical ``band_provenance.has_numeric_band(test)`` (+
``numeric_band_of``); rigor.py and report_linter.py now import it instead of
reimplementing. Reconciliation rule: ``pass_if.value`` IS a band when it is a
plain number (regardless of comparator op) — a non-numeric ``value``
(the config-selection shape, e.g. ``{op: equals, value: "wells-riley"}``) is
never a band.

These tests assert all three consumers now agree, and cover the reconciled
rule's numeric/non-numeric split.
"""
from __future__ import annotations

import pytest


# A test using the comparator-op shape with a numeric value — a genuine
# quantitative acceptance band per docs/concepts/expected-behavior-grammar.md's
# closed pass_if op set (e.g. `pass_if: {op: "<", value: 0.05}`).
_VALUE_BAND_TEST = {
    "name": "p-value-test",
    "pass_if": {"op": "<", "value": 0.05},
    # no cites — should be flagged as missing provenance by all consumers
}

# A test using pass_if.value for config-selection (non-numeric) — must NOT be
# treated as a quantitative band by anyone.
_CONFIG_SELECTOR_TEST = {
    "name": "engine-select-test",
    "pass_if": {"op": "equals", "value": "wells-riley"},
}

# A test using pass_if.value with a boolean — bool is an int subclass in
# Python but must not be treated as a numeric band.
_BOOL_VALUE_TEST = {
    "name": "tooling-test",
    "pass_if": {"op": "eq", "value": True},
}


def test_canonical_has_numeric_band_detects_value_band():
    from discovery_engine.band_provenance import has_numeric_band

    assert has_numeric_band(_VALUE_BAND_TEST) is True


def test_canonical_has_numeric_band_rejects_non_numeric_value():
    from discovery_engine.band_provenance import has_numeric_band

    assert has_numeric_band(_CONFIG_SELECTOR_TEST) is False
    assert has_numeric_band(_BOOL_VALUE_TEST) is False


def test_all_three_consumers_agree_on_value_band():
    """rigor.py and report_linter.py now import the same function
    band_provenance.py uses — identity, not just equal behavior."""
    from discovery_engine.band_provenance import has_numeric_band
    from discovery_engine.rigor import _has_numeric_band
    from discovery_engine.report_linter import _is_numeric_band

    # Same underlying function (single source of truth, not 3 copies).
    assert _has_numeric_band is has_numeric_band
    assert _is_numeric_band is has_numeric_band

    for test in (_VALUE_BAND_TEST, _CONFIG_SELECTOR_TEST, _BOOL_VALUE_TEST):
        assert has_numeric_band(test) == _has_numeric_band(test) == _is_numeric_band(test)


def test_value_band_test_now_visible_to_citation_gap_worklist():
    """The /viva-cite-bands citation-gap surface (band_provenance's
    bands_missing_provenance) must see an uncited pass_if.value band — this
    was the concrete bug: previously invisible here, but flagged by rigor."""
    from discovery_engine.band_provenance import bands_missing_provenance

    spec = {"tests": [_VALUE_BAND_TEST]}
    result = bands_missing_provenance(spec)

    flagged = [r for r in result if r["name"] == "p-value-test"]
    assert len(flagged) == 1, f"pass_if.value band should be flagged; got {result}"
    assert flagged[0]["kind"] == "test"
    assert flagged[0]["band"].get("value") == pytest.approx(0.05)


def test_config_selector_test_never_flagged_as_band():
    """A non-numeric pass_if.value (config-selection) must never surface as a
    band-missing-citation entry — it isn't a quantitative acceptance band."""
    from discovery_engine.band_provenance import bands_missing_provenance

    spec = {"tests": [_CONFIG_SELECTOR_TEST]}
    result = bands_missing_provenance(spec)
    assert result == []


def test_report_linter_check_now_fires_on_value_band(tmp_path):
    """_check_band_test_missing_cites (Pass 10B) now warns on an uncited
    pass_if.value band, matching rigor's existing gate."""
    import yaml

    from discovery_engine.report_linter import lint_workspace_report

    ws = tmp_path / "ws"
    study_dir = ws / "studies" / "value-band-study"
    study_dir.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: ws\n", encoding="utf-8")
    (study_dir / "study.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "value-band-study",
                "tests": [_VALUE_BAND_TEST],
            }
        ),
        encoding="utf-8",
    )

    findings = lint_workspace_report(ws)
    band_findings = [f for f in findings if f.check == "band_test_missing_cites"]
    assert len(band_findings) == 1, (
        f"expected one band_test_missing_cites warning for the value-band test; "
        f"got checks: {[f.check for f in findings]}"
    )


def test_rigor_gate_and_citation_gap_worklist_now_agree_end_to_end():
    """Before the fix: rigor.study_rigor counted the value-band test in its
    numeric-band-provenance dimension while band_provenance's citation-gap
    worklist didn't see it at all. Now both see exactly the same band tests."""
    from discovery_engine.band_provenance import bands_missing_provenance
    from discovery_engine.rigor import _numeric_band_tests

    spec = {"tests": [_VALUE_BAND_TEST, _CONFIG_SELECTOR_TEST]}

    rigor_band_names = {t["name"] for t in _numeric_band_tests(spec)}
    gap_worklist_names = {r["name"] for r in bands_missing_provenance(spec)}

    assert rigor_band_names == {"p-value-test"}
    assert gap_worklist_names == {"p-value-test"}
    assert rigor_band_names == gap_worklist_names
