"""TDD tests for finding_observations.populate_finding_observations (spine stage #5).

Tests written BEFORE implementation (red phase).

Covers:
  - Filling absent evidence.observed, evidence.units, expected.range,
    expected.cites, provenance.run_ids, evidence.divergence_factor
  - calibration_anchor.observed_value / .divergence_factor when anchor exists
  - NEVER touching authored slots (statement/summary/explanation/status/kind/
    expected.summary/expert_reference/calibration_anchor.literature_target/next_action)
  - Skipping findings with no resolvable from_test link
  - Idempotency (second call → filled=0, no write)
  - Comment preservation via ruamel round-trip
  - Divergence arithmetic: inside band→0.0, below→(low-m)/low, above→(m-high)/high
  - calibration_anchor divergence: (measured - L) / L (signed)
  - Returns {filled: N, skipped: N}
  - v2e-invest golden: confirm real study untouched after populate on a tmp copy
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml


# ---------------------------------------------------------------------------
# Fixture builder helpers
# ---------------------------------------------------------------------------

def _write_study(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content), encoding="utf-8")


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


# Minimal study with a test, a canonical run with computed_outcomes, and a
# finding linked via evidence.from_test. The finding has an authored statement
# and no code-owned slots filled yet.
_BASE_STUDY = """\
# study header comment
name: test-study

tests:
  # inline comment on frac-test
  - name: frac-test
    measure:
      kind: generation_average
      path: obs.fraction
    pass_if:
      op: in_range
      low: 0.2
      high: 0.5
    cites:
      - Boesen2024

readouts:
  - name: DnaA-ATP fraction
    identifier: obs.fraction
    units: fraction
    notes: Boesen band [0.2, 0.5].

runs:
  # canonical run entry
  - name: run-canonical
    status: completed
    timestamp: '2026-01-01T00:00:00Z'
    emitter:
      store: /tmp/fake.parquet
    computed_outcomes:
      frac-test:
        result: PASS
        measured_value: 0.28
        evaluated_by: code
        operator: in_range
        detail: 0.28 in [0.2, 0.5]

findings:
  # authored finding
  - id: F-01
    kind: computational
    status: confirms
    statement: 'The DnaA-ATP fraction lands in the biological band.'
    summary: Mechanism summary authored by expert.
    evidence:
      from_test: frac-test
    expected:
      summary: Boesen 2024 reports 0.2-0.5 for DnaA-ATP fraction.
"""


_STUDY_NO_TEST_LINK = """\
name: test-study

tests:
  - name: frac-test
    measure:
      kind: generation_average
      path: obs.fraction
    pass_if:
      op: in_range
      low: 0.2
      high: 0.5

runs:
  - name: run-canonical
    status: completed
    timestamp: '2026-01-01T00:00:00Z'
    computed_outcomes:
      frac-test:
        result: PASS
        measured_value: 0.28
        evaluated_by: code

findings:
  - id: F-01
    kind: computational
    status: novel
    statement: Some authored claim.
    evidence:
      from_run: run-canonical
"""


_STUDY_WITH_CALIBRATION_ANCHOR = """\
name: test-study

tests:
  - name: dnaa-level
    measure:
      kind: generation_average
      path: obs.count
    pass_if:
      op: in_range
      low: 300
      high: 800
    calibration_anchor:
      literature_target: 550
    cites:
      - Boesen2024

runs:
  - name: run-canonical
    status: completed
    timestamp: '2026-01-01T00:00:00Z'
    computed_outcomes:
      dnaa-level:
        result: PASS
        measured_value: 535
        evaluated_by: code

findings:
  - id: F-02
    kind: computational
    status: confirms
    statement: 'Total DnaA is in the biological range.'
    evidence:
      from_test: dnaa-level
    calibration_anchor:
      literature_target: 550
"""


# Study with two findings: one with from_test (should fill), one without (skip)
_STUDY_MIXED = """\
name: mixed-study

tests:
  - name: frac-test
    measure:
      kind: generation_average
      path: obs.fraction
    pass_if:
      op: in_range
      low: 0.2
      high: 0.5
    cites:
      - Boesen2024

