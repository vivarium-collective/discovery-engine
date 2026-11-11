"""PR 3 — chain promotion -> run in one advance, and promote ALL ready, parent-bearing
proposals (not just refuted-finding seeds).

PR 2 closed the seam (a refuted finding becomes a promotable `proposed_study`). PR 3 pins
that the full loop RUNS in a single `advance --autonomous` (seed then run the child), and
broadens promotion to authored `followup_proposals` and audit narrative-gap followups — the
"author a study to move the investigation forward" intent the dead `branch` placeholder
never executed, now handled by the working authoring path.
"""
from __future__ import annotations

from pathlib import Path

import yaml

from discovery_engine.discover import advance_cycle, load_cycle_inputs, suggest_studies
from discovery_engine.projection import engine as de


def _ws(tmp_path: Path, s1_extra: dict) -> Path:
    ws = tmp_path / "ws"
    (ws / "investigations" / "g").mkdir(parents=True)
    (ws / "studies" / "s1").mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: w\n", encoding="utf-8")
    (ws / "investigations" / "g" / "investigation.yaml").write_text(
        yaml.safe_dump({"name": "g", "studies": ["s1"]}, sort_keys=False), encoding="utf-8")
    s1 = {"schema_version": 4, "name": "s1", "investigation": "g", "claim": "X holds",
          "behavior_tests": [{"name": "x", "measure": {"kind": "scalar"},
                              "pass_if": {"op": ">", "value": 0}}], **s1_extra}
    (ws / "studies" / "s1" / "study.yaml").write_text(yaml.safe_dump(s1, sort_keys=False),
                                                      encoding="utf-8")
    return ws


_REFUTED = {"findings": [
    {"id": "f1", "status": "contradicts", "statement": "X fails under flow",
     "evidence": {"from_test": "x"}, "explanation": "shear exceeds adhesion",
     "next_action": "raise adhesion and re-test under flow"}]}


def test_advance_seeds_and_runs_child_in_one_invocation(tmp_path):
    """The whole loop in ONE `advance --autonomous`: the child study is written AND run."""
    ws = _ws(tmp_path, _REFUTED)
    calls: list[str] = []

    def rec_runner(command, ws_root, *, worktree=None, **kw):
        calls.append(command)
        return {"ok": True, "returncode": 0, "run_id": f"r{len(calls)}", "tail": ""}

    advance_cycle(ws, "g", autonomous=True, runner=rec_runner, max_runs=8)

    child_dirs = [p.name for p in (ws / "studies").iterdir() if p.is_dir() and p.name != "s1"]
    assert len(child_dirs) == 1, f"expected one child, got {child_dirs}"
    child = child_dirs[0]
    # the child was both authored AND run in this single invocation (derive -> author -> run)
    assert any(child in c for c in calls), f"child never ran; runner calls were: {calls}"


def test_authored_followup_proposal_is_promotable(tmp_path):
    """A ready `followup_proposal` (no refuted finding, no pre-built seed) is promoted via the
    proposal path — the 'author a study to move forward' intent, now live."""
    ws = _ws(tmp_path, {"followup_proposals": [
        {"id": "fp-adhesion", "title": "Adhesion sweep under flow",
         "motivation": "Map the adhesion threshold that keeps the collective intact.",
         "status": "proposed"}]})

    _, inv, studies = load_cycle_inputs(ws, "g")
    sugg = suggest_studies({}, inv, studies)
    fr = de.frontier({"suggestions": sugg}, inv)
    seeds = [i for i in fr if i["kind"] == "proposed_study"]
    assert [i["target"] for i in seeds] == ["fp-adhesion"]

    advance_cycle(ws, "g", autonomous=True, runner=lambda *a, **k: {"ok": True, "run_id": "r"},
                  max_runs=6)
    child_dirs = [p for p in (ws / "studies").iterdir() if p.is_dir() and p.name != "s1"]
    assert len(child_dirs) == 1
    child = yaml.safe_load((child_dirs[0] / "study.yaml").read_text())
    assert child["seeded_from"]["proposal_id"] == "fp-adhesion"


def test_promoted_proposal_is_not_re_proposed(tmp_path):
    """Idempotent across sources: once a proposal is promoted (recorded in seeded_from.
    proposal_id) it drops out of suggestions, so advance doesn't re-seed it forever."""
    ws = _ws(tmp_path, {"followup_proposals": [
        {"id": "fp-adhesion", "title": "Adhesion sweep", "motivation": "m", "status": "proposed"}]})
    advance_cycle(ws, "g", autonomous=True, runner=lambda *a, **k: {"ok": True, "run_id": "r"},
                  max_runs=6)

    _, inv2, studies2 = load_cycle_inputs(ws, "g")
    sugg2 = suggest_studies({}, inv2, studies2)
    assert not [s for s in sugg2 if s["id"] == "fp-adhesion"]

    advance_cycle(ws, "g", autonomous=True, runner=lambda *a, **k: {"ok": True, "run_id": "r"},
                  max_runs=6)
    child_dirs = [p for p in (ws / "studies").iterdir() if p.is_dir() and p.name != "s1"]
    assert len(child_dirs) == 1   # still exactly one child
