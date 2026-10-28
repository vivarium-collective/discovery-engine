"""Roll per-test outcomes → a study verdict → write the coded gate_evaluator slot.

Spine stage #2 (coded gate/verdict/acceptance roll-up).

The verdict rule is the single canonical source replacing the three inline
``tests-passed`` re-implementations. The ``passed`` predicate EXACTLY matches
``vivarium_workbench.lib.investigations_index._condition_satisfied`` ``tests-passed`` branch:
    counts["fail"] == 0 and counts["pass"] > 0

Public API
----------
roll_up_verdict(spec) -> dict
    Pure function: canonical per-test outcomes → {result, blocked_by, evaluated_by}.

write_gate_evaluator(study_dir) -> bool
    Ruamel round-trip write of pipeline_gate.gate_evaluator (parallel coded slot).
    Never touches gate_status / report.verdict / any authored field.
    Returns True when the file was changed, False on a no-op (idempotent).
"""
from __future__ import annotations

from pathlib import Path

from .study_outcomes import canonical_outcomes, canonical_run
from .study_status import _study_tests, bucket_tests


# ---------------------------------------------------------------------------
# Verdict vocabulary — the result values this module emits
# ---------------------------------------------------------------------------
_RESULT_PASSED = "passed"
_RESULT_FAILED = "failed"
_RESULT_NEEDS_CALIBRATION = "needs_calibration"
_RESULT_BLOCKED = "blocked"
_RESULT_NOT_STARTED = "not_started"

# Map authored gate_status values → the same result vocabulary so divergence
# can be detected by simple equality.
_GATE_STATUS_MAP: dict[str, str] = {
    "passed": _RESULT_PASSED,
    "failed": _RESULT_FAILED,
    "failed_evaluation": _RESULT_FAILED,
    "blocked": _RESULT_BLOCKED,
    "needs_calibration": _RESULT_NEEDS_CALIBRATION,
}


# ---------------------------------------------------------------------------
# roll_up_verdict
# ---------------------------------------------------------------------------

def roll_up_verdict(spec: dict) -> dict:
    """Compute the study verdict from the canonical run's per-test outcomes.

    Parameters
    ----------
    spec : dict
        The study spec (e.g. from study_io.load_yaml_mapping). Must carry at
        least one of ``behavior_tests`` / ``tests`` / ``expected_behavior`` and
        optionally ``runs``.

    Returns
    -------
    dict with keys:
        result       : str  — one of the _RESULT_* constants above
        blocked_by   : list[str]  — test names that are FAIL or still pending
        evaluated_by : str  — always "code"
    """
    tests = _study_tests(spec)
    outcomes = canonical_outcomes(spec)

    has_any_run = bool((spec.get("runs") or []))

    # Shared per-test classification (single-sourced in study_status.bucket_tests,
    # also used by count_test_outcomes). The verdict rule below is what makes this
    # the COMPUTED verdict — distinct from study_status.study_clarity_summary,
    # which prefers the AUTHORED gate_status.
    buckets = bucket_tests(tests, outcomes)
    pass_names = buckets["pass"]
    fail_names = buckets["fail"]
    skip_names = buckets["skip"]
    pending_names = buckets["pending"]

    # Verdict rule — canonical; must mirror server._condition_satisfied exactly.
    # Priority: FAIL > PARTIAL/SKIP (needs_calibration) > PASSED > no-run.
    # Note: the gate condition for DAG unblocking is "fail==0 and pass>0"
    # (server._condition_satisfied, tests-passed). The displayed verdict adds a
    # finer bucket: "needs_calibration" when any partial/skip outcomes exist
    # alongside passes, so calibration work is visibly outstanding.
    if fail_names:
        result = _RESULT_FAILED
        blocked_by = fail_names + pending_names
    elif skip_names and not fail_names:
        # Any PARTIAL/SKIP outcome, no failures → needs calibration
        result = _RESULT_NEEDS_CALIBRATION
        blocked_by = []
    elif pass_names and not fail_names:
        # passed: fail==0 and pass>0 — EXACTLY the server.py gate predicate
        result = _RESULT_PASSED
        blocked_by = []
    elif not has_any_run or not tests:
        result = _RESULT_NOT_STARTED
        blocked_by = list(t.get("name", "") for t in tests)
    else:
        # Has runs but all tests are pending (no recorded outcomes)
        result = _RESULT_BLOCKED
        blocked_by = pending_names

    return {
        "result": result,
        "blocked_by": blocked_by,
        "evaluated_by": "code",
    }