runs:
  - name: run-canonical
    status: completed
    timestamp: '2026-01-01T00:00:00Z'
    computed_outcomes:
      frac-test:
        result: PASS
        measured_value: 0.28
        evaluated_by: code

findings:
  - id: F-01
    kind: computational
    status: confirms
    statement: 'Linked finding.'
    evidence:
      from_test: frac-test
  - id: F-02
    kind: computational
    status: novel
    statement: 'Unlinked finding.'
    evidence:
      from_run: run-canonical
"""


# ---------------------------------------------------------------------------
# Task 1 — Test suite
# ---------------------------------------------------------------------------

def test_populate_import():
    """The module and function are importable."""
    from discovery_engine.finding_observations import populate_finding_observations  # noqa: F401


def test_populate_returns_filled_skipped(tmp_path):
    """Returns a dict with 'filled' and 'skipped' integer keys."""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", _BASE_STUDY)

    from discovery_engine.finding_observations import populate_finding_observations
    result = populate_finding_observations(study_dir)
    assert isinstance(result, dict), "must return dict"
    assert "filled" in result and "skipped" in result
    assert isinstance(result["filled"], int)
    assert isinstance(result["skipped"], int)


def test_populate_fills_evidence_observed(tmp_path):
    """evidence.observed is filled from computed_outcomes[T].measured_value."""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", _BASE_STUDY)

    from discovery_engine.finding_observations import populate_finding_observations
    result = populate_finding_observations(study_dir)

    assert result["filled"] == 1
    doc = _load(study_dir / "study.yaml")
    finding = doc["findings"][0]
    assert "evidence" in finding
    assert finding["evidence"]["observed"] == pytest.approx(0.28)


def test_populate_fills_evidence_units(tmp_path):
    """evidence.units is filled from the matched readout's units."""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", _BASE_STUDY)

    from discovery_engine.finding_observations import populate_finding_observations
    populate_finding_observations(study_dir)

    doc = _load(study_dir / "study.yaml")
    finding = doc["findings"][0]
    assert finding["evidence"].get("units") == "fraction"


def test_populate_fills_expected_range(tmp_path):
    """expected.range is filled from test.pass_if {low, high}."""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", _BASE_STUDY)

    from discovery_engine.finding_observations import populate_finding_observations
    populate_finding_observations(study_dir)

    doc = _load(study_dir / "study.yaml")
    finding = doc["findings"][0]
    assert finding["expected"]["range"] == [0.2, 0.5]


def test_populate_fills_expected_cites(tmp_path):
    """expected.cites is filled from test.cites."""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", _BASE_STUDY)

    from discovery_engine.finding_observations import populate_finding_observations
    populate_finding_observations(study_dir)

    doc = _load(study_dir / "study.yaml")
    finding = doc["findings"][0]
    assert finding["expected"]["cites"] == ["Boesen2024"]


def test_populate_fills_provenance_run_ids(tmp_path):
    """provenance.run_ids is filled from the canonical run name."""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", _BASE_STUDY)

    from discovery_engine.finding_observations import populate_finding_observations
    populate_finding_observations(study_dir)

    doc = _load(study_dir / "study.yaml")
    finding = doc["findings"][0]
    assert finding["provenance"]["run_ids"] == ["run-canonical"]


def test_populate_fills_divergence_factor_inside_band(tmp_path):
    """Measured 0.28 is inside [0.2, 0.5] → divergence_factor = 0.0."""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", _BASE_STUDY)

    from discovery_engine.finding_observations import populate_finding_observations
    populate_finding_observations(study_dir)

    doc = _load(study_dir / "study.yaml")
    finding = doc["findings"][0]
    assert finding["evidence"]["divergence_factor"] == pytest.approx(0.0)


def test_populate_fills_divergence_factor_below_band(tmp_path):
    """Measured 0.15 is below low=0.2 → divergence_factor = (0.2 - 0.15) / 0.2 = 0.25."""
    study_yaml = """\
name: test-study
tests:
  - name: frac-test
    pass_if:
      op: in_range
      low: 0.2
      high: 0.5
runs:
  - name: run-canonical
    status: completed
    timestamp: '2026-01-01T00:00:00Z'
    computed_outcomes:
      frac-test:
        result: FAIL
        measured_value: 0.15
        evaluated_by: code
findings:
  - id: F-01
    kind: computational
    status: contradicts
    statement: 'Below band.'
    evidence:
      from_test: frac-test
"""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", study_yaml)

    from discovery_engine.finding_observations import populate_finding_observations
    populate_finding_observations(study_dir)

    doc = _load(study_dir / "study.yaml")
    finding = doc["findings"][0]
    # (0.2 - 0.15) / 0.2 = 0.25
    assert finding["evidence"]["divergence_factor"] == pytest.approx(0.25)


