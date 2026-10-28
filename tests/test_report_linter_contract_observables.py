"""P3 Task 3 — report_linter surfaces declared-but-unemitted contract.face ports."""
from pathlib import Path

import discovery_engine.report_linter as rl


def _ctx(spec):
    return rl._LintContext(ws_root=Path("/ws"), slug="s", spec=spec)


def test_check_registered():
    assert rl._check_contract_observables in rl._CHECK_FUNCTIONS


def test_warns_on_gap(monkeypatch):
    monkeypatch.setattr(
        rl,
        "reconcile_study_observables",
        lambda spec, ws_root: [
            {
                "severity": "warning",
                "check": "contract-observable",
                "message": "outputs port 'heat_flux' declared in contract.face is not emitted by the latest run",
            }
        ],
    )
    ctx = _ctx({"contract": {"face": {"outputs": {"heat_flux": {}}}}})
    rl._check_contract_observables(ctx)
    assert any(
        f.check == "contract-observable" and f.level == "warning" for f in ctx.findings
    )
    assert any("heat_flux" in f.message for f in ctx.findings)


def test_silent_without_findings(monkeypatch):
    monkeypatch.setattr(rl, "reconcile_study_observables", lambda spec, ws_root: [])
    ctx = _ctx({"name": "s"})
    rl._check_contract_observables(ctx)
    assert ctx.findings == []


def test_resolver_error_never_breaks_lint(monkeypatch):
    def boom(spec, ws_root):
        raise RuntimeError("resolver exploded")

    monkeypatch.setattr(rl, "reconcile_study_observables", boom)
    ctx = _ctx({"contract": {"face": {"outputs": {"x": {}}}}})
    rl._check_contract_observables(ctx)  # must not raise
    assert ctx.findings == []
