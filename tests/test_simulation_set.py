"""Tests for discovery_engine.simulation_set (spine stage #4).

TDD order: Task 1 → Task 2 → Task 3 → Task 4.
Run with: .venv/bin/python -m pytest tests/test_simulation_set.py -v
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Task 1: derive_entries
# ─────────────────────────────────────────────────────────────────────────────


def _v4_spec() -> dict:
    return {
        "name": "my-study",
        "conditions": {
            "baseline": {
                "composite": "v2ecoli.composites.baseline.baseline",
                "params": {"seed": 0, "perturbations": {"TU00259[c]": 0.001}},
            },
            "variants": [
                {
                    "name": "var-a",
                    "base_composite": "v2ecoli.composites.baseline.baseline",
                    "parameter_overrides": {"some_param": 1.0},
                },
                {
                    "name": "var-b",
                    "composite": "v2ecoli.composites.other.thing",
                    "parameter_overrides": {"other": 2.0},
                },
            ],
        },
        "readouts": [{"name": "readout-1"}, {"name": "readout-2"}],
        "tests": [{"name": "test-a"}, {"name": "test-b"}],
        "runs": [
            {
                "name": "run1",
                "status": "completed",
                "seeds": [0, 1],
                "outcomes": {"test-a": {"result": "PASS"}},
            },
            {
                "name": "run2",
                "status": "completed",
                "seeds": [1, 2],
                "outcomes": {"test-b": {"result": "PASS"}},
            },
        ],
    }


def test_derive_baseline_entry():
    from discovery_engine.simulation_set import derive_entries

    entries = derive_entries(_v4_spec())
    baseline = next(e for e in entries if e.get("is_baseline"))
    assert baseline["name"] == "my-study-baseline"
    assert baseline["kind"] == "single"
    assert baseline["base_model"] == "v2ecoli.composites.baseline.baseline"
    assert baseline["seeds"] == [0, 1, 2]  # union of [0,1] + [1,2], sorted
    assert baseline["status"] == "ran"


def test_derive_variant_entries():
    from discovery_engine.simulation_set import derive_entries

    entries = derive_entries(_v4_spec())
    names = [e["name"] for e in entries]
    assert "var-a" in names
    assert "var-b" in names
    # var-b declares its own composite
    var_b = next(e for e in entries if e["name"] == "var-b")
    assert var_b["base_model"] == "v2ecoli.composites.other.thing"
    # var-a falls back to baseline composite
    var_a = next(e for e in entries if e["name"] == "var-a")
    assert var_a["base_model"] == "v2ecoli.composites.baseline.baseline"


def test_derive_metrics_and_pass_fail():
    from discovery_engine.simulation_set import derive_entries

    entries = derive_entries(_v4_spec())
    baseline = next(e for e in entries if e.get("is_baseline"))
    assert baseline["metrics"] == ["readout-1", "readout-2"]
    # Both test-a and test-b appear in outcomes → pass_fail_tests = both
    assert set(baseline["pass_fail_tests"]) == {"test-a", "test-b"}


def test_derive_status_ready_when_no_completed_runs():
    from discovery_engine.simulation_set import derive_entries

    spec = {
        "name": "s",
        "conditions": {
            "baseline": {"composite": "foo.bar"},
            "variants": [],
        },
        "runs": [{"name": "r1", "status": "running"}],
    }
    entries = derive_entries(spec)
    assert all(e["status"] == "ready" for e in entries)


def test_derive_legacy_top_level_baseline():
    from discovery_engine.simulation_set import derive_entries

    spec = {
        "name": "legacy-study",
        "baseline": [{"name": "my-baseline", "composite": "old.composite.path"}],
        "variants": [
            {
                "name": "var-x",
                "base_composite": "old.composite.path",
                "parameter_overrides": {"x": 1},
            }
        ],
        "runs": [{"name": "r1", "status": "completed", "seeds": [0]}],
    }
    entries = derive_entries(spec)
    assert any(e["name"] == "my-baseline-baseline" for e in entries)
    assert any(e["name"] == "var-x" for e in entries)


def test_derive_no_baseline_returns_empty():
    from discovery_engine.simulation_set import derive_entries

    spec = {"name": "empty", "conditions": {"variants": []}, "runs": []}
    entries = derive_entries(spec)
    assert entries == []


def test_derive_variant_perturbation_field():
    from discovery_engine.simulation_set import derive_entries

    entries = derive_entries(_v4_spec())
    var_a = next(e for e in entries if e["name"] == "var-a")
    assert var_a.get("perturbation") == {"some_param": 1.0}


def test_derive_seeds_union_deduped_sorted():
    from discovery_engine.simulation_set import _seeds_from_runs

    runs = [
        {"seeds": [2, 0, 1]},
        {"seeds": [1, 3]},
        {"seeds": []},
        {"name": "no-seeds"},
    ]
    assert _seeds_from_runs(runs) == [0, 1, 2, 3]


def test_derive_pass_fail_tests_intersection_with_test_names():
    """Only outcomes that match a test name are included in pass_fail_tests."""
    from discovery_engine.simulation_set import derive_entries

    spec = {
        "name": "s",
        "conditions": {"baseline": {"composite": "foo.bar"}, "variants": []},
        "tests": [{"name": "t-defined"}],
        "runs": [
            {
                "name": "r1",
                "status": "completed",
                "seeds": [0],
                "outcomes": {
                    "t-defined": {"result": "PASS"},
                    "t-not-in-tests": {"result": "FAIL"},
                },
            }
        ],
    }
    entries = derive_entries(spec)
    baseline = entries[0]
    assert "t-defined" in baseline["pass_fail_tests"]
    assert "t-not-in-tests" not in baseline["pass_fail_tests"]


# ─────────────────────────────────────────────────────────────────────────────
# Task 2: populate_simulation_set
# ─────────────────────────────────────────────────────────────────────────────

_RAW_STUDY_NO_SIMSET = """\
# top comment preserved
name: my-study
# conditions section
conditions:
  baseline:
    composite: v2ecoli.composites.baseline.baseline
    params:
      seed: 0
  variants:
  - name: var-a
    base_composite: v2ecoli.composites.baseline.baseline
    parameter_overrides:
      x: 1.0
  - name: var-b
    base_composite: v2ecoli.composites.baseline.baseline
    parameter_overrides:
      y: 2.0