def test_populate_fills_divergence_factor_above_band(tmp_path):
    """Measured 0.7 is above high=0.5 → divergence_factor = (0.7 - 0.5) / 0.5 = 0.4."""
    study_yaml = """\
name: test-study
tests:
  - name: frac-test
    pass_if:
      op: in_range
      low: 0.2
      high: 0.5
runs:
  - name: run-canonical
    status: completed
    timestamp: '2026-01-01T00:00:00Z'
    computed_outcomes:
      frac-test:
        result: FAIL
        measured_value: 0.7
        evaluated_by: code
findings:
  - id: F-01
    kind: computational
    status: contradicts
    statement: 'Above band.'
    evidence:
      from_test: frac-test
"""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", study_yaml)

    from discovery_engine.finding_observations import populate_finding_observations
    populate_finding_observations(study_dir)

    doc = _load(study_dir / "study.yaml")
    finding = doc["findings"][0]
    # (0.7 - 0.5) / 0.5 = 0.4
    assert finding["evidence"]["divergence_factor"] == pytest.approx(0.4)


def test_populate_skips_finding_with_no_test_link(tmp_path):
    """A finding with only from_run (no from_test) → skipped, never fabricated."""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", _STUDY_NO_TEST_LINK)

    from discovery_engine.finding_observations import populate_finding_observations
    result = populate_finding_observations(study_dir)

    assert result["skipped"] >= 1
    assert result["filled"] == 0

    # The finding must be entirely unchanged
    doc = _load(study_dir / "study.yaml")
    finding = doc["findings"][0]
    assert "observed" not in finding.get("evidence", {})
    assert "range" not in finding.get("expected", {})


def test_populate_idempotent(tmp_path):
    """Second call returns filled=0 and does NOT write the file again."""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", _BASE_STUDY)

    from discovery_engine.finding_observations import populate_finding_observations
    r1 = populate_finding_observations(study_dir)
    mtime1 = (study_dir / "study.yaml").stat().st_mtime

    r2 = populate_finding_observations(study_dir)
    mtime2 = (study_dir / "study.yaml").stat().st_mtime

    assert r1["filled"] == 1
    assert r2["filled"] == 0  # nothing new to fill
    assert mtime2 == mtime1, "file must not be written on second idempotent call"


def test_populate_never_touches_authored_fields(tmp_path):
    """statement, summary, explanation, status, kind, expected.summary,
    calibration_anchor.literature_target, next_action are NEVER modified."""
    study_yaml = """\
name: test-study
tests:
  - name: frac-test
    pass_if:
      op: in_range
      low: 0.2
      high: 0.5
    cites:
      - Boesen2024
runs:
  - name: run-canonical
    status: completed
    timestamp: '2026-01-01T00:00:00Z'
    computed_outcomes:
      frac-test:
        result: PASS
        measured_value: 0.28
        evaluated_by: code
findings:
  - id: F-01
    kind: computational
    status: confirms
    statement: 'Authored statement — must not change.'
    summary: 'Authored summary — must not change.'
    explanation: 'Authored explanation — must not change.'
    next_action: 'Authored next action — must not change.'
    evidence:
      from_test: frac-test
    expected:
      summary: 'Authored expected summary — must not change.'
    calibration_anchor:
      literature_target: 0.35
"""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", study_yaml)

    from discovery_engine.finding_observations import populate_finding_observations
    populate_finding_observations(study_dir)

    doc = _load(study_dir / "study.yaml")
    f = doc["findings"][0]
    # Authored fields must be unchanged
    assert f["statement"] == "Authored statement — must not change."
    assert f["summary"] == "Authored summary — must not change."
    assert f["explanation"] == "Authored explanation — must not change."
    assert f["next_action"] == "Authored next action — must not change."
    assert f["kind"] == "computational"
    assert f["status"] == "confirms"
    assert f["expected"]["summary"] == "Authored expected summary — must not change."
    assert f["calibration_anchor"]["literature_target"] == 0.35


