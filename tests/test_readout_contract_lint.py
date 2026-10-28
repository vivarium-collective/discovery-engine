"""Lint check that wires the #1396 readout↔contract audit into the lint gate.

The workbench already validates every study readout against its baseline
composites' real structure at ``GET /api/study-observable-check`` (runs
``available_observables`` + ``validate_readouts`` in the env worker). These
tests cover ``_check_readout_contract``, which surfaces that verdict as lint
findings so a never-fabricate ``not_in_structure`` — a readout pointing at an
observable no baseline composite exposes — blocks a reviewer-facing render.

The check reaches a *running* workbench (it reuses the server's already-built
composites) and skips gracefully offline, so the server-fetch seam
(``fetch_study_observable_check``) is monkeypatched here — no live server.
"""
from __future__ import annotations

from pathlib import Path

from discovery_engine import report_linter
from discovery_engine.report_linter import _LintContext, _check_readout_contract


def _ctx(tmp_path: Path, readouts: list[dict] | None = None) -> _LintContext:
    return _LintContext(
        ws_root=tmp_path,
        slug="s1",
        spec={"readouts": readouts or []},
    )


def _patch_fetch(monkeypatch, payload):
    monkeypatch.setattr(
        report_linter, "fetch_study_observable_check", lambda ws_root, slug, **k: payload
    )


def test_no_server_is_a_graceful_skip(monkeypatch, tmp_path):
    """Offline (no running workbench) → the check fires nothing, so the
    linter stays deterministic in CI."""
    _patch_fetch(monkeypatch, None)
    ctx = _ctx(tmp_path, [{"name": "x", "identifier": "listeners.foo[0]"}])
    _check_readout_contract(ctx)
    assert ctx.findings == []


def test_not_in_structure_is_a_blocking_error(monkeypatch, tmp_path):
    """The never-fabricate flag: a selector no baseline exposes → error."""
    _patch_fetch(monkeypatch, {
        "composite": "v2ecoli.composites.ecoli_baseline.ecoli_baseline",
        "readouts": [
            {"name": "ghost readout", "status": "not_in_structure",
             "detail": "listeners.not_a_real_path not exposed by any baseline"},
        ],
    })
    ctx = _ctx(tmp_path)
    _check_readout_contract(ctx)
    assert len(ctx.findings) == 1
    f = ctx.findings[0]
    assert f.level == "error"
    assert f.check == "readout_contract"
    assert "ghost readout" in f.message
    assert "not_a_real_path" in f.message


def test_unit_mismatch_is_a_warning(monkeypatch, tmp_path):
    """#1396's typed check: a declared unit dimensionally incompatible with
    the port contract → warning (version-tolerant, so not a hard block)."""
    _patch_fetch(monkeypatch, {
        "readouts": [
            {"name": "mass in seconds", "status": "unit_mismatch",
             "detail": "declared 'second' vs port contract 'femtogram'"},
        ],
    })
    ctx = _ctx(tmp_path)
    _check_readout_contract(ctx)
    assert len(ctx.findings) == 1
    assert ctx.findings[0].level == "warning"
    assert ctx.findings[0].check == "readout_contract"


def test_ok_aspirational_unresolved_produce_no_contract_findings(monkeypatch, tmp_path):
    """``ok`` is clean; ``aspirational`` is runtime-only (can't be statically
    checked); ``unresolved`` is owned by the readout-migration check — none are
    this check's concern."""
    _patch_fetch(monkeypatch, {
        "readouts": [
            {"name": "a", "status": "ok"},
            {"name": "b", "status": "aspirational", "detail": "bulk id, runtime-only"},
            {"name": "c", "status": "unresolved", "detail": "prose"},
        ],
    })
    ctx = _ctx(tmp_path)
    _check_readout_contract(ctx)
    assert ctx.findings == []


def test_study_without_baseline_is_a_graceful_skip(monkeypatch, tmp_path):
    """A 422-shaped payload (no baseline composite) carries no readouts → skip,
    not a crash."""
    _patch_fetch(monkeypatch, {"error": "study has no baseline composite", "readouts": []})
    ctx = _ctx(tmp_path)
    _check_readout_contract(ctx)
    assert ctx.findings == []


def test_mixed_payload_reports_only_the_flagged_readouts(monkeypatch, tmp_path):
    """Several readouts, mixed statuses → exactly the not_in_structure (error)
    and unit_mismatch (warning) surface."""
    _patch_fetch(monkeypatch, {
        "readouts": [
            {"name": "ok one", "status": "ok"},
            {"name": "ghost", "status": "not_in_structure", "detail": "missing"},
            {"name": "bad unit", "status": "unit_mismatch", "detail": "s vs fg"},
            {"name": "runtime", "status": "aspirational"},
        ],
    })
    ctx = _ctx(tmp_path)
    _check_readout_contract(ctx)
    levels = sorted((f.level, f.message.split(":")[0]) for f in ctx.findings)
    assert len(ctx.findings) == 2
    assert {f.level for f in ctx.findings} == {"error", "warning"}
