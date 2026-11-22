"""reproducibility_gap: a study that RECORDS a run in its YAML but has NO registered runs.db
artifact asserts an unbacked `ran` — the run-side twin of an authored verdict with no evidence.
advance keys off the YAML run record and would skip it as "ran"; the engine must instead surface
the gap on the frontier (advisory, non-blocking, resolvable), never silently trust it."""
from __future__ import annotations

from discovery_engine import discover
from discovery_engine.projection import engine as de
from discovery_engine.phases.evidence_integrity import reconcile_evidence_state

_INV = {"studies": ["s1"]}


def _study(runs=None):
    s = {"name": "s1", "baseline": [{"name": "b", "composite": "x.y.z", "params": {}}]}
    if runs is not None:
        s["runs"] = runs
    return s


# --- detection in reconcile (has both the YAML records and the registered runs) -------------

def test_recorded_run_without_registered_artifact_is_a_gap():
    ev = reconcile_evidence_state(_INV, {"s1": _study(runs=[{"name": "r0", "run_id": "r0"}])},
                                  runs_by_study={"s1": []})   # no registered run on disk
    gaps = ev["reproducibility_gaps"]
    assert len(gaps) == 1
    assert gaps[0]["study"] == "s1" and gaps[0]["recorded_runs"] == ["r0"]
    assert "unreproducible" in gaps[0]["note"]


def test_registered_run_is_not_a_gap():
    ev = reconcile_evidence_state(_INV, {"s1": _study(runs=[{"name": "r0", "run_id": "r0"}])},
                                  runs_by_study={"s1": [{"run_id": "r0"}]})   # backed on disk
    assert ev["reproducibility_gaps"] == []


def test_no_recorded_run_is_not_a_gap():
    ev = reconcile_evidence_state(_INV, {"s1": _study(runs=[])}, runs_by_study={"s1": []})
    assert ev["reproducibility_gaps"] == []


def _study_status(**kw):
    s = {"name": "s1", "baseline": [{"name": "b", "composite": "x.y.z", "params": {}}]}
    s.update(kw)
    return s


def test_completed_status_without_registered_run_is_a_gap():
    # `completed` implies a run; with none registered (no runs[], no runs.db, no run log) it is an
    # unbacked completion claim — a reproducibility gap even without a recorded runs[] entry.
    for field in ("execution_status", "status"):
        ev = reconcile_evidence_state(_INV, {"s1": _study_status(**{field: "completed"})},
                                      runs_by_study={"s1": []})
        assert len(ev["reproducibility_gaps"]) == 1, field
        assert "completed/ran" in ev["reproducibility_gaps"][0]["note"]


def test_running_status_is_not_a_gap():
    # an in-progress study claims no completion
    ev = reconcile_evidence_state(_INV, {"s1": _study_status(status="running")}, runs_by_study={"s1": []})
    assert ev["reproducibility_gaps"] == []


def test_completed_status_with_registered_run_is_not_a_gap():
    ev = reconcile_evidence_state(_INV, {"s1": _study_status(status="completed")},
                                  runs_by_study={"s1": [{"run_id": "r0"}]})
    assert ev["reproducibility_gaps"] == []


# --- surfacing on the frontier (advisory, non-blocking, resolvable) --------------------------

def _cyc():
    return {"signals": {"reproducibility_gap": [
        {"study": "s1", "recorded_runs": ["r0"], "note": "unreproducible run"}]}}


def test_reproducibility_gap_surfaces_on_the_frontier():
    (it,) = [i for i in de.frontier(_cyc(), {}) if i["kind"] == "reproducibility_gap"]
    assert it["target"] == "s1"
    assert it["op"] == "revalidate"
    assert it["priority"] == de._PRIORITY["reproducibility_gap"] == 44


def test_reproducibility_gap_is_advisory_non_blocking():
    # it must NOT be auto-dispatched by advance, and must not keep the investigation open
    from discovery_engine.discover.cycle import _ADVANCE_OPS
    assert "revalidate" not in _ADVANCE_OPS
    assert "reproducibility_gap" not in de._CLOSURE_BLOCKING
    assert de.closure(_cyc(), {})["closed"] is True


def test_reproducibility_gap_is_resolvable():
    assert "reproducibility_gap" in discover.RESOLUTION_KINDS
    inv = {"resolutions": [{"kind": "reproducibility_gap", "target": "s1",
                            "rationale": "acknowledged read-only snapshot"}]}
    assert not [i for i in de.frontier(_cyc(), inv) if i["kind"] == "reproducibility_gap"]


# --- end-to-end: the LIVE operator path (_cycle_with_evidence -> signals -> opportunities) -----

def test_reproducibility_gap_surfaces_on_the_live_cycle(tmp_path):
    """Regression for the integration seam: the detector feeds reconcile_evidence_state, which
    feeds run_cycle's signals via _cycle_with_evidence, which feeds opportunities. A study that
    records a run with NO registered run (no runs.db, no run-log entry) must surface a
    reproducibility_gap on the LIVE cycle an operator sees — not just via a hand-fed signal."""
    from discovery_engine.study.io import save_yaml_atomic
    from discovery_engine.discover.cycle import load_cycle_inputs, _cycle_with_evidence
    (tmp_path / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    inv = tmp_path / "investigations" / "demo"
    (tmp_path / "studies" / "s1").mkdir(parents=True)
    inv.mkdir(parents=True)
    save_yaml_atomic(inv / "investigation.yaml", {"name": "demo", "studies": ["s1"]})
    save_yaml_atomic(tmp_path / "studies" / "s1" / "study.yaml", {
        "schema_version": 4, "name": "s1", "investigation": "demo", "claim": "X",
        "baseline": [{"name": "b", "composite": "json.tool.main", "params": {}}],
        "observables": ["obs.val"],
        "runs": [{"name": "r0", "run_id": "r0", "params": {}}]})   # recorded, but no runs.db / run-log

    _, inv_spec, studies = load_cycle_inputs(str(tmp_path), "demo")
    result = _cycle_with_evidence(str(tmp_path), "demo", inv_spec, studies)
    assert [g["study"] for g in result["signals"]["reproducibility_gap"]] == ["s1"]
    opps = de.opportunities(result, inv_spec)
    assert any(o.get("action_type") == "revalidate" and str(o.get("source", "")).endswith("s1")
               for o in opps), "reproducibility_gap did not reach the live opportunities"


def test_reproducibility_gap_surfaces_via_the_opportunities_cli(tmp_path, capsys):
    """The actual CLI COMMAND surface (not just the in-process cycle): `discover opportunities
    <inv>` on a genuinely-artifactless workspace must PRINT the reproducibility_gap (op
    revalidate). Guards the cycle+engine -> CLI-handler seam, which the in-process test skips."""
    from discovery_engine.study.io import save_yaml_atomic
    (tmp_path / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    inv = tmp_path / "investigations" / "demo"
    (tmp_path / "studies" / "s1").mkdir(parents=True)
    inv.mkdir(parents=True)
    save_yaml_atomic(inv / "investigation.yaml", {"name": "demo", "studies": ["s1"]})
    save_yaml_atomic(tmp_path / "studies" / "s1" / "study.yaml", {
        "schema_version": 4, "name": "s1", "investigation": "demo", "claim": "X",
        "baseline": [{"name": "b", "composite": "json.tool.main", "params": {}}],
        "observables": ["obs.val"], "runs": [{"name": "r0", "run_id": "r0", "params": {}}]})
    rc = discover.main(["--workspace", str(tmp_path), "opportunities", "demo"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "revalidate" in out and "s1" in out, f"reproducibility_gap not in CLI output:\n{out}"