def test_populate_does_not_overwrite_existing_observed(tmp_path):
    """If evidence.observed is already set, it is NOT overwritten (fill-absent-only)."""
    study_yaml = """\
name: test-study
tests:
  - name: frac-test
    pass_if:
      op: in_range
      low: 0.2
      high: 0.5
runs:
  - name: run-canonical
    status: completed
    timestamp: '2026-01-01T00:00:00Z'
    computed_outcomes:
      frac-test:
        result: PASS
        measured_value: 0.28
        evaluated_by: code
findings:
  - id: F-01
    kind: computational
    status: confirms
    statement: 'Test.'
    evidence:
      from_test: frac-test
      observed: 0.999
"""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", study_yaml)

    from discovery_engine.finding_observations import populate_finding_observations
    populate_finding_observations(study_dir)

    doc = _load(study_dir / "study.yaml")
    # 0.999 was authored/existing — must NOT be overwritten by 0.28
    assert doc["findings"][0]["evidence"]["observed"] == pytest.approx(0.999)


def test_populate_mixed_findings_only_fills_linked(tmp_path):
    """Only findings with from_test get filled; others are skipped."""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", _STUDY_MIXED)

    from discovery_engine.finding_observations import populate_finding_observations
    result = populate_finding_observations(study_dir)

    assert result["filled"] == 1
    assert result["skipped"] == 1

    doc = _load(study_dir / "study.yaml")
    f1 = doc["findings"][0]  # from_test: frac-test
    f2 = doc["findings"][1]  # from_run (no test link)
    assert "observed" in f1.get("evidence", {})
    assert "observed" not in f2.get("evidence", {})


def test_populate_calibration_anchor_fills_observed_value(tmp_path):
    """If finding has calibration_anchor, fills observed_value + divergence_factor."""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", _STUDY_WITH_CALIBRATION_ANCHOR)

    from discovery_engine.finding_observations import populate_finding_observations
    populate_finding_observations(study_dir)

    doc = _load(study_dir / "study.yaml")
    finding = doc["findings"][0]
    anchor = finding.get("calibration_anchor", {})
    # observed_value should be filled
    assert anchor.get("observed_value") == pytest.approx(535)
    # divergence_factor should be (535 - 550) / 550 = -0.02727... (signed)
    expected_div = (535 - 550) / 550
    assert anchor.get("divergence_factor") == pytest.approx(expected_div, abs=1e-5)
    # literature_target must NOT be touched
    assert anchor.get("literature_target") == 550


def test_populate_calibration_anchor_never_touches_literature_target(tmp_path):
    """calibration_anchor.literature_target is NEVER overwritten."""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", _STUDY_WITH_CALIBRATION_ANCHOR)

    from discovery_engine.finding_observations import populate_finding_observations
    populate_finding_observations(study_dir)

    doc = _load(study_dir / "study.yaml")
    finding = doc["findings"][0]
    assert finding["calibration_anchor"]["literature_target"] == 550


def test_populate_comments_preserved(tmp_path):
    """YAML comments on header and untouched sections survive the round-trip."""
    # The _BASE_STUDY fixture has comments; after populate those must survive.
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", _BASE_STUDY)

    from discovery_engine.finding_observations import populate_finding_observations
    populate_finding_observations(study_dir)

    text = (study_dir / "study.yaml").read_text(encoding="utf-8")
    assert "# study header comment" in text
    assert "# inline comment on frac-test" in text
    assert "# canonical run entry" in text
    assert "# authored finding" in text


def test_populate_no_computed_outcomes_skips_finding(tmp_path):
    """If the canonical run has no computed_outcomes for the test, finding is skipped."""
    study_yaml = """\
name: test-study
tests:
  - name: frac-test
    pass_if:
      op: in_range
      low: 0.2
      high: 0.5
runs:
  - name: run-canonical
    status: completed
    timestamp: '2026-01-01T00:00:00Z'
    computed_outcomes: {}
findings:
  - id: F-01
    kind: computational
    status: confirms
    statement: 'Missing computed outcome.'
    evidence:
      from_test: frac-test
"""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", study_yaml)

    from discovery_engine.finding_observations import populate_finding_observations
    result = populate_finding_observations(study_dir)

    # skipped when measured_value is absent
    assert result["filled"] == 0
    assert result["skipped"] == 1


