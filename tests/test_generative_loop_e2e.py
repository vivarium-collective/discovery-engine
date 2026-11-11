"""PR 2 — the keystone: derive -> AUTHOR -> run, end to end.

The generative loop used to stop at a proposal: a refuted finding produced a child-study
SEED (pure), but nothing promoted it to disk under autonomy — a human had to run
`seed-study --write`. PR 2 makes "a refuted finding proposes the next study" into "runs the
next study": the proposal becomes a `proposed_study` frontier item (op `seed`, consequence
`authoring`), and `advance` promotes it via `write_child_study` + registers it as a member,
so the next crank picks up the new study's own `test` op.

This test pins the whole seam on a real on-disk workspace: proposed_study appears -> advance
writes + registers the child study.yaml -> the promoted finding is not re-proposed (idempotent).
"""
from __future__ import annotations

from pathlib import Path

import yaml

from discovery_engine.discover import advance_cycle, load_cycle_inputs, suggest_studies
from discovery_engine.projection import engine as de


def _noop_runner(command, ws_root, *, worktree=None, **kw):
    # The seam under test writes a study; it never needs a real simulation. A stub keeps any
    # incidental run-op from shelling out.
    return {"ok": True, "returncode": 0, "run_id": "stub-run", "tail": ""}


def _ws_with_refuted_finding(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "investigations" / "gen-e2e").mkdir(parents=True)
    (ws / "studies" / "s1").mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: gen-e2e-ws\n", encoding="utf-8")
    (ws / "investigations" / "gen-e2e" / "investigation.yaml").write_text(
        yaml.safe_dump({"name": "gen-e2e", "studies": ["s1"]}, sort_keys=False), encoding="utf-8")
    s1 = {
        "schema_version": 4, "name": "s1", "investigation": "gen-e2e",
        "claim": "X holds under flow",
        "baseline": [{"name": "b1", "composite": "pkg.composites.x"}],
        "behavior_tests": [
            {"name": "x-holds", "measure": {"kind": "scalar"}, "pass_if": {"op": ">", "value": 0}},
        ],
        "findings": [
            {"id": "f1", "kind": "biological", "status": "contradicts",
             "statement": "X does NOT hold under flow — the collective disperses.",
             "evidence": {"from_test": "x-holds"},
             "explanation": "flow shear exceeds the adhesion the current model allows.",
             "next_action": "Test whether raising adhesion restores the collective under flow."},
        ],
    }
    (ws / "studies" / "s1" / "study.yaml").write_text(yaml.safe_dump(s1, sort_keys=False),
                                                      encoding="utf-8")
    return ws


def test_refuted_finding_becomes_a_proposed_study_frontier_item(tmp_path):
    ws = _ws_with_refuted_finding(tmp_path)
    _, inv_spec, studies = load_cycle_inputs(ws, "gen-e2e")
    sugg = suggest_studies({}, inv_spec, studies)
    fr = de.frontier({"suggestions": sugg}, inv_spec)
    seeds = [i for i in fr if i["kind"] == "proposed_study"]
    assert seeds, f"expected a proposed_study item, got kinds {[i['kind'] for i in fr]}"
    assert seeds[0]["op"] == "seed"


def test_advance_promotes_the_seed_to_a_registered_study(tmp_path):
    ws = _ws_with_refuted_finding(tmp_path)
    out = advance_cycle(ws, "gen-e2e", autonomous=True, runner=_noop_runner, max_runs=6)

    # a seed op executed
    seeded = [r for r in out["runs"] if str((r.get("outcome") or "")).startswith("seeded")]
    assert seeded, f"no seed promotion in advance runs: {out['runs']}"

    # the child study.yaml is on disk, derived from the finding
    child_dirs = [p for p in (ws / "studies").iterdir() if p.is_dir() and p.name != "s1"]
    assert len(child_dirs) == 1, f"expected exactly one child study dir, got {child_dirs}"
    child = yaml.safe_load((child_dirs[0] / "study.yaml").read_text())
    assert child["seeded_from"]["finding"] == "f1"
    assert child["seeded_from"]["study"] == "s1"
    assert child.get("purpose", {}).get("question")      # derived from the finding

    # and it is REGISTERED as a member — the next crank sees it (loop can chain)
    _, _inv2, studies2 = load_cycle_inputs(ws, "gen-e2e")
    assert child_dirs[0].name in studies2


def test_promotion_is_idempotent(tmp_path):
    ws = _ws_with_refuted_finding(tmp_path)
    advance_cycle(ws, "gen-e2e", autonomous=True, runner=_noop_runner, max_runs=6)

    # after promotion the refuted finding is NOT re-proposed (no infinite seed stream)
    _, inv2, studies2 = load_cycle_inputs(ws, "gen-e2e")
    sugg2 = suggest_studies({}, inv2, studies2)
    assert not [s for s in sugg2 if s.get("finding_id") == "f1"]
    fr2 = de.frontier({"suggestions": sugg2}, inv2)
    assert not [i for i in fr2 if i["kind"] == "proposed_study"]

    # a second advance writes no further child (one child total)
    advance_cycle(ws, "gen-e2e", autonomous=True, runner=_noop_runner, max_runs=6)
    child_dirs = [p for p in (ws / "studies").iterdir() if p.is_dir() and p.name != "s1"]
    assert len(child_dirs) == 1