# ---------------------------------------------------------------------------
# Canonical display confidence (reconciles the status surfaces)
# ---------------------------------------------------------------------------

def severity_gate(report: dict) -> dict:
    """Severity-aware study gate over a ``build_report()`` ``test_report/v1`` doc.

    Complements ``roll_up_verdict`` (which gates on per-*test* outcomes): this
    gates on per-*axis* severity in the graded report cards.

    - **fail** iff any ``hard``-severity axis is ``mismatch`` (``counts.hard_mismatch``).
    - **warn** iff there's a non-hard miss to record — a ``soft`` mismatch or any
      ``drift`` (calibration outstanding) — but nothing hard failed.
    - **pass** otherwise.

    ``directional`` axes never emit ``mismatch`` (they trend via ``drift`` + a
    signed margin), so they never gate. Returns
    ``{status: pass|fail|warn, hard_mismatch: int, gated_by: [{card, group, id}]}``
    where ``gated_by`` is the actionable list of hard mismatches.
    """
    report = report or {}
    counts = report.get("counts") or {}
    hard = int(counts.get("hard_mismatch") or 0)
    gated_by = []
    for card, doc in (report.get("cards") or {}).items():
        for gslug, grp in (doc.get("groups") or {}).items():
            for ax in grp.get("axes") or []:
                if ax.get("verdict") == "mismatch" and ax.get("severity", "hard") == "hard":
                    gated_by.append({"card": card, "group": gslug, "id": ax.get("id")})
    if hard > 0:
        status = "fail"
    elif int(counts.get("mismatch") or 0) > 0 or int(counts.get("drift") or 0) > 0:
        status = "warn"
    else:
        status = "pass"
    return {"status": status, "hard_mismatch": hard, "gated_by": gated_by}


_CONFIDENCE_ACCEPTED = "Accepted"
_CONFIDENCE_INVESTIGATING = "Investigating"
_CONFIDENCE_REFUTED = "Refuted"
_CONFIDENCE_PLANNED = "Planned"


def derive_confidence(spec: dict) -> str:
    """The single canonical display confidence for a study.

    A study's status shows up in three places — the left-rail dot, the
    investigation-graph node, and the study badge — and they diverge when each
    reads a different field (computed gate vs. authored ``report.verdict`` vs.
    stale lifecycle ``status``). This derives ONE value all three can read so they
    never disagree. Derive-only: it never mutates any authored field.

    Priority:
      1. the COMPUTED gate verdict from real per-test outcomes (``roll_up_verdict``)
         when definitive (passed / failed / needs_calibration / blocked);
      2. else the AUTHORED ``report.verdict`` (the same claim the rail dot reads),
         so a study with no coded tests still reads consistently with the rail;
      3. else the lifecycle run ``status``.

    Returns one of ``"Accepted"`` / ``"Investigating"`` / ``"Refuted"`` /
    ``"Planned"``.
    """
    result = (roll_up_verdict(spec) or {}).get("result")
    if result == _RESULT_PASSED:
        return _CONFIDENCE_ACCEPTED
    if result == _RESULT_FAILED:
        return _CONFIDENCE_REFUTED
    if result in (_RESULT_NEEDS_CALIBRATION, _RESULT_BLOCKED):
        return _CONFIDENCE_INVESTIGATING
    # No coded verdict (not_started: runs but no coded tests) — fall to the
    # authored report.verdict so the graph node matches the rail dot the reviewer
    # already sees, then to the lifecycle status.
    authored = ((spec.get("report") or {}).get("verdict")
                or spec.get("gate_status") or "").strip().lower()
    if authored in ("passed", "pass", "passing", "passing-with-caveats", "accepted"):
        return _CONFIDENCE_ACCEPTED
    if authored in ("failed", "failed_evaluation", "refuted", "failing", "blocked"):
        return _CONFIDENCE_REFUTED
    if authored in ("partial", "needs_calibration", "investigating", "inconclusive"):
        return _CONFIDENCE_INVESTIGATING
    status = (spec.get("status") or "").strip().lower()
    if status in ("completed", "complete", "ran", "evaluated"):
        return _CONFIDENCE_ACCEPTED
    if status in ("running", "in_progress"):
        return _CONFIDENCE_INVESTIGATING
    return _CONFIDENCE_PLANNED