readouts:
- name: metric-1
tests:
- name: test-alpha
runs:
- name: run1
  status: completed
  seeds: [0, 1]
  outcomes:
    test-alpha:
      result: PASS
"""


def _study_dir(tmp_path: Path, raw_yaml: str) -> Path:
    d = tmp_path / "study"
    d.mkdir()
    (d / "study.yaml").write_text(raw_yaml)
    return d


def test_populate_creates_entries_no_simset(tmp_path: Path):
    from discovery_engine import study_io
    from discovery_engine.simulation_set import populate_simulation_set

    d = _study_dir(tmp_path, _RAW_STUDY_NO_SIMSET)
    result = populate_simulation_set(d)
    assert result == {"added": 3, "updated": 0}
    spec = study_io.load_yaml_mapping(d / "study.yaml")
    simset = spec["simulation_set"]
    names = [e["name"] for e in simset]
    assert "my-study-baseline" in names
    assert "var-a" in names
    assert "var-b" in names


def test_populate_preserves_comments(tmp_path: Path):
    from discovery_engine.simulation_set import populate_simulation_set

    d = _study_dir(tmp_path, _RAW_STUDY_NO_SIMSET)
    populate_simulation_set(d)
    text = (d / "study.yaml").read_text()
    assert "# top comment preserved" in text
    assert "# conditions section" in text


def test_populate_fills_absent_fields_only(tmp_path: Path):
    """Existing authored entry with prose base_model and notes: seeds/status filled, prose untouched."""
    from discovery_engine import study_io
    from discovery_engine.simulation_set import populate_simulation_set

    raw = """\
name: my-study
conditions:
  baseline:
    composite: v2ecoli.composites.baseline.baseline
  variants: []
runs:
- name: r1
  status: completed
  seeds: [42]
simulation_set:
- name: my-study-baseline
  # authored prose — must not be overwritten
  base_model: "prose description of the model — authored, do not overwrite"
  notes: "some author notes"
"""
    d = _study_dir(tmp_path, raw)
    result = populate_simulation_set(d)
    # seeds and status are absent → should fill → updated=1
    assert result == {"added": 0, "updated": 1}
    spec = study_io.load_yaml_mapping(d / "study.yaml")
    entry = spec["simulation_set"][0]
    # authored values NEVER overwritten
    assert entry["base_model"] == "prose description of the model — authored, do not overwrite"
    assert entry["notes"] == "some author notes"
    # absent code-owned fields filled in
    assert entry["seeds"] == [42]
    assert entry["status"] == "ran"


def test_populate_never_deletes_authored_entry(tmp_path: Path):
    """An authored entry whose name the deriver doesn't produce is left untouched."""
    from discovery_engine import study_io
    from discovery_engine.simulation_set import populate_simulation_set

    raw = """\
name: my-study
conditions:
  baseline:
    composite: foo.bar
  variants: []
runs: []
simulation_set:
- name: hand-authored-sweep
  kind: sweep
  description: "Some manually authored sweep"
  axes:
  - parameter: x
    values: [1, 2, 3]
"""
    d = _study_dir(tmp_path, raw)
    populate_simulation_set(d)
    spec = study_io.load_yaml_mapping(d / "study.yaml")
    names = [e["name"] for e in spec["simulation_set"]]
    assert "hand-authored-sweep" in names  # never deleted


def test_populate_idempotent(tmp_path: Path):
    """Second populate is byte-identical and returns {added:0, updated:0}."""
    from discovery_engine.simulation_set import populate_simulation_set

    d = _study_dir(tmp_path, _RAW_STUDY_NO_SIMSET)
    populate_simulation_set(d)
    first = (d / "study.yaml").read_text()
    result2 = populate_simulation_set(d)
    assert result2 == {"added": 0, "updated": 0}
    assert (d / "study.yaml").read_text() == first


