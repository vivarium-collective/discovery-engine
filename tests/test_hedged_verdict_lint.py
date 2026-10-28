"""The hedged-verdict linter check: a PASSED study must state its conclusion in
the indicative, not hedge it ("should"/"likely"/"seems"). Wording symptom from
obra's verification-before-completion lesson."""
from pathlib import Path

from discovery_engine.report_linter import (
    _LintContext,
    _check_hedged_verdict_when_passed,
)


def _run(spec: dict):
    ctx = _LintContext(ws_root=Path("."), slug="s", spec=spec)
    _check_hedged_verdict_when_passed(ctx)
    return ctx.findings


def test_passed_study_with_hedged_conclusion_is_flagged():
    findings = _run({
        "gate_status": "passed",
        "conclusion_logic": {
            "if_primary_tests_pass": "the mechanism should reproduce the band",
        },
    })
    assert len(findings) == 1
    f = findings[0]
    assert f.check == "hedged_verdict_when_passed"
    assert f.level == "warning"
    assert "should" in f.message


def test_passed_study_with_indicative_conclusion_is_clean():
    findings = _run({
        "gate_status": "passed",
        "conclusion_logic": {
            "if_primary_tests_pass": "the mechanism reproduces the band at 28 cpc",
        },
    })
    assert findings == []


def test_hedge_words_ignored_when_not_passed():
    # A non-passed study may legitimately hedge — the check only fires on a pass.
    findings = _run({
        "gate_status": "blocked",
        "conclusion_logic": {"if_primary_tests_fail": "the band likely won't hold"},
    })
    assert findings == []


def test_hedge_in_executive_block_is_flagged():
    findings = _run({
        "gate_status": "passed",
        "executive": {"verdict_detail": "results seem consistent with the target"},
    })
    assert len(findings) == 1
    assert findings[0].field_path == "executive"