# ---------------------------------------------------------------------------
# Authored-vs-computed divergence (pure helper, reusable by callers/spines)
# ---------------------------------------------------------------------------

def diverges_from_authored(spec: dict) -> bool:
    """True when the authored ``gate_status`` disagrees with the computed verdict.

    Pure ``spec -> bool``. Maps the authored ``gate_status`` to the result
    vocabulary and compares it to ``roll_up_verdict(spec)['result']``. Returns
    False when there is no recognised authored ``gate_status`` (no comparison
    possible) or when authored and computed agree.

    This is the single canonical divergence rule used by
    :func:`write_gate_evaluator`; it is exposed so other code (e.g. the
    pbg-autopoiesis spine) can compute the same value without a file write.
    """
    spec = spec or {}
    verdict = roll_up_verdict(spec)
    authored_gate = str(spec.get("gate_status") or "").strip().lower()
    authored_mapped = _GATE_STATUS_MAP.get(authored_gate)
    if authored_mapped is None:
        return False
    return authored_mapped != verdict["result"]


def confidence_diverges_from_derived(spec: dict) -> bool:
    """True when the authored study-graph ``confidence:`` disagrees with
    :func:`derive_confidence`.

    ``confidence:`` is the investigation-graph node badge an author may set by
    hand at Decide; :func:`derive_confidence` is the single canonical value the
    left-rail dot and graph node otherwise read. When the author pins a
    ``confidence:`` that the status/verdict evidence does not support — e.g.
    ``Accepted`` on a study whose rolled-up verdict is ``failed`` — the three
    status surfaces tell different stories and a reviewer reads it as spin.

    Pure ``spec -> bool``: False when no ``confidence:`` is authored (nothing to
    compare) or when the authored value matches the derived one (case-folded).
    """
    spec = spec or {}
    authored = str(spec.get("confidence") or "").strip()
    if not authored:
        return False
    return authored.lower() != derive_confidence(spec).strip().lower()


# ---------------------------------------------------------------------------
# Gate classification (review G1) — regression_pin vs acceptance_criterion
# ---------------------------------------------------------------------------

# Canonical values for the per-test ``gate_class`` field (review M1 fix).
# A REGRESSION PIN is a threshold set AFTER observing the run — it guards a
# rerun against drift, but passing it is not evidence the model met a
# pre-stated expectation. An ACCEPTANCE CRITERION is a directional prior
# stated BEFORE the run — passing it IS evidence. Conflating the two lets
# "gate: passed" overclaim; every counter below keeps them separate.
GATE_CLASS_REGRESSION_PIN = "regression_pin"
GATE_CLASS_ACCEPTANCE_CRITERION = "acceptance_criterion"
GATE_CLASSES = (GATE_CLASS_REGRESSION_PIN, GATE_CLASS_ACCEPTANCE_CRITERION)

# Presence of any of these fields marks a behavior_test as COMMITTED /
# rerunnable — it names an executable check (a pytest node / registered
# post-sim step) or a machine-evaluable predicate (``pass_if``), rather than a
# narrated judgement. Extend this tuple when a new carrier lands (e.g. the
# tests-spec typed registry).
_COMMITTED_TEST_FIELDS = ("pytest", "pytest_node", "test_path", "post_sim", "pass_if")


