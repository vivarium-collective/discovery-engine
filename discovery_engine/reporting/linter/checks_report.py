"""Core report-quality checks: incomplete summaries, status contradictions,
provenance, stale/divergent verdicts, unresolved placeholders, duplicate modal
phrases, and truncated takeaways.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, Iterator

import yaml

from discovery_engine.study.band_provenance import has_numeric_band as _is_numeric_band
from discovery_engine.bibtex import bib_keys
from discovery_engine.reporting.rigor import run_is_emitter_backed
from discovery_engine.workspace_paths import WorkspacePaths
from discovery_engine.study.observable_lint import reconcile_study_observables
from discovery_engine.reporting.linter.context import *  # noqa: F401,F403




# --- 1. incomplete_summaries -----------------------------------------------


def _check_incomplete_summaries(ctx: _LintContext) -> None:
    """Any study marked evaluation_status: evaluated but missing conclusion_logic content."""
    if ctx.spec.get("evaluation_status") != "evaluated":
        return
    cl = ctx.spec.get("conclusion_logic") or {}
    if not isinstance(cl, dict):
        cl = {}
    # Consider it "content" if any of the canonical sub-objects exist and are non-empty.
    has_content = any(
        _is_nonempty(cl.get(k))
        for k in (
            "if_primary_tests_pass",
            "if_primary_tests_fail",
            "if_pass",
            "if_fail",
        )
    )
    if not has_content:
        ctx.add(
            level="error",
            field_path="conclusion_logic",
            message=(
                "Study is marked evaluation_status: evaluated but "
                "conclusion_logic is empty. Every evaluated study must "
                "have a conclusion mapping (if_primary_tests_pass / "
                "if_primary_tests_fail)."
            ),
            check="incomplete_summaries",
        )


# --- 2. status_contradictions ----------------------------------------------


def _check_status_contradictions(ctx: _LintContext) -> None:
    spec = ctx.spec
    gate = spec.get("gate_status")
    evalst = spec.get("evaluation_status")
    sim = spec.get("simulation_status")
    impl = spec.get("implementation_status")
    review = spec.get("expert_review_status")

    if gate == "passed" and evalst == "failed_evaluation":
        ctx.add(
            level="error",
            field_path="gate_status",
            message=(
                "gate_status: passed but evaluation_status: failed_evaluation. "
                "A study cannot have passed the pipeline gate while its "
                "evaluation has failed."
            ),
            check="status_contradictions",
        )
    if sim == "not_run" and evalst == "evaluated":
        ctx.add(
            level="error",
            field_path="evaluation_status",
            message=(
                "simulation_status: not_run but evaluation_status: evaluated. "
                "There is nothing to evaluate."
            ),
            check="status_contradictions",
        )
    if impl == "not_started" and sim in {"running", "ran"}:
        ctx.add(
            level="error",
            field_path="simulation_status",
            message=(
                f"implementation_status: not_started but simulation_status: {sim}. "
                "Code that wasn't written cannot be running or ran."
            ),
            check="status_contradictions",
        )
    if review == "approved" and gate in {"blocked", "needs_calibration"}:
        ctx.add(
            level="error",
            field_path="expert_review_status",
            message=(
                f"expert_review_status: approved but gate_status: {gate}. "
                "An approved review should not coexist with a blocked or "
                "needs-calibration pipeline gate."
            ),
            check="status_contradictions",
        )


# --- 3. missing_provenance -------------------------------------------------


def _check_missing_provenance(ctx: _LintContext) -> None:
    """Each finding with evaluation_status: evaluated (or evidence.from_run) must have run_ids."""
    spec = ctx.spec
    findings = spec.get("findings") or []
    if not isinstance(findings, list):
        return
    study_evaluated = spec.get("evaluation_status") == "evaluated"
    for idx, f in enumerate(findings):
        if not isinstance(f, dict):
            continue
        evidence = f.get("evidence") or {}
        from_run = bool(evidence.get("from_run")) if isinstance(evidence, dict) else False
        if not (study_evaluated or from_run):
            continue
        prov = f.get("provenance") or {}
        run_ids = prov.get("run_ids") if isinstance(prov, dict) else None
        if not run_ids:
            fid = f.get("id", f"<index-{idx}>")
            ctx.add(
                level="error",
                field_path=f"findings[{idx}].provenance.run_ids",
                message=(
                    f"Finding {fid!r} is run-derived (study is evaluated "
                    "or evidence.from_run set) but provenance.run_ids "
                    "is empty. An evaluated finding without run IDs "
                    "cannot be re-checked against the underlying data."
                ),
                check="missing_provenance",
            )


def _check_stale_computed_grades(ctx: _LintContext) -> None:
    """Warn when a run's persisted ``computed_outcomes`` was graded by a grader behind the current
    one (graded-test ontology Phase 2). The stored grade — and any roll-up summarising it — is stale
    and should be re-derived (``vivarium-workbench sync``); rendering it risks a confidently-wrong
    verdict. Non-blocking: it signals a re-grade, it does not fail publication."""
    from discovery_engine.reporting.grader_freshness import grade_freshness, STALE
    runs = ctx.spec.get("runs")
    if not isinstance(runs, list):
        return
    for idx, run in enumerate(runs):
        if not isinstance(run, dict):
            continue
        co = run.get("computed_outcomes")
        if not isinstance(co, dict):
            continue
        stamp = co.get("_grader")
        if not isinstance(stamp, dict):
            continue
        if grade_freshness(stamp) == STALE:
            name = run.get("name", f"<index-{idx}>")
            ctx.add(
                level="warning",
                field_path=f"runs[{idx}].computed_outcomes._grader",
                message=(
                    f"Run {name!r} carries a computed grade from grader "
                    f"{stamp.get('version')!r}, behind the current grader — the stored verdict "
                    "is stale; re-derive it (sync) so acceptance/gates reflect the current grader."
                ),
                check="stale_computed_grades",
            )


def _check_verdict_divergence(ctx: _LintContext) -> None:
    """Error when the computed verdict contradicts the authored verdict.

    Mirrors needs_attention._verdict_divergence_items: a study-level signal
    (pipeline_gate.gate_evaluator.diverges_from_authored) and a per-test
    signal (runs[].computed_outcomes[test].reconcile == "divergent").
    """
    spec = ctx.spec
    gate = spec.get("pipeline_gate")
    ev = gate.get("gate_evaluator") if isinstance(gate, dict) else None
    if isinstance(ev, dict) and ev.get("diverges_from_authored"):
        ctx.add(
            level="error",
            field_path="pipeline_gate.gate_evaluator.diverges_from_authored",
            message=(
                "The study's computed verdict diverges from the authored "
                "gate. Reconcile the authored verdict with the computed "
                "one, or override this check if the divergence is intended."
            ),
            check="verdict_divergence",
        )
    runs = spec.get("runs") or []
    if not isinstance(runs, list):
        return
    for i, run in enumerate(runs):
        if not isinstance(run, dict):
            continue
        outcomes = run.get("computed_outcomes")
        if not isinstance(outcomes, dict):
            continue
        for test, entry in outcomes.items():
            if isinstance(entry, dict) and entry.get("reconcile") == "divergent":
                ctx.add(
                    level="error",
                    field_path=f"runs[{i}].computed_outcomes.{test}.reconcile",
                    message=(
                        f"Test {test!r} in run index {i} has a computed "
                        "outcome that is divergent from the authored "
                        "outcome. Reconcile them, or override this check "
                        "if the divergence is intended."
                    ),
                    check="verdict_divergence",
                )


# --- 4. unresolved_placeholders --------------------------------------------


# The bare-word markers use a HYPHEN-AWARE boundary: a placeholder is a standalone
# token, not part of a hyphenated compound. `\bTODO\b` treats `-` as a boundary, so
# it false-positives on ordinary prose like "a build-TODO list" (TODO used as a
# common noun). `(?<![\w-])…(?![\w-])` requires the marker not be adjacent to a word
# char OR a hyphen, so "TODO"/"TODO:" still fire but "build-TODO"/"TODO-list" do not.
_PLACEHOLDER_PATTERNS = (
    re.compile(r"(?<![\w-])TBD(?![\w-])", re.IGNORECASE),
    re.compile(r"(?<![\w-])TODO(?![\w-])", re.IGNORECASE),
    re.compile(r"(?<![\w-])XXX(?![\w-])", re.IGNORECASE),
    re.compile(r"\[fill in\]", re.IGNORECASE),
    re.compile(r"<insert>", re.IGNORECASE),
)


# Path leaves that are slug-shaped (identifiers, kebab-case names) — skip
# placeholder checks on them. A study LITERALLY named "TBD" would otherwise
# always trip the linter.
_SKIP_PLACEHOLDER_PATH_LEAVES = frozenset({
    "name", "id", "slug", "composite", "kind", "study", "parameter",
    "study_slug", "proposal_id", "field_path", "evaluator_version",
})


def _check_unresolved_placeholders(ctx: _LintContext) -> None:
    for path, value in _walk_strings(ctx.spec):
        leaf = path.rsplit(".", 1)[-1].split("[", 1)[0]
        if leaf in _SKIP_PLACEHOLDER_PATH_LEAVES:
            continue
        for pat in _PLACEHOLDER_PATTERNS:
            m = pat.search(value)
            if m:
                ctx.add(
                    level="error",
                    field_path=path,
                    message=(
                        f"Unresolved placeholder {m.group(0)!r} in field "
                        f"{path!r}. Either fill in the content or remove the "
                        "placeholder before publishing."
                    ),
                    check="unresolved_placeholders",
                )
                break  # one finding per offending string is enough
    # other scalar — skip


# --- 5. duplicate_modal_phrases --------------------------------------------


def _char_similarity(a: str, b: str) -> float:
    """Cheap >90% similarity check based on char-set Jaccard + length ratio.

    True if at least 90% of the (lower-cased, whitespace-normalized)
    characters of each string are present in the other AND the shorter
    string is at least 70% the length of the longer (avoids flagging
    "ok" inside a 200-char paragraph).
    """
    na = re.sub(r"\s+", " ", a.lower().strip())
    nb = re.sub(r"\s+", " ", b.lower().strip())
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    # Length-ratio gate: very different lengths are not "duplicate phrases".
    sa, sb = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(sa) / max(len(sb), 1) < 0.7:
        return 0.0
    # Use difflib SequenceMatcher for fast char-level similarity.
    import difflib
    return difflib.SequenceMatcher(a=na, b=nb).ratio()


def _check_duplicate_modal_phrases(ctx: _LintContext) -> None:
    """Any pair of behavior_tests[].description >90% identical."""
    tests = ctx.spec.get("behavior_tests") or []
    if not isinstance(tests, list):
        return
    descs: list[tuple[int, str, str]] = []  # (index, name, description)
    for i, t in enumerate(tests):
        if not isinstance(t, dict):
            continue
        d = t.get("description")
        if isinstance(d, str) and d.strip():
            descs.append((i, t.get("name", f"<index-{i}>"), d))
    for ai in range(len(descs)):
        for bi in range(ai + 1, len(descs)):
            ia, na, da = descs[ai]
            ib, nb, db = descs[bi]
            ratio = _char_similarity(da, db)
            if ratio >= 0.90:
                ctx.add(
                    level="warning",
                    field_path=f"behavior_tests[{ib}].description",
                    message=(
                        f"behavior_tests {nb!r} description is {ratio:.0%} "
                        f"similar to {na!r} (likely copy-paste residue). "
                        "Reword one of them to clarify the distinct test "
                        "intent."
                    ),
                    check="duplicate_modal_phrases",
                )


# --- 6. truncated_takeaways ------------------------------------------------


_TAKEAWAY_KEYS_CL_NEW = ("if_primary_tests_pass", "if_primary_tests_fail")
_TAKEAWAY_KEYS_CL_OLD = ("if_pass", "if_fail")
_MIN_TAKEAWAY_LEN = 20

# Within conclusion_logic.if_primary_tests_{pass,fail}, only these subkeys
# carry narrative takeaways that should be terminated/long. Keys like
# `implementation_status`, `pipeline_unblocks` (list), `diagnose` (list)
# are not single-sentence takeaways and should not be flagged.
_NARRATIVE_SUBKEYS = frozenset({"biological_validation", "block_downstream", "summary"})


def _check_truncated_takeaways(ctx: _LintContext) -> None:
    cl = ctx.spec.get("conclusion_logic") or {}
    if not isinstance(cl, dict):
        return
    # Inspect both shapes — old plain string keys and new object-with-fields keys.
    for k in _TAKEAWAY_KEYS_CL_OLD:
        v = cl.get(k)
        if isinstance(v, str):
            _flag_if_truncated(ctx, f"conclusion_logic.{k}", v)
    for k in _TAKEAWAY_KEYS_CL_NEW:
        v = cl.get(k)
        if not isinstance(v, dict):
            continue
        for sk, sv in v.items():
            if sk not in _NARRATIVE_SUBKEYS:
                continue
            if isinstance(sv, str):
                _flag_if_truncated(ctx, f"conclusion_logic.{k}.{sk}", sv)


def _flag_if_truncated(ctx: _LintContext, path: str, value: str) -> None:
    s = value.strip()
    if not s:
        # Empty strings are caught by incomplete_summaries (when the parent is empty)
        # — don't double-flag.
        return
    if len(s) < _MIN_TAKEAWAY_LEN:
        ctx.add(
            level="error",
            field_path=path,
            message=(
                f"Takeaway at {path!r} is only {len(s)} chars (<{_MIN_TAKEAWAY_LEN}). "
                "Likely a truncated or stub takeaway — write a complete sentence."
            ),
            check="truncated_takeaways",
        )
        return
    if s[-1] not in ".!?\")]'":
        ctx.add(
            level="error",
            field_path=path,
            message=(
                f"Takeaway at {path!r} does not end with a terminal "
                "punctuation mark (. ! ?). Likely truncated mid-sentence."
            ),
            check="truncated_takeaways",
        )


__all__ = [
    '_check_incomplete_summaries',
    '_check_status_contradictions',
    '_check_missing_provenance',
    '_check_stale_computed_grades',
    '_check_verdict_divergence',
    '_PLACEHOLDER_PATTERNS',
    '_SKIP_PLACEHOLDER_PATH_LEAVES',
    '_check_unresolved_placeholders',
    '_char_similarity',
    '_check_duplicate_modal_phrases',
    '_TAKEAWAY_KEYS_CL_NEW',
    '_TAKEAWAY_KEYS_CL_OLD',
    '_MIN_TAKEAWAY_LEN',
    '_NARRATIVE_SUBKEYS',
    '_check_truncated_takeaways',
    '_flag_if_truncated',
]
