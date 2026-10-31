"""Derive a study's status from execution state (don't trust hand-set fields).

Integration friction (2026-05-22): a report shipped
``status: planning-with-baseline-confirmed`` and a "for expert review prior to
execution" tagline *after all 8 studies had executed with verdicts*. The
expert and the user caught it — no check did. The authored status string had
drifted from reality.

The robust fix is **derive-on-read**: compute the observably-derivable status
axes from execution state (runs.db + recorded verdicts) instead of trusting a
hand-maintained field that can't keep up. This module is the shared core both
the dashboard (live render) and the report (export) call, so a study's status
reflects what actually ran — by construction, it can't go stale.

Multi-axis status vocabulary: design / implementation / simulation /
evaluation / gate / expert_review. Of these:

- ``simulation_status`` and ``evaluation_status`` are **observable** — fully
  derivable from runs + verdicts. This module derives them.
- ``design_status`` / ``implementation_status`` / ``expert_review_status`` are
  **authored intent** (was it designed? is the code written? did an expert
  sign off?) — not derivable from a run, so they pass through unchanged.
- ``gate_status`` mixes authored prerequisites with test outcomes; deriving it
  needs the test pass/fail signal and is left for a follow-up.

``status_disagreements`` reports where a *stored* axis contradicts its derived
value, so the report can surface "stored: planning / actual: ran" the way the
param-enforcement banner surfaces declared-vs-applied drift.
"""
from __future__ import annotations

from .study_outcomes import canonical_run


# runs_meta.status vocabulary → our coarse buckets. "ran" is the v2/legacy
# completed marker; "complete"/"completed" are the canonical ones.
_RUN_COMPLETE = {"complete", "completed", "ran", "done"}
_RUN_RUNNING = {"running"}
# failed / orphaned count as "attempted" but not a clean completion.


def derive_simulation_status(runs: list[dict] | None) -> str:
    """``ran`` if any run completed, ``running`` if one is in progress, else
    ``not_run``. Derived purely from runs.db rows."""
    has_complete = False
    has_running = False
    for r in runs or []:
        st = str((r or {}).get("status") or "").strip().lower()
        if st in _RUN_COMPLETE:
            has_complete = True
        elif st in _RUN_RUNNING:
            has_running = True
    if has_complete:
        return "ran"
    if has_running:
        return "running"
    return "not_run"


def _has_recorded_verdicts(spec: dict) -> bool:
    """True if the study carries evaluation output (verdicts / findings)."""
    for key in ("conclusion_verdicts", "findings"):
        v = spec.get(key)
        if isinstance(v, (list, dict)) and len(v) > 0:
            return True
    return False


def derive_evaluation_status(spec: dict, runs: list[dict] | None,
                             *, has_verdicts: bool | None = None) -> str:
    """``evaluated`` only when a run completed AND verdicts/findings exist;
    otherwise ``not_evaluated``. You can't evaluate what hasn't run."""
    if derive_simulation_status(runs) != "ran":
        return "not_evaluated"
    if has_verdicts is None:
        has_verdicts = _has_recorded_verdicts(spec)
    return "evaluated" if has_verdicts else "not_evaluated"


# Which axes this module derives, and the function that derives each.
_DERIVABLE_AXES = ("simulation_status", "evaluation_status")


def derive_status(spec: dict, runs: list[dict] | None,
                  *, has_verdicts: bool | None = None) -> dict[str, dict]:
    """Return ``{axis: {"value": str, "source": str}}`` for the derivable axes.

    Only the observable axes (simulation, evaluation) are returned — authored
    axes aren't derivable and are left to the spec. ``source`` is a short
    human string for the report ("3 completed run(s)").
    """
    runs = runs or []
    n_complete = sum(
        1 for r in runs
        if str((r or {}).get("status") or "").strip().lower() in _RUN_COMPLETE
    )
    sim = derive_simulation_status(runs)
    evalst = derive_evaluation_status(spec, runs, has_verdicts=has_verdicts)
    sim_source = (
        f"{n_complete} completed run(s)" if n_complete
        else ("a run in progress" if sim == "running" else "no runs in runs.db")
    )
    eval_source = (
        "completed run + recorded verdicts" if evalst == "evaluated"
        else ("no verdicts recorded yet" if sim == "ran" else "nothing run to evaluate")
    )
    return {
        "simulation_status": {"value": sim, "source": sim_source},
        "evaluation_status": {"value": evalst, "source": eval_source},
    }


# ---------------------------------------------------------------------------
# Clarity summary — a single normalized "did it run / were the tests run / did
# it pass" object that every renderer (the viva-report slim renderer and the
# dashboard downloadable report) reads, so the run/test/verdict markers are derived ONCE
# and shown consistently everywhere. Driven by reviewer feedback on
# dnaa-replication (2026-05-31): "make it clear which studies have run, if the
# tests were run and if the study passed."
# ---------------------------------------------------------------------------