def _test_gate_class(test: dict) -> str | None:
    """The canonical ``gate_class`` of one behavior_test, or ``None``.

    Reads the ``gate_class`` field; tolerates the value appearing under the
    tests-spec ``kind`` carrier when it equals one of the canonical values
    (``kind`` values like ``report_card`` are ignored — they are a different
    axis). Unknown / absent values return ``None`` (→ narrated/unclassified).
    """
    test = test or {}
    for key in ("gate_class", "kind"):
        v = str(test.get(key) or "").strip().lower()
        if v in GATE_CLASSES:
            return v
    return None


def is_expected_fail(test: dict) -> bool:
    """True for a control DESIGNED to fail (a negative/diagnostic control).

    Recognised by any of: ``expected_result: fail`` (or ``failed``),
    ``classification: diagnostic``, or ``control: negative`` — the same
    markers ``test_audit.has_discriminating_control`` reads. Such a test must
    NEVER be counted as an acceptance pass: its FAIL outcome is the desired
    behaviour, and its gate_class (if any) is overridden by its control role.
    """
    test = test or {}
    if str(test.get("expected_result") or "").strip().lower() in ("fail", "failed"):
        return True
    if str(test.get("classification") or "").strip().lower() == "diagnostic":
        return True
    if str(test.get("control") or "").strip().lower() == "negative":
        return True
    return False


def _is_committed_rerunnable(test: dict) -> bool:
    """True when the test names an executable check or machine predicate."""
    test = test or {}
    return any(test.get(k) for k in _COMMITTED_TEST_FIELDS)


def classify_gates(spec: dict) -> dict:
    """Bucket a study's behavior_tests by ``gate_class`` (review G1).

    Pure ``spec -> dict`` of NAME lists (mirroring ``bucket_tests``):

    - ``expected_fail`` — negative/diagnostic controls designed to fail
      (:func:`is_expected_fail`). Checked FIRST: an expected-fail control is
      never bucketed as (nor counted toward) acceptance, whatever its
      ``gate_class`` says.
    - ``committed_pins`` — ``gate_class: regression_pin`` (post-hoc rerun
      guards).
    - ``acceptance_criteria`` — ``gate_class: acceptance_criterion``
      (pre-stated directional priors).
    - ``narrated`` — no gate_class AND no committed executable check
      (:func:`_is_committed_rerunnable`): a narrative judgement, not a
      rerunnable gate.
    - ``unclassified`` — a committed/coded check with NO gate_class: the
      author has not yet said whether its threshold is a pin or a prior
      (the report linter can flag these).

    Buckets are mutually exclusive and cover every test once.
    """
    out: dict[str, list[str]] = {
        "committed_pins": [],
        "acceptance_criteria": [],
        "expected_fail": [],
        "narrated": [],
        "unclassified": [],
    }
    for t in _study_tests(spec or {}):
        name = t.get("name") or ""
        if is_expected_fail(t):
            out["expected_fail"].append(name)
        elif _test_gate_class(t) == GATE_CLASS_REGRESSION_PIN:
            out["committed_pins"].append(name)
        elif _test_gate_class(t) == GATE_CLASS_ACCEPTANCE_CRITERION:
            out["acceptance_criteria"].append(name)
        elif _is_committed_rerunnable(t):
            out["unclassified"].append(name)
        else:
            out["narrated"].append(name)
    return out


# ---------------------------------------------------------------------------
# Split verdict ledger — pure spec -> dict
# ---------------------------------------------------------------------------
# NOT dead code: vivarium-workbench's gate_rigor.verdict_count_split /
# behavior_test_card delegate to it for the honest "pins N/N; acceptance M/K"
# ledger. Removing it silently reverts the workbench to a sentinel label.


