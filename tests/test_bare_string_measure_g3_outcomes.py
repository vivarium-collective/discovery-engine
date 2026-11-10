"""G3 — a bare-STRING ``measure`` must not CRASH the compute_outcomes AGGREGATION path.

G1 fixed the single-test sites (``run_readiness`` / ``evaluate_test``); G2 wired grading via the
shared resolver. But ``compute_outcomes`` still filtered the test list with
``(t.get("measure") or {}).get("kind")`` at two sites — the cross-run (run_delta) split and the
store-less filter — and a non-empty string is truthy, so ``m.get("kind")`` raised
``AttributeError: 'str' object has no attribute 'get'`` the moment a study carried a bare-string
measure (the M-track / workbench convention). That blocked the real autonomous grade path
(discovered taking viva-genesis s01-formation all the way to a verified gate). The same latent
crash lived in ``config._evaluate_config_value``, ``measures._evaluate_run_delta`` and
``grading._grade_axis_from_outcome``. G3 pins the aggregation path: a bare-string measure
alongside dict measures must flow through compute_outcomes without crashing.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import yaml

from discovery_engine.study import evaluator as se


_STUDY_YAML = textwrap.dedent("""\
    name: g3-study

    behavior_tests:
    # a BARE-STRING measure (composite-observable fn) — the crash trigger
    - name: group-life-cycle-persists
      measure: group_life_cycle_persistence
      pass_if:
        op: ">="
        value: 0.5
    # a normal dict measure alongside it still works
    - name: scalar-test
      measure:
        kind: range_check_per_generation
        path: obs.val
        window: full_lineage_from_gen_0
      pass_if:
        op: range
        low: 0
        high: 100

    runs:
    - name: run-a
      status: completed
      emitter:
        store: {store_path}
      outcomes:
        group-life-cycle-persists:
          result: PASS
          detail: hand-authored pass
    """)


def test_compute_outcomes_does_not_crash_on_bare_string_measure(tmp_path: Path):
    """Before G3: AttributeError 'str' object has no attribute 'get' at the two filter
    comprehensions in compute_outcomes. After: returns a summary dict, no crash."""
    store_dir = tmp_path / "store"
    (store_dir / "history").mkdir(parents=True)
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    (study_dir / "study.yaml").write_text(
        _STUDY_YAML.format(store_path=str(store_dir)), encoding="utf-8"
    )

    # No viva_emitters resolver / reader needed to prove the FILTER path is crash-safe:
    # the store resolves (dir with history/) so the bare-string test flows into evaluation.
    summary = se.compute_outcomes(study_dir)

    assert isinstance(summary, dict)
    assert summary.get("runs_evaluated", 0) >= 1
    # the dict measure still grades; the bare-string one is handled (ungraded or graded),
    # never a crash — the whole point of G3.
    doc = yaml.safe_load((study_dir / "study.yaml").read_text())
    assert "computed_outcomes" in doc["runs"][0]