_TEST_PASS = {"pass", "passed", "ok"}
_TEST_FAIL = {"fail", "failed", "error"}
_TEST_SKIP = {"skip", "skipped", "inconclusive", "partial"}
# A test with no recorded run-outcome renders as "pending" (the report keys the
# pill off the latest run's outcomes, NOT the test's own status field).


def _study_tests(spec: dict) -> list[dict]:
    """The test list a report shows, in renderer-precedence order."""
    for key in ("tests", "behavior_tests", "expected_behavior"):
        v = spec.get(key)
        if isinstance(v, list) and v:
            return [t for t in v if isinstance(t, dict)]
    return []


def _latest_run(runs: list[dict] | None) -> dict | None:
    """The run a report reads outcomes from: the last entry in ``runs``."""
    runs = runs or []
    return runs[-1] if runs else None


def bucket_tests(tests: list[dict], outcomes: dict) -> dict[str, list[str]]:
    """Bucket each test into pass/fail/skip/pending NAME-lists by the result a
    report would render for it: ``outcomes[name].result`` lowercased, matched
    against ``_TEST_PASS`` / ``_TEST_FAIL`` / ``_TEST_SKIP`` (no matching outcome
    → ``pending``).

    This is the single source of the per-test pass/fail/skip/pending
    classification shared by :func:`count_test_outcomes` (which takes ``len`` of
    each list) and :func:`study_verdict.roll_up_verdict` (which uses the names).
    Callers select the ``outcomes`` source themselves, so this helper stays a
    pure classification of a given outcomes map.
    """
    buckets: dict[str, list[str]] = {"pass": [], "fail": [], "skip": [], "pending": []}
    for t in tests:
        name = t.get("name") or ""
        out = outcomes.get(name)
        res = out.get("result") if isinstance(out, dict) else out
        r = str(res or "").strip().lower()
        if r in _TEST_PASS:
            buckets["pass"].append(name)
        elif r in _TEST_FAIL:
            buckets["fail"].append(name)
        elif r in _TEST_SKIP:
            buckets["skip"].append(name)
        else:
            buckets["pending"].append(name)
    return buckets


def count_test_outcomes(spec: dict, runs: list[dict] | None) -> dict:
    """Count tests by the result a report WOULD render for each.

    Mirrors the renderer exactly: the pill comes from the latest run's
    ``outcomes[test_name].result`` (PASS/FAIL/SKIP); a test with no matching
    outcome renders ``pending`` regardless of its own ``status`` field. Returns
    ``{total, pass, fail, skip, pending}``.
    """
    tests = _study_tests(spec)
    chosen = canonical_run(runs if runs is not None else spec.get("runs"))
    outcomes = (chosen or {}).get("outcomes") or {}
    buckets = bucket_tests(tests, outcomes)
    return {
        "total": len(tests),
        "pass": len(buckets["pass"]),
        "fail": len(buckets["fail"]),
        "skip": len(buckets["skip"]),
        "pending": len(buckets["pending"]),
    }