def verdict_count_split(spec: dict) -> dict:
    """Per-gate_class outcome counts, so a verdict can render HONESTLY.

    ``roll_up_verdict`` deliberately stays the coarse gate (fail==0 and
    pass>0); this ADDITIVE split lets the renderer/report say
    ``pins: N/N; acceptance: M/K`` instead of conflating rerun-guard pins with
    pre-stated acceptance criteria (review G1). Pure ``spec -> dict``:

    - ``regression_pins`` / ``acceptance_criteria`` — ``{total, pass, fail}``
      against the canonical run's outcomes (same classification as
      ``bucket_tests``).
    - ``expected_fail`` — ``{total, behaved}`` where ``behaved`` counts
      controls that FAILED as designed. Never contributes to acceptance.
    - ``narrated`` / ``unclassified`` — counts of tests with no coded gate /
      no declared gate_class.
    - ``committed_rerunnable`` — how many tests carry an executable check or
      machine predicate at all (:func:`_is_committed_rerunnable`).
    - ``label`` — the honest render string, e.g.
      ``"pins: 2/2; acceptance: 1/3"`` (expected-fail / narrated appended
      only when present).
    """
    spec = spec or {}
    tests = _study_tests(spec)
    outcomes = canonical_outcomes(spec)
    buckets = bucket_tests(tests, outcomes)
    passed = set(buckets["pass"])
    failed = set(buckets["fail"])
    classes = classify_gates(spec)

    def _split(names: list[str]) -> dict:
        return {
            "total": len(names),
            "pass": sum(1 for n in names if n in passed),
            "fail": sum(1 for n in names if n in failed),
        }

    pins = _split(classes["committed_pins"])
    acceptance = _split(classes["acceptance_criteria"])
    expected_fail = {
        "total": len(classes["expected_fail"]),
        # behaved == FAILED as designed (the control's success condition)
        "behaved": sum(1 for n in classes["expected_fail"] if n in failed),
    }

    label = (f"pins: {pins['pass']}/{pins['total']}; "
             f"acceptance: {acceptance['pass']}/{acceptance['total']}")
    if expected_fail["total"]:
        label += (f"; expected-fail behaved: "
                  f"{expected_fail['behaved']}/{expected_fail['total']}")
    if classes["narrated"]:
        label += f"; narrated: {len(classes['narrated'])}"
    if classes["unclassified"]:
        label += f"; unclassified: {len(classes['unclassified'])}"

    return {
        "committed_rerunnable": sum(1 for t in tests if _is_committed_rerunnable(t)),
        "regression_pins": pins,
        "acceptance_criteria": acceptance,
        "expected_fail": expected_fail,
        "narrated": len(classes["narrated"]),
        "unclassified": len(classes["unclassified"]),
        "label": label,
    }


# ---------------------------------------------------------------------------
# Pre-registration status (critique #18) — pure spec -> dict
# ---------------------------------------------------------------------------


def _run_start_timestamp(run: dict | None) -> str | None:
    """The canonical run's start time, preferring ``started_at`` then the
    generic ``timestamp`` (which study_outcomes derives from completed/started)."""
    if not isinstance(run, dict):
        return None
    for k in ("started_at", "timestamp"):
        v = run.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def preregistration_status(spec: dict) -> dict:
    """Whether a study's acceptance criteria were PRE-registered (critique #18).

    Pure ``spec -> {preregistered, registered_before_run, criteria_match}``:

    - ``preregistered`` (bool) — a non-empty ``preregistered:`` block exists.
    - ``registered_before_run`` (bool | None) — ``preregistered.registered_at``
      is at or before the canonical run's start time. ``None`` when either
      timestamp is missing (degrade gracefully — author-supplied ``registered_at``
      is often absent).
    - ``criteria_match`` (bool | None) — every name in
      ``preregistered.thresholds`` resolves to a ``behavior_tests[]`` whose
      ``pass_if`` equals the pre-registered threshold. ``None`` when no
      ``thresholds`` were declared.

    Tolerant of a minimal / absent block. Sibling
    :func:`preregistration_gate_alignment` returns this dict ENRICHED with the
    gate_class alignment keys (review G1) — kept separate so existing callers
    see byte-identical output.
    """
    spec = spec or {}
    prereg = spec.get("preregistered")
    if not isinstance(prereg, dict) or not prereg:
        return {
            "preregistered": False,
            "registered_before_run": None,
            "criteria_match": None,
        }

    out: dict = {
        "preregistered": True,
        "registered_before_run": None,
        "criteria_match": None,
    }

    # registered_before_run — compare the author-supplied registered_at (ISO
    # string) to the canonical run's start. ISO-8601 strings order lexically.
    registered_at = prereg.get("registered_at")
    started = _run_start_timestamp(canonical_run(spec))
    if (isinstance(registered_at, str) and registered_at.strip()
            and isinstance(started, str) and started.strip()):
        out["registered_before_run"] = registered_at.strip() <= started.strip()

    # criteria_match — pre-registered thresholds vs the actual behavior_tests.
    thresholds = prereg.get("thresholds")
    if isinstance(thresholds, dict) and thresholds:
        actual = {
            t.get("name"): t.get("pass_if")
            for t in _study_tests(spec)
            if isinstance(t, dict) and t.get("name")
        }
        match = True
        for name, predeclared in thresholds.items():
            if name not in actual or actual.get(name) != predeclared:
                match = False
                break
        out["criteria_match"] = match

    return out


