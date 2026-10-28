"""Single home for the report-card / test verdict vocabulary and its mappings.

Canonical verdicts are ``within_tol | drift | mismatch | ungraded``; this module
owns them plus the alias-normalization, worst-of rollup, and the two projection
maps (agent semantics and four-value display) that were previously duplicated
across v2ecoli.library.report_card, workbench study_spec/study_page/conclusion_card.
Pure stdlib; no process_bigraph / vivarium_workbench import.
"""
from __future__ import annotations

CANONICAL = ("within_tol", "drift", "mismatch", "ungraded")
SEVERITY = ("hard", "soft", "directional")

COLOR = {"within_tol": "#1a7f37", "drift": "#ef6c00",
         "mismatch": "#c62828", "ungraded": "#757575"}
GLYPH = {"within_tol": "✓", "drift": "≈", "mismatch": "✗", "ungraded": "–"}
RANK = {"mismatch": 3, "drift": 2, "within_tol": 1, "ungraded": 0}

_ALIASES = {
    "within_tol": "within_tol", "pass": "within_tol", "passed": "within_tol",
    "ok": "within_tol", "met": "within_tol", "passing": "within_tol",
    "mismatch": "mismatch", "fail": "mismatch", "failed": "mismatch",
    "failing": "mismatch", "not met": "mismatch",
    "drift": "drift", "partial": "drift", "warn": "drift",
    "conditional-pass": "drift", "conditional_pass": "drift",
    "ungraded": "ungraded", "skip": "ungraded", "skipped": "ungraded",
    "pending": "ungraded", "gap": "ungraded", "not assessable": "ungraded",
}
_AGENT = {"within_tol": "pass", "mismatch": "fail", "drift": "warn", "ungraded": "no-data"}
_DISPLAY = {"within_tol": "met", "mismatch": "not met",
            "drift": "conditional-pass", "ungraded": "not assessable"}


def normalize_verdict(v):
    if not v:
        return "ungraded"
    return _ALIASES.get(str(v).strip().lower(), "ungraded")


def worst(verdicts):
    w = "ungraded"
    for v in verdicts:
        n = normalize_verdict(v)
        if RANK[n] > RANK[w]:
            w = n
    return w


def agent_status(verdict):
    return _AGENT[normalize_verdict(verdict)]


def display_status(verdict):
    return _DISPLAY[normalize_verdict(verdict)]


_RESULT = {"within_tol": "PASS", "drift": "PASS", "mismatch": "FAIL", "ungraded": "SKIP"}


def verdict_to_result(verdict) -> str:
    """Project a canonical verdict to the PASS/FAIL/SKIP a study test carries.
    ``drift`` (a soft/directional warning) still PASSES the gate; ``ungraded``
    (no gradable data) is SKIP."""
    return _RESULT[normalize_verdict(verdict)]