def study_clarity_summary(spec: dict, runs: list[dict] | None = None) -> dict:
    """One normalized run/test/verdict object for the reviewer-facing strip.

    Returns::

        {
          "ran":     {"status": "ran"|"running"|"not_run", "n_runs": int,
                      "latest": str|None, "label": str},
          "tests":   {"total","pass","fail","skip","pending": int, "label": str},
          "verdict": {"value": str, "label": str, "glyph": str, "cls": str},
          "ambiguities": [str, ...],
        }

    ``ran`` is derived from ``runs`` (observable). ``tests`` mirrors the pill the
    report would render. ``verdict`` prefers the authored ``gate_status`` (the
    pipeline verdict) and otherwise derives from the test results. Anything that
    would read ambiguously to a reviewer is listed in ``ambiguities`` (the
    report-linter surfaces these; the strip can show a ⚠ marker).
    """
    runs = runs if runs is not None else (spec.get("runs") or [])
    n_complete = sum(
        1 for r in runs
        if str((r or {}).get("status") or "").strip().lower() in _RUN_COMPLETE
    )
    sim = derive_simulation_status(runs)
    latest = _latest_run(runs)
    ran_label = (
        f"Ran · {n_complete} run{'s' if n_complete != 1 else ''}" if sim == "ran"
        else "Running…" if sim == "running" else "Not run"
    )
    ran = {"status": sim, "n_runs": n_complete,
           "latest": (latest or {}).get("name"), "label": ran_label}

    tc = count_test_outcomes(spec, runs)
    parts = []
    if tc["pass"]:    parts.append(f"{tc['pass']}✓")
    if tc["fail"]:    parts.append(f"{tc['fail']}✗")
    if tc["skip"]:    parts.append(f"{tc['skip']}⏭")
    if tc["pending"]: parts.append(f"{tc['pending']}⏳")
    tests = dict(tc, label=("Tests: " + " · ".join(parts)) if tc["total"]
                 else "No tests declared")

    # Verdict — prefer the authored pipeline gate, then test-derived.
    gate = str(spec.get("gate_status") or "").strip().lower()
    _VMAP = {
        "passed":          ("passed", "Passed", "✅", "v-pass"),
        "failed":          ("failed", "Failing", "❌", "v-fail"),
        "failed_evaluation": ("failed", "Failing", "❌", "v-fail"),
        "blocked":         ("blocked", "Blocked", "⛔", "v-block"),
        "needs_calibration": ("calibrating", "Needs calibration", "🔄", "v-cal"),
        "in_progress":     ("in_progress", "In progress", "🔶", "v-warn"),
    }
    if gate in _VMAP:
        v, label, glyph, cls = _VMAP[gate]
    elif sim != "ran":
        v, label, glyph, cls = ("not_started", "Not run", "○", "v-none")
    elif tc["fail"]:
        v, label, glyph, cls = ("failing", "Failing", "❌", "v-fail")
    elif tc["pending"] and tc["total"]:
        v, label, glyph, cls = ("in_progress", "Tests pending", "⏳", "v-warn")
    elif tc["pass"]:
        v, label, glyph, cls = ("passed", "Passed", "✅", "v-pass")
    else:
        v, label, glyph, cls = ("in_progress", "In progress", "🔶", "v-warn")
    verdict = {"value": v, "label": label, "glyph": glyph, "cls": cls}

    # Ambiguities a reviewer would trip on.
    ambiguities: list[str] = []
    if sim == "ran" and tests["total"] and tc["pending"] == tests["total"]:
        ambiguities.append(
            "ran, but every test renders 'pending' — no run outcomes recorded "
            "(add runs[].outcomes so the tests show pass/fail)")
    elif sim == "ran" and tc["pending"]:
        ambiguities.append(
            f"{tc['pending']} of {tests['total']} tests render 'pending' "
            "(no run outcome recorded for them)")
    if gate == "passed" and tc["fail"]:
        ambiguities.append(
            "gate_status: passed but a test is recorded FAILED — verdict and "
            "test outcomes disagree")
    if str(spec.get("status") or "").lower() in {"completed", "passed", "done"} \
            and sim == "not_run":
        ambiguities.append(
            "headline status says done but no runs are recorded → renders as "
            "not-run")

    return {"ran": ran, "tests": tests, "verdict": verdict,
            "ambiguities": ambiguities}


# Axis value ordering, least→most advanced. Disagreements are only flagged
# when the DERIVED value outranks the stored one — i.e. execution has moved
# past what the stored status claims (the round-2 #2 case: a "planning" /
# "not_run" status on a study that has actually run). The reverse (stored
# claims more than the evidence shows) is NOT flagged: runs.db can simply be
# absent from a given checkout, so a stored ``ran`` with no local db is
# ambiguous, not proof of drift.
_AXIS_RANK = {
    "simulation_status": {"not_run": 0, "running": 1, "ran": 2},
    "evaluation_status": {"not_evaluated": 0, "evaluated": 1},
}


def status_disagreements(spec: dict, runs: list[dict] | None,
                         *, has_verdicts: bool | None = None) -> list[dict]:
    """Where a stored axis (or legacy ``status``) lags BEHIND execution state.

    Returns ``[{axis, stored, derived, message}]`` — empty when everything
    agrees, the stored value is absent, or the stored value merely claims
    *more* than the local runs.db shows (ambiguous: the db may not be in this
    checkout). Only flags when the derived value outranks the stored one — the
    "planning status after the study already ran" drift (round-2 #2).
    """
    derived = derive_status(spec, runs, has_verdicts=has_verdicts)
    out: list[dict] = []
    for axis, info in derived.items():
        stored = spec.get(axis)
        if not stored or stored == info["value"]:
            continue
        ranks = _AXIS_RANK.get(axis, {})
        if ranks.get(info["value"], -1) <= ranks.get(stored, 99):
            continue  # derived not more advanced than stored → not flagged
        out.append({
            "axis": axis,
            "stored": stored,
            "derived": info["value"],
            "message": (
                f"{axis}: stored {stored!r} but execution state implies "
                f"{info['value']!r} ({info['source']})"
            ),
        })
    # Legacy free-form `status`: only flag the unambiguous planning-vs-ran case.
    legacy = str(spec.get("status") or "").strip().lower()
    sim = derived["simulation_status"]["value"]
    if legacy and ("planning" in legacy or legacy == "planned") and sim == "ran":
        out.append({
            "axis": "status",
            "stored": spec.get("status"),
            "derived": "ran",
            "message": (
                f"status: headline {spec.get('status')!r} still says planning, "
                f"but the study has completed runs ({derived['simulation_status']['source']})"
            ),
        })
    return out