def test_populate_no_canonical_run_skips(tmp_path):
    """If there is no canonical run, all findings are skipped."""
    study_yaml = """\
name: test-study
tests:
  - name: frac-test
    pass_if:
      op: in_range
      low: 0.2
      high: 0.5
runs: []
findings:
  - id: F-01
    kind: computational
    status: confirms
    statement: 'No run yet.'
    evidence:
      from_test: frac-test
"""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", study_yaml)

    from discovery_engine.finding_observations import populate_finding_observations
    result = populate_finding_observations(study_dir)

    assert result["filled"] == 0
    assert result["skipped"] == 1


# ---------------------------------------------------------------------------
# Divergence: div-by-zero guards
# ---------------------------------------------------------------------------

def test_populate_divergence_div_by_zero_low(tmp_path):
    """Guard: low=0 — divergence = abs(measured - low) instead of dividing."""
    study_yaml = """\
name: test-study
tests:
  - name: zero-low
    pass_if:
      op: in_range
      low: 0
      high: 1.0
runs:
  - name: run-canonical
    status: completed
    timestamp: '2026-01-01T00:00:00Z'
    computed_outcomes:
      zero-low:
        result: FAIL
        measured_value: -0.5
        evaluated_by: code
findings:
  - id: F-01
    kind: computational
    status: contradicts
    statement: 'Below zero band.'
    evidence:
      from_test: zero-low
"""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", study_yaml)

    from discovery_engine.finding_observations import populate_finding_observations
    result = populate_finding_observations(study_dir)
    # Should not raise; divergence_factor may be computed or None
    assert result["filled"] >= 0  # at minimum, doesn't crash


def test_populate_no_findings_section(tmp_path):
    """A study with no findings[] section returns filled=0 skipped=0."""
    study_yaml = """\
name: test-study
tests:
  - name: frac-test
    pass_if:
      op: in_range
      low: 0.2
      high: 0.5
runs:
  - name: run-canonical
    status: completed
    timestamp: '2026-01-01T00:00:00Z'
    computed_outcomes:
      frac-test:
        result: PASS
        measured_value: 0.28
        evaluated_by: code
"""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", study_yaml)

    from discovery_engine.finding_observations import populate_finding_observations
    result = populate_finding_observations(study_dir)

    assert result["filled"] == 0
    assert result["skipped"] == 0


# ---------------------------------------------------------------------------
# Fix: _divergence_factor must not crash on per-generation dict measured_value
# (in_range_every_generation / generation_average_in_range store measured_value
# as {generation: value}, not a scalar — see study_evaluator.py ~:867-868).
# ---------------------------------------------------------------------------


def test_divergence_factor_scalar_still_computes_exactly_as_before():
    """Sanity: scalar measured_value arithmetic is unchanged by the type guard."""
    from discovery_engine.finding_observations import _divergence_factor

    band = {"low": 0.2, "high": 0.5}
    assert _divergence_factor(0.28, band, None) == pytest.approx(0.0)
    assert _divergence_factor(0.15, band, None) == pytest.approx(0.25)
    assert _divergence_factor(0.7, band, None) == pytest.approx(0.4)


def test_divergence_factor_dict_measured_value_returns_none_not_raise():
    """A per-generation dict measured_value (e.g. from in_range_every_generation)
    must not raise TypeError from the ``low <= measured <= high`` comparison —
    it is not a well-defined single scalar, so divergence arithmetic is skipped."""
    from discovery_engine.finding_observations import _divergence_factor

    band = {"low": 0.2, "high": 0.5}
    per_gen_measured = {"0": 0.28, "1": 0.31, "2": 0.19}

    result = _divergence_factor(per_gen_measured, band, None)  # must not raise
    assert result is None

    # Also exercise the calibration_anchor and threshold-only code paths with a
    # dict measured_value — none of them should raise either.
    assert _divergence_factor(per_gen_measured, None, 0.35) is None
    assert _divergence_factor(per_gen_measured, {"threshold": 0.3}, None) is None