def preregistration_gate_alignment(spec: dict) -> dict:
    """:func:`preregistration_status` ENRICHED with gate_class alignment (G1).

    Ties the pin-vs-acceptance distinction to the pre-registration machinery.
    An ``acceptance_criterion`` gate is a pre-stated directional prior, so it
    SHOULD have a matching ``preregistered.thresholds`` entry (same name, same
    ``pass_if`` — the equality rule ``criteria_match`` already uses); a
    ``regression_pin`` is a post-hoc rerun guard and is EXEMPT from
    pre-statement. Pure ``spec -> dict``: all of
    :func:`preregistration_status`'s keys unchanged, plus:

    - ``acceptance_prestated`` (bool | None) — every acceptance_criterion gate
      has a ``preregistered.thresholds`` entry equal to its ``pass_if``.
      ``None`` when the study declares no acceptance_criterion gates; ``False``
      when any acceptance gate lacks one (including when there is no
      ``preregistered:`` block at all).
    - ``unregistered_acceptance`` (list[str]) — acceptance_criterion gate names
      WITHOUT a matching pre-stated threshold (post-hoc acceptance: either
      reclassify as ``regression_pin`` or pre-register the threshold).
    - ``pins_exempt`` (list[str]) — regression_pin gate names, exempt from the
      pre-statement requirement (their absence from ``thresholds`` is fine).
    """
    spec = spec or {}
    out = dict(preregistration_status(spec))

    prereg = spec.get("preregistered")
    thresholds = prereg.get("thresholds") if isinstance(prereg, dict) else None
    if not isinstance(thresholds, dict):
        thresholds = {}

    classes = classify_gates(spec)
    acceptance_names = classes["acceptance_criteria"]
    actual_pass_if = {
        t.get("name"): t.get("pass_if")
        for t in _study_tests(spec)
        if isinstance(t, dict) and t.get("name")
    }
    unregistered = [
        n for n in acceptance_names
        if n not in thresholds or actual_pass_if.get(n) != thresholds.get(n)
    ]
    out["acceptance_prestated"] = None if not acceptance_names else not unregistered
    out["unregistered_acceptance"] = unregistered
    out["pins_exempt"] = classes["committed_pins"]
    return out


# ---------------------------------------------------------------------------
# Finding lifecycle floor (critique #25)
# ---------------------------------------------------------------------------

# The maturity ladder a finding climbs (DISTINCT from ``tier``, the claim class,
# and from ``claim_scope``, the claim breadth). The first five are derivable
# from rigor signals; ``retired`` / ``superseded`` are terminal states an author
# sets manually (never auto-derived).
LIFECYCLE_STATES = (
    "observation",
    "candidate-explanation",
    "tested-vs-alternatives",
    "provisional-claim",
    "generalized",
    "retired",
    "superseded",
)
_LIFECYCLE_RANK = {s: i for i, s in enumerate(LIFECYCLE_STATES)}