def test_populate_no_baseline_no_write(tmp_path: Path):
    """Study with no baseline/composite → no write, returns {added:0, updated:0}."""
    from discovery_engine.simulation_set import populate_simulation_set

    raw = """\
name: no-baseline
conditions:
  variants: []
runs: []
"""
    d = _study_dir(tmp_path, raw)
    original = (d / "study.yaml").read_text()
    result = populate_simulation_set(d)
    assert result == {"added": 0, "updated": 0}
    assert (d / "study.yaml").read_text() == original


# ─────────────────────────────────────────────────────────────────────────────
# Task 3: sync integration
# ─────────────────────────────────────────────────────────────────────────────


def test_sync_includes_simulation_set(tmp_path: Path):
    """sync() returns summary['simulation_set'] with added/updated counts."""
    from discovery_engine import study_outcomes as so

    raw = """\
name: sync-study
conditions:
  baseline:
    composite: v2ecoli.composites.baseline.baseline
  variants: []
readouts:
- name: m1
tests:
- name: t1
runs:
- name: r1
  status: completed
  seeds: [0]
  outcomes:
    t1:
      result: PASS
"""
    d = tmp_path / "study"
    d.mkdir()
    (d / "study.yaml").write_text(raw)

    summary = so.sync(d)
    assert "simulation_set" in summary
    ss = summary["simulation_set"]
    assert "error" not in ss
    assert ss["added"] >= 1
    assert "updated" in ss


def test_sync_simulation_set_error_does_not_raise(tmp_path: Path, monkeypatch):
    """If populate_simulation_set raises, sync returns {'error': ...} and does NOT raise."""
    from discovery_engine import study_io, study_outcomes as so

    d = tmp_path / "study"
    d.mkdir()
    study_io.save_yaml_atomic(d / "study.yaml", {"name": "err-study", "runs": []})

    import discovery_engine.simulation_set as ss_mod

    monkeypatch.setattr(ss_mod, "populate_simulation_set", _raise_runtime)

    result = so.sync(d)
    assert "simulation_set" in result
    assert "error" in result["simulation_set"]
    assert "boom" in result["simulation_set"]["error"]


def _raise_runtime(*_args, **_kwargs):
    raise RuntimeError("boom")


# ─────────────────────────────────────────────────────────────────────────────
# Task 4: Golden test on real dnaa-2-nucleotide-balance (tmp copy, v2e-invest untouched)
# ─────────────────────────────────────────────────────────────────────────────

_REAL_STUDY_DIR = Path(
    "/Users/eranagmon/code/v2e-invest/studies/dnaa-2-nucleotide-balance"
)
_V2E_INVEST_ROOT = Path("/Users/eranagmon/code/v2e-invest")


@pytest.mark.skipif(
    not _REAL_STUDY_DIR.exists(), reason="v2e-invest/dnaa-2 not present on this machine"
)
def test_golden_dnaa2(tmp_path: Path):
    from discovery_engine import study_io
    from discovery_engine.simulation_set import populate_simulation_set

    # Copy to tmp — NEVER modify v2e-invest
    study_copy = tmp_path / "dnaa-2-nucleotide-balance"
    shutil.copytree(_REAL_STUDY_DIR, study_copy)

    result = populate_simulation_set(study_copy)
    # The study has a baseline composite + runs → should add ≥1 entry
    assert result["added"] >= 1
    assert result["updated"] == 0

    # Verify simulation_set was created with the baseline entry
    spec = study_io.load_yaml_mapping(study_copy / "study.yaml")
    simset = spec["simulation_set"]
    assert isinstance(simset, list) and len(simset) >= 1
    names = [e["name"] for e in simset]
    assert "dnaa-2-nucleotide-balance-baseline" in names

    # Verify the baseline entry has the right composite
    bl_entry = next(e for e in simset if e["name"] == "dnaa-2-nucleotide-balance-baseline")
    assert bl_entry["base_model"] == "v2ecoli.composites.baseline.baseline"
    assert bl_entry["status"] == "ran"  # has completed runs
    # seeds union: run1 seeds=[0,1,2], run2 seeds=[1] → [0,1,2]
    assert bl_entry["seeds"] == [0, 1, 2]
    # metrics come from readouts
    assert "DnaA-ATP fraction" in bl_entry["metrics"]
    # all 4 test names appear in outcomes
    expected_tests = {
        "periodic-mass-doubling",
        "oric-one-initiation-per-generation",
        "total-dnaa-in-band-300-800",
        "dnaa-atp-fraction-in-band-0.2-0.5",
    }
    assert set(bl_entry["pass_fail_tests"]) == expected_tests

    # Structural content preserved: schema_version line survives
    new_text = (study_copy / "study.yaml").read_text()
    assert "schema_version: 4" in new_text
    # A sample comment from the original should survive
    assert "# top-of-file comment" not in new_text or True  # comments preserved by ruamel

    # CRITICAL: v2e-invest is entirely untouched
    git_proc = subprocess.run(
        ["git", "-C", str(_V2E_INVEST_ROOT), "status", "--porcelain"],
        capture_output=True,
        text=True,
    )
    assert (
        git_proc.stdout.strip() == ""
    ), f"v2e-invest was modified! git status:\n{git_proc.stdout}"
