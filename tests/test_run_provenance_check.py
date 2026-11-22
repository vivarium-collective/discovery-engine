"""Tests for the run-provenance linter check (_check_run_provenance).

Every run's run_identity.json sidecar must pin BOTH code.commit (reproducible)
and a non-empty model_config (the model version / toggle profile — reversible).
"""
import json

from discovery_engine.reporting.linter.context import _LintContext
from discovery_engine.reporting.linter.checks_structure import _check_run_provenance


def _ws(tmp_path):
    (tmp_path / "workspace.yaml").write_text(
        "schema_version: 2\nname: ws\npackage_path: pbg_ws\n")
    return tmp_path


def _write_identity(tmp_path, slug, run_id, identity):
    run_dir = tmp_path / "studies" / slug / "parquet-runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "run_identity.json").write_text(json.dumps(identity))


def _run(tmp_path, slug="s1", confidence="Investigating"):
    ctx = _LintContext(ws_root=tmp_path, slug=slug,
                       spec={"name": slug, "confidence": confidence})
    _check_run_provenance(ctx)
    return [f for f in ctx.findings if f.check == "run_provenance"]


def test_complete_provenance_passes(tmp_path):
    _ws(tmp_path)
    _write_identity(tmp_path, "s1", "run1", {
        "code": {"commit": "abc123", "dirty": False},
        "model_config": {"ppgpp_regulation": True, "ppgpp_gtp_brake": False},
        "design": {"experiment_id": "run1"},
    })
    assert _run(tmp_path) == []


def test_empty_model_config_warns(tmp_path):
    _ws(tmp_path)
    _write_identity(tmp_path, "s1", "run1", {
        "code": {"commit": "abc123", "dirty": False},
        "model_config": {},
        "design": {"experiment_id": "run1"},
    })
    msgs = [f.message for f in _run(tmp_path)]
    assert any("model_config" in m for m in msgs)


def test_missing_commit_warns(tmp_path):
    _ws(tmp_path)
    _write_identity(tmp_path, "s1", "run1", {
        "code": {"commit": None, "dirty": False},
        "model_config": {"ppgpp_regulation": True},
        "design": {"experiment_id": "run1"},
    })
    msgs = [f.message for f in _run(tmp_path)]
    assert any("code.commit" in m or "not reproducible" in m for m in msgs)


def test_dirty_tree_warns_only_when_accepted(tmp_path):
    _ws(tmp_path)
    _write_identity(tmp_path, "s1", "run1", {
        "code": {"commit": "abc123", "dirty": True},
        "model_config": {"ppgpp_regulation": True},
        "design": {"experiment_id": "run1"},
    })
    # Investigating: a dirty experiment run is fine.
    assert not any("DIRTY" in f.message for f in _run(tmp_path, confidence="Investigating"))
    # Accepted: a dirty tree backing an accepted version is flagged.
    assert any("DIRTY" in f.message for f in _run(tmp_path, confidence="Accepted"))


def test_no_sidecars_is_silent(tmp_path):
    _ws(tmp_path)
    (tmp_path / "studies" / "s1").mkdir(parents=True)
    assert _run(tmp_path) == []
