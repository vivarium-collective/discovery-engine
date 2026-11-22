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


# --- operator visibility: advisories are always printed, even below the ranked fold ----------

def _opp(kind, source, score, op="test", note=""):
    return {"action_type": op, "source": source, "kind": kind, "priority": 44, "question": note,
            "confidence": 0.4, "value": {"score": score, "info": 0.3, "impact": 0, "enable": 0,
                                         "cost": 0.2, "discriminate": 0}}


def test_summarize_opportunities_always_shows_advisories_below_the_fold():
    # A busy frontier: two high-value ops crowd a low-ranked reproducibility_gap below the printed
    # top-N. The gap (an unbacked run claim) must still appear, on a dedicated advisory line.
    opps = [_opp("open_question", "claim:a", 0.9), _opp("open_question", "claim:b", 0.8),
            _opp("reproducibility_gap", "s9", 0.03, op="revalidate",
                 note="study records a run with no registered runs.db artifact")]
    out = de.summarize_opportunities(opps, top=2)
    assert "integrity advisory" in out
    assert "revalidate" in out and "s9" in out
    assert "no registered runs.db" in out          # the note is shown, not just the op


def test_summarize_opportunities_does_not_double_list_a_top_ranked_advisory():
    # an advisory already in the printed top-N is not repeated in the advisory section
    opps = [_opp("reproducibility_gap", "s1", 0.9, op="revalidate", note="unbacked")]
    out = de.summarize_opportunities(opps, top=10)
    assert out.count("s1") == 1
    assert "below the top" not in out