def test_populate_per_generation_dict_measured_value_does_not_raise(tmp_path):
    """End-to-end: a finding linked to a per-generation test (measured_value is
    a dict, as produced by in_range_every_generation/generation_average_in_range
    in study_evaluator.py) must not crash populate_finding_observations. The
    dict is still recorded as evidence.observed (never fabricated away), but
    divergence_factor is skipped since it has no defined arithmetic for a
    per-generation shape."""
    study_yaml = """\
name: test-study
tests:
  - name: per-gen-test
    measure:
      kind: in_range_every_generation
      path: obs.fraction
    pass_if:
      op: in_range
      low: 0.2
      high: 0.5
runs:
  - name: run-canonical
    status: completed
    timestamp: '2026-01-01T00:00:00Z'
    computed_outcomes:
      per-gen-test:
        result: PASS
        measured_value:
          '0': 0.28
          '1': 0.31
          '2': 0.19
        evaluated_by: code
findings:
  - id: F-01
    kind: computational
    status: confirms
    statement: 'Per-generation finding.'
    evidence:
      from_test: per-gen-test
"""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", study_yaml)

    from discovery_engine.finding_observations import populate_finding_observations

    # Must not raise (this reproduces the /viva-biology-forward crash).
    result = populate_finding_observations(study_dir)

    assert result["filled"] == 1
    assert result["skipped"] == 0

    doc = _load(study_dir / "study.yaml")
    finding = doc["findings"][0]
    # evidence.observed is still recorded (the raw dict), never fabricated
    assert finding["evidence"]["observed"] == {"0": 0.28, "1": 0.31, "2": 0.19}
    # divergence_factor is skipped for the non-scalar shape (no crash, no field)
    assert "divergence_factor" not in finding["evidence"]


# ---------------------------------------------------------------------------
# Task 2: sync wiring
# ---------------------------------------------------------------------------


def test_sync_includes_findings_key(tmp_path, monkeypatch):
    """study_outcomes.sync includes 'findings' key in its summary dict."""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", "name: sync-test-study\nruns: []\n")

    # Monkeypatch populate_finding_observations to return a known value
    import discovery_engine.finding_observations as fo
    monkeypatch.setattr(fo, "populate_finding_observations",
                        lambda *a, **k: {"filled": 2, "skipped": 1})

    from discovery_engine import study_outcomes as so
    result = so.sync(study_dir)

    assert "findings" in result, "sync must include findings key"
    assert result["findings"] == {"filled": 2, "skipped": 1}


def test_sync_findings_best_effort_does_not_raise_on_error(tmp_path, monkeypatch):
    """populate_finding_observations errors are captured; sync still returns cleanly."""
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", "name: err-test\nruns: []\n")

    def _boom(*a, **k):
        raise RuntimeError("boom")

    import discovery_engine.finding_observations as fo
    monkeypatch.setattr(fo, "populate_finding_observations", _boom)

    from discovery_engine import study_outcomes as so
    result = so.sync(study_dir)

    assert "findings" in result
    assert "error" in result["findings"]


# ---------------------------------------------------------------------------
# Synthetic golden: dnaa-like study with from_test (proves full populate)
# ---------------------------------------------------------------------------