def lifecycle_floor(finding: dict, spec: dict) -> str:
    """Derive the MINIMUM lifecycle_state a finding has earned (critique #25).

    Pure ``(finding, spec) -> str``. Reuses the rigor signals already computed:

    - ``observation`` — the default floor.
    - ``candidate-explanation`` — the finding is an explanatory claim
      (``tier`` of mechanism/interpretation), not a bare observation.
    - ``tested-vs-alternatives`` — a competing explanation has been EXCLUDED and
      its discriminator names this finding's test (reuses the rigor alternatives
      matcher; degrades to "any excluded alternative in the study").
    - ``provisional-claim`` — the result REPLICATES with cross-seed agreement
      (reuses ``rigor._replication_agreement``).
    - ``generalized`` — a generality / robustness signal is present (critique
      #22; reuses ``rigor._has_generality_signal``).

    Each level's precondition is checked independently and the HIGHEST satisfied
    level is returned (the floor). An authored ``lifecycle_state`` may sit ABOVE
    the floor, but the linter warns when it sits BELOW. ``retired`` /
    ``superseded`` are never returned (they are author-only terminal states).
    """
    finding = finding or {}
    spec = spec or {}
    rank = 0  # observation

    tier = str(finding.get("tier") or "").strip().lower()
    if tier in ("mechanism", "interpretation"):
        rank = max(rank, _LIFECYCLE_RANK["candidate-explanation"])

    # Defensive import of the rigor predicates (rigor imports study_verdict
    # lazily, so a module-level import here would risk a cycle).
    try:
        from . import rigor as _rigor
    except Exception:  # noqa: BLE001
        _rigor = None  # type: ignore

    if _rigor is not None:
        tokens = _rigor._finding_match_tokens(finding)
        excluded = _rigor._excluded_alternatives(spec)
        matched = [a for a in excluded if _rigor._alt_matches(a, tokens)]
        if matched or (excluded and not tokens):
            rank = max(rank, _LIFECYCLE_RANK["tested-vs-alternatives"])

        n_rep, _ = _rigor._replicate_count(spec)
        if n_rep >= 2:
            disagrees, _ = _rigor._replication_agreement(spec, n_rep)
            replicated = (n_rep >= 3) and not disagrees
            if replicated:
                rank = max(rank, _LIFECYCLE_RANK["provisional-claim"])

        # generalized requires the FINDING's OWN generality claim (#22) — NOT a
        # study-level sweep (which would lift every finding in the study) and NOT
        # mere replication (which only earns provisional-claim above). A single
        # declared axis earns provisional-claim; ≥2 axes or an explicit
        # framework-level claim earns generalized. A finding the author marked
        # instance_specific never reaches generalized.
        faxes = _rigor._finding_generality_axes(finding)
        gen = finding.get("generality") if isinstance(finding.get("generality"), dict) else {}
        level = str(gen.get("level") or "").strip().lower()
        if level != "instance_specific" and (len(faxes) >= 2 or level == "framework"):
            rank = max(rank, _LIFECYCLE_RANK["generalized"])
        elif faxes:
            rank = max(rank, _LIFECYCLE_RANK["provisional-claim"])

    return LIFECYCLE_STATES[rank]


def lifecycle_below_floor(finding: dict, spec: dict) -> str | None:
    """If a finding's authored ``lifecycle_state`` sits BELOW its derived floor,
    return the floor; otherwise ``None``.

    Returns ``None`` when no ``lifecycle_state`` is authored, when the authored
    value is unknown (the enum check is a separate concern), or when it is at or
    above the floor.
    """
    finding = finding or {}
    authored = str(finding.get("lifecycle_state") or "").strip().lower()
    if authored not in _LIFECYCLE_RANK:
        return None
    floor = lifecycle_floor(finding, spec)
    if _LIFECYCLE_RANK[authored] < _LIFECYCLE_RANK[floor]:
        return floor
    return None


# ---------------------------------------------------------------------------
# write_gate_evaluator
# ---------------------------------------------------------------------------

def write_gate_evaluator(study_dir) -> bool:
    """Write pipeline_gate.gate_evaluator (coded parallel slot) into study.yaml.

    Uses ruamel round-trip to preserve all comments and formatting. Only the
    ``pipeline_gate.gate_evaluator`` mapping is created/updated — the authored
    ``gate_status``, ``report.verdict``, and all other authored fields are
    NEVER touched.

    ``diverges_from_authored`` is True when the authored ``gate_status`` (mapped
    to the result vocabulary) disagrees with the computed result. False when no
    authored gate_status exists (no comparison possible) or they agree.

    ``evaluated_at`` is intentionally omitted: it is a datetime and is not
    deterministically available in this environment. The caller may inject it
    after the fact if needed.

    Returns True when study.yaml was rewritten, False when the computed
    gate_evaluator is identical to what was already written (idempotent).
    """
    from io import StringIO

    from ruamel.yaml import YAML

    from . import study_io

    study_dir = Path(study_dir)
    study_yaml = study_dir / "study.yaml"

    # Load spec for verdict computation (plain safe-load, no round-trip needed here)
    spec = study_io.load_yaml_mapping(study_yaml)
    verdict = roll_up_verdict(spec)

    # Authored gate_status vs computed verdict (single canonical rule).
    diverges = diverges_from_authored(spec)

    new_evaluator: dict = {
        "result": verdict["result"],
        "blocked_by": verdict["blocked_by"],
        "evaluated_by": "code",
        "diverges_from_authored": diverges,
    }

    # Round-trip load to preserve comments
    ryaml = YAML()
    ryaml.preserve_quotes = True
    ryaml.width = 4096

    rt_spec = ryaml.load(study_yaml.read_text(encoding="utf-8"))
    if rt_spec is None:
        rt_spec = {}

    # Locate or create pipeline_gate
    pg = rt_spec.get("pipeline_gate")
    if not isinstance(pg, dict):
        pg = {}
        rt_spec["pipeline_gate"] = pg

    # Compare existing gate_evaluator to detect no-op
    existing = pg.get("gate_evaluator")
    if isinstance(existing, dict):
        # Compare field by field (ruamel CommentedMap compares equal to plain dict)
        existing_plain = {
            "result": existing.get("result"),
            "blocked_by": list(existing.get("blocked_by") or []),
            "evaluated_by": existing.get("evaluated_by"),
            "diverges_from_authored": existing.get("diverges_from_authored"),
        }
        if existing_plain == new_evaluator:
            return False  # idempotent no-op

    # Write ONLY the gate_evaluator sub-mapping; leave all other fields intact
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    ge = CommentedMap()
    ge["result"] = new_evaluator["result"]
    bl = CommentedSeq(new_evaluator["blocked_by"])
    ge["blocked_by"] = bl
    ge["evaluated_by"] = new_evaluator["evaluated_by"]
    ge["diverges_from_authored"] = new_evaluator["diverges_from_authored"]

    pg["gate_evaluator"] = ge

    buf = StringIO()
    ryaml.dump(rt_spec, buf)
    study_io.atomic_write(study_yaml, buf.getvalue())
    return True


# Findings that do NOT back an Accepted conclusion: Pass-10A ``status`` values
# that qualify or oppose it (partial = caveat-like, contradicts = refuting), plus
# the literal caveat/refuted/exploratory labels some authors put in status/kind.
_NON_SUPPORTING_FINDING = frozenset({
    "partial", "contradicts", "caveat", "refuted", "exploratory", "planned"})

_ACCEPTED_VERDICTS = frozenset({
    "passed", "pass", "passing", "passing-with-caveats", "accepted"})


def is_supporting_finding(f: dict) -> bool:
    """True when a finding backs (rather than qualifies/opposes) a conclusion."""
    if f.get("obsoleted_by"):
        return False
    for key in ("status", "kind"):
        v = f.get(key)
        if isinstance(v, str) and v.strip().lower() in _NON_SUPPORTING_FINDING:
            return False
    return True


def study_accepted_on_verdict(spec: dict) -> bool:
    """True when confidence is Accepted AND rests on a real verdict signal
    (computed roll-up ``passed``, or an authored accepted ``report.verdict`` /
    ``gate_status``). Accepted purely from lifecycle status -> False. Returns
    False on any error. Shared by the study-audit gate and the report linter."""
    try:
        if derive_confidence(spec) != "Accepted":
            return False
        authored = ((spec.get("report") or {}).get("verdict")
                    or spec.get("gate_status") or "")
        if str(authored).strip().lower() in _ACCEPTED_VERDICTS:
            return True
        return (roll_up_verdict(spec) or {}).get("result") == "passed"
    except Exception:  # noqa: BLE001 - cannot resolve -> not accepted
        return False