# Mimics the dnaa-2 nucleotide-balance study band structure, but with from_test
# links added so populate can fill the slots. This is the synthetic golden
# because the real dnaa-2 uses from_run (documented in the test below).
_DNAA_LIKE_STUDY = """\
# Synthetic golden — mirrors dnaa-2 band structure with from_test links
name: dnaa-like-synthetic

tests:
  # Mimics frac-test from dnaa-2 (Boesen 2024 [0.2, 0.5])
  - name: frac-test
    measure:
      kind: generation_average
      path: obs.fraction
    pass_if:
      op: in_range
      low: 0.2
      high: 0.5
    cites:
      - Boesen2024
  # Mimics total-dnaa test from dnaa-2 ([300, 800])
  - name: total-dnaa
    measure:
      kind: generation_average
      path: obs.count
    pass_if:
      op: in_range
      low: 300
      high: 800
    calibration_anchor:
      literature_target: 550
    cites:
      - Boesen2024

readouts:
  - name: DnaA-ATP fraction
    identifier: obs.fraction
    units: fraction
    notes: DnaA-ATP / total, Boesen 2024 [0.2, 0.5] band.
  - name: total DnaA count
    identifier: obs.count
    units: molecules/cell
    notes: Total DnaA pool, [300, 800] band.

runs:
  - name: dnaa-like-run-1
    status: completed
    timestamp: '2026-06-01T00:00:00Z'
    # ruamel round-trip must preserve this comment
    computed_outcomes:
      frac-test:
        result: PASS
        measured_value: 0.268
        evaluated_by: code
        operator: in_range
        detail: 0.268 in [0.2, 0.5]
      total-dnaa:
        result: PASS
        measured_value: 575.0
        evaluated_by: code
        operator: in_range
        detail: 575 in [300, 800]

findings:
  # authored finding — statement/summary must NOT change
  - id: F-01
    kind: computational
    status: confirms
    statement: 'The DnaA-ATP fraction is in the biological band with the integrate-dt mechanism.'
    summary: |
      Authored mechanism summary. Must survive populate unchanged.
    evidence:
      from_test: frac-test
    expected:
      summary: 'Boesen 2024 reports [0.2, 0.5] for the DnaA-ATP fraction.'
  - id: F-02
    kind: computational
    status: confirms
    statement: 'Total DnaA pool is in the biological range.'
    evidence:
      from_test: total-dnaa
    calibration_anchor:
      literature_target: 550
"""


def test_golden_synthetic_dnaa_like_populate(tmp_path):
    """Synthetic golden: dnaa-2-like study with from_test links fills all slots.

    Real dnaa-2 uses from_run — documented in test_golden_dnaa2_v2einvest_untouched.
    This synthetic fixture proves the populate path works end-to-end for studies
    that DO have from_test links (the intended future state of dnaa-like studies).
    """
    study_dir = tmp_path / "dnaa-like"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", _DNAA_LIKE_STUDY)

    from discovery_engine.finding_observations import populate_finding_observations
    result = populate_finding_observations(study_dir)

    assert result["filled"] == 2, f"both findings should be filled; got {result}"
    assert result["skipped"] == 0

    doc = _load(study_dir / "study.yaml")
    findings = {f["id"]: f for f in doc["findings"]}

    # F-01: frac-test (in-band → divergence_factor=0.0)
    f1 = findings["F-01"]
    assert f1["evidence"]["observed"] == pytest.approx(0.268)
    assert f1["evidence"]["units"] == "fraction"
    assert f1["evidence"]["divergence_factor"] == pytest.approx(0.0)
    assert f1["expected"]["range"] == [0.2, 0.5]
    assert f1["expected"]["cites"] == ["Boesen2024"]
    assert f1["provenance"]["run_ids"] == ["dnaa-like-run-1"]
    # Authored prose must be preserved
    assert f1["statement"] == "The DnaA-ATP fraction is in the biological band with the integrate-dt mechanism."
    assert "Authored mechanism summary" in f1["summary"]
    assert f1["expected"]["summary"] == "Boesen 2024 reports [0.2, 0.5] for the DnaA-ATP fraction."

    # F-02: total-dnaa (in-band → divergence_factor=0.0) + calibration_anchor
    f2 = findings["F-02"]
    assert f2["evidence"]["observed"] == pytest.approx(575.0)
    assert f2["evidence"]["units"] == "molecules/cell"
    assert f2["evidence"]["divergence_factor"] == pytest.approx(0.0)
    assert f2["expected"]["range"] == [300, 800]
    # calibration_anchor.observed_value + divergence_factor filled
    anchor = f2["calibration_anchor"]
    assert anchor["observed_value"] == pytest.approx(575.0)
    expected_div = (575.0 - 550) / 550
    assert anchor["divergence_factor"] == pytest.approx(expected_div, abs=1e-5)
    assert anchor["literature_target"] == 550  # unchanged


def test_golden_synthetic_dnaa_like_comments_preserved(tmp_path):
    """ruamel comment-preserving round-trip: comments survive populate."""
    study_dir = tmp_path / "dnaa-like"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", _DNAA_LIKE_STUDY)

    from discovery_engine.finding_observations import populate_finding_observations
    populate_finding_observations(study_dir)

    text = (study_dir / "study.yaml").read_text(encoding="utf-8")
    assert "# Synthetic golden — mirrors dnaa-2 band structure" in text
    assert "# Mimics frac-test from dnaa-2 (Boesen 2024 [0.2, 0.5])" in text
    assert "# authored finding — statement/summary must NOT change" in text
    assert "# ruamel round-trip must preserve this comment" in text


def test_golden_synthetic_dnaa_like_idempotent(tmp_path):
    """Second populate call on dnaa-like synthetic → filled=0, no file write."""
    study_dir = tmp_path / "dnaa-like"
    study_dir.mkdir()
    _write_study(study_dir / "study.yaml", _DNAA_LIKE_STUDY)

    from discovery_engine.finding_observations import populate_finding_observations
    r1 = populate_finding_observations(study_dir)
    mtime1 = (study_dir / "study.yaml").stat().st_mtime

    r2 = populate_finding_observations(study_dir)
    mtime2 = (study_dir / "study.yaml").stat().st_mtime

    assert r1["filled"] == 2
    assert r2["filled"] == 0
    assert mtime2 == mtime1, "no write on idempotent second call"


# ---------------------------------------------------------------------------
# Golden test: real dnaa-2 study (read-only, tmp copy)
# ---------------------------------------------------------------------------

DNAA2_STUDY_DIR = Path("/Users/eranagmon/code/v2e-invest/studies/dnaa-2-nucleotide-balance")
DNAA2_STUDY_YAML = DNAA2_STUDY_DIR / "study.yaml"


@pytest.mark.skipif(
    not DNAA2_STUDY_YAML.is_file(),
    reason="dnaa-2 study.yaml not present (read-only golden fixture)",
)
def test_golden_dnaa2_v2einvest_untouched(tmp_path):
    """populate_finding_observations on a TMP COPY does NOT touch v2e-invest.

    dnaa-2's findings use from_run (not from_test), so all are SKIPPED —
    this proves the never-fabricate rule: no test link → no fill.
    """
    import subprocess

    from discovery_engine.finding_observations import populate_finding_observations

    # Record mtime before
    original_mtime = DNAA2_STUDY_YAML.stat().st_mtime

    # Make a tmp copy; operate on the copy
    tmp_study_dir = tmp_path / "dnaa-2-copy"
    tmp_study_dir.mkdir()
    (tmp_study_dir / "study.yaml").write_text(
        DNAA2_STUDY_YAML.read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    result = populate_finding_observations(tmp_study_dir)

    # All dnaa-2 findings use from_run → all skipped, none filled
    doc = yaml.safe_load(DNAA2_STUDY_YAML.read_text()) or {}
    n_findings = len(doc.get("findings") or [])
    assert result["skipped"] == n_findings, (
        f"All {n_findings} dnaa-2 findings (from_run) should be skipped; "
        f"got filled={result['filled']} skipped={result['skipped']}"
    )
    assert result["filled"] == 0

    # v2e-invest must be untouched
    assert DNAA2_STUDY_YAML.stat().st_mtime == original_mtime, (
        "v2e-invest study.yaml was modified — must never touch real workspace"
    )

    # Confirm git status on v2e-invest is clean
    r = subprocess.run(
        ["git", "-C", str(DNAA2_STUDY_DIR.parent.parent), "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    assert r.stdout.strip() == "", (
        f"v2e-invest git working tree is dirty:\n{r.stdout}"
    )


# ---------------------------------------------------------------------------
# Task 3: biology-forward aspect tests (folded into /viva-harden-investigation
# from the standalone /viva-biology-forward skill — see skills/
# viva-harden-investigation/SKILL.md, "### Aspect: biology-forward")
# ---------------------------------------------------------------------------


def test_backing_plugin_symbols_still_importable():
    """finding_observations stays in the plugin (it is transitively required by
    workbench-free STAY modules), so its symbol must remain importable even
    though the skill no longer references it. expert_search moved into the
    workbench in Phase 2.1k (batch 1) — it is no longer a plugin module."""
    from discovery_engine.finding_observations import populate_finding_observations  # noqa: F401


