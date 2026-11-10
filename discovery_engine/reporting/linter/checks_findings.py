"""Findings-protocol checks (Pass 10A/10B + Wave 3a/3b): decide-phase findings,
evidence/statement/citation completeness, workflow typing, claim-scope /
generality / lifecycle enums, and band-provenance citations.
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




def _bib_keys_for_workspace(ws_root: Path) -> set[str]:
    """Every @entry key declared in the workspace bibliography.

    Delegates to the shared :func:`discovery_engine.bibtex.bib_keys` so the
    publish-gate linter and the verify gate agree on file + parser.
    """
    return bib_keys(ws_root)


def _expert_doc_names_for_workspace(ws_root: Path) -> set[str]:
    """Set of expert_docs[].name from workspace.yaml. Empty if absent."""
    ws_yaml = ws_root / "workspace.yaml"
    if not ws_yaml.is_file():
        return set()
    try:
        data = yaml.safe_load(ws_yaml.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError:
        return set()
    docs = data.get("expert_docs") or []
    out: set[str] = set()
    if isinstance(docs, list):
        for d in docs:
            if isinstance(d, dict) and isinstance(d.get("name"), str):
                out.add(d["name"])
    return out


def _check_decide_phase_missing_findings(ctx: _LintContext) -> None:
    """Phase=Decide OR (simulation_status=ran AND evaluation_status=evaluated)
    with zero findings is a Pass 10A error.
    """
    spec = ctx.spec
    findings = spec.get("findings") or []
    if findings:
        return
    phase = spec.get("phase")
    sim = spec.get("simulation_status")
    evalst = spec.get("evaluation_status")
    triggered = phase == "Decide" or (sim == "ran" and evalst == "evaluated")
    if not triggered:
        return
    ctx.add(
        level="error",
        field_path="findings",
        message=(
            "Study reached Decide/Evaluated but has no findings[]. "
            f"Run `/viva-study findings {ctx.slug}` to draft them."
        ),
        check="decide_phase_missing_findings",
    )


def _check_finding_without_evidence(ctx: _LintContext) -> None:
    """A biological/computational finding with no evidence.from_run AND no
    evidence.from_test cannot be linked back to an artifact. Warning; Error
    for a supporting finding of an accepted-with-verdict study."""
    from discovery_engine.study.verdict import (
        is_supporting_finding, study_accepted_on_verdict)

    findings = ctx.spec.get("findings") or []
    if not isinstance(findings, list):
        return
    accepted = study_accepted_on_verdict(ctx.spec)
    for idx, f in enumerate(findings):
        if not isinstance(f, dict):
            continue
        kind = f.get("kind")
        if kind not in {"biological", "computational"}:
            continue
        ev = f.get("evidence") or {}
        if not isinstance(ev, dict):
            ev = {}
        has_link = bool(ev.get("from_run") or ev.get("from_test"))
        if has_link:
            continue
        fid = f.get("id", f"<index-{idx}>")
        # Block publication only where the study-audit gate fires: a
        # supporting finding of an accepted-with-verdict study.
        level = "error" if (accepted and is_supporting_finding(f)) else "warning"
        ctx.add(
            level=level,
            field_path=f"findings[{idx}].evidence",
            message=(
                f"Finding {fid!r} claims a {kind} status but has no "
                "evidence.from_run and no evidence.from_test. Add a link "
                "to the run / behavior_test that produced it."
            ),
            check="finding_without_evidence",
        )


def _check_finding_without_statement(ctx: _LintContext) -> None:
    """A finding with no `statement` renders as '(no statement)' in the report
    and produces an empty Key-takeaways bullet. Every finding must carry a
    one-to-two-sentence statement. Error (blocking)."""
    findings = ctx.spec.get("findings") or []
    if not isinstance(findings, list):
        return
    for idx, f in enumerate(findings):
        if not isinstance(f, dict):
            continue
        stmt = f.get("statement")
        if isinstance(stmt, str) and stmt.strip():
            continue
        fid = f.get("id", f"<index-{idx}>")
        ctx.add(
            level="error",
            field_path=f"findings[{idx}].statement",
            message=(
                f"Finding {fid!r} has no statement. It renders as "
                "'(no statement)' in the report and produces an empty "
                "Key-takeaways bullet. Add a one-to-two sentence statement "
                "describing what was found."
            ),
            check="finding_without_statement",
        )


def _check_finding_cites_unknown_bib_key(ctx: _LintContext) -> None:
    """Any expected.cites entry not in references/papers.bib is an error."""
    findings = ctx.spec.get("findings") or []
    if not isinstance(findings, list):
        return
    known = _cached(ctx, "bib_keys", lambda: _bib_keys_for_workspace(ctx.ws_root))
    if not known:
        # No bibliography to compare against — surface nothing rather than
        # spamming errors. Authors who reference bib_keys without papers.bib
        # are caught by other parts of the report pipeline.
        return
    for idx, f in enumerate(findings):
        if not isinstance(f, dict):
            continue
        expected = f.get("expected") or {}
        if not isinstance(expected, dict):
            continue
        cites = expected.get("cites") or []
        if not isinstance(cites, list):
            continue
        for cidx, key in enumerate(cites):
            if not isinstance(key, str):
                continue
            if key not in known:
                fid = f.get("id", f"<index-{idx}>")
                ctx.add(
                    level="error",
                    field_path=f"findings[{idx}].expected.cites[{cidx}]",
                    message=(
                        f"Finding {fid!r} cites unknown bib_key {key!r}. "
                        "Add it to references/papers.bib first."
                    ),
                    check="finding_cites_unknown_bib_key",
                )


def _check_finding_references_unknown_expert_doc(ctx: _LintContext) -> None:
    """expert_reference.doc must resolve to a workspace.yaml.expert_docs[] entry."""
    findings = ctx.spec.get("findings") or []
    if not isinstance(findings, list):
        return
    known = _cached(ctx, "expert_doc_names", lambda: _expert_doc_names_for_workspace(ctx.ws_root))
    for idx, f in enumerate(findings):
        if not isinstance(f, dict):
            continue
        ref = f.get("expert_reference") or {}
        if not isinstance(ref, dict):
            continue
        doc = ref.get("doc")
        if not isinstance(doc, str):
            continue
        if doc in known:
            continue
        fid = f.get("id", f"<index-{idx}>")
        ctx.add(
            level="error",
            field_path=f"findings[{idx}].expert_reference.doc",
            message=(
                f"Finding {fid!r} references unknown expert doc {doc!r}. "
                "Add it to workspace.yaml.expert_docs[] first."
            ),
            check="finding_references_unknown_expert_doc",
        )


# ---------------------------------------------------------------------------
# Wave 3a — workflow-typing soft checks (critique #7 / #10)
# ---------------------------------------------------------------------------


def _workflow_typing_enums() -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The (next_action_type, study_type) enums, imported defensively so a
    missing/renamed source module degrades to a built-in copy rather than
    crashing the lint run."""
    try:
        from discovery_engine.growth.seed_from_followup import NEXT_ACTION_TYPES
    except Exception:  # noqa: BLE001
        NEXT_ACTION_TYPES = (
            "replicate", "calibrate", "ablate", "adversarially_probe",
            "refine_representation", "split_hypothesis", "retire_hypothesis",
            "escalate_model",
        )
    try:
        from discovery_engine.reporting.rigor import STUDY_TYPES
    except Exception:  # noqa: BLE001
        STUDY_TYPES = (
            "exploratory", "confirmatory", "diagnostic", "adversarial", "standard",
        )
    return tuple(NEXT_ACTION_TYPES), tuple(STUDY_TYPES)


def _check_workflow_typing(ctx: _LintContext) -> None:
    """Soft checks for the wave-3a workflow-typing enums (critique #7 / #10).

    All warning-level (additive / optional fields — existing studies with
    free-text ``next_action`` and no ``study_type`` keep validating):

    - a finding (or followup proposal) sets ``next_action`` but no
      ``next_action_type`` — mirrors the mechanism_origin gap nudge.
    - ``next_action_type`` is set to a value outside the enum.
    - ``study_type`` (or its ``kind`` / ``study_kind`` aliases) is set to a
      value outside the enum.
    """
    spec = ctx.spec
    next_action_types, study_types = _workflow_typing_enums()

    def _check_action(container: dict, path: str) -> None:
        na = container.get("next_action")
        nat = container.get("next_action_type")
        has_na = isinstance(na, str) and na.strip()
        has_nat = isinstance(nat, str) and nat.strip()
        if has_na and not has_nat:
            ctx.add(
                level="warning",
                field_path=f"{path}.next_action_type",
                message=(
                    "next_action is set but next_action_type is absent. Add a "
                    f"machine-readable next_action_type (one of {list(next_action_types)}) "
                    "so the action is filterable; the free-text next_action stays "
                    "as the rationale."
                ),
                check="next_action_type_missing",
            )
        if has_nat and nat.strip() not in next_action_types:
            ctx.add(
                level="warning",
                field_path=f"{path}.next_action_type",
                message=(
                    f"next_action_type {nat.strip()!r} is not a recognised value. "
                    f"Expected one of {list(next_action_types)}."
                ),
                check="next_action_type_unknown",
            )

    findings = spec.get("findings") or []
    if isinstance(findings, list):
        for idx, f in enumerate(findings):
            if isinstance(f, dict):
                _check_action(f, f"findings[{idx}]")

    # followup proposals — both the v3 list and the discovery_implications nest.
    for sect in ("followup_proposals", "followup_study_proposals"):
        items = spec.get(sect) or []
        if isinstance(items, list):
            for idx, p in enumerate(items):
                if isinstance(p, dict):
                    _check_action(p, f"{sect}[{idx}]")
    di = spec.get("discovery_implications")
    if isinstance(di, dict):
        items = di.get("followup_study_proposals") or []
        if isinstance(items, list):
            for idx, p in enumerate(items):
                if isinstance(p, dict):
                    _check_action(p, f"discovery_implications.followup_study_proposals[{idx}]")

    # study_type enum (critique #10) — honor the kind / study_kind aliases.
    raw_st = spec.get("study_type") or spec.get("kind") or spec.get("study_kind")
    if isinstance(raw_st, str) and raw_st.strip() and raw_st.strip().lower() not in study_types:
        field = "study_type" if spec.get("study_type") else (
            "kind" if spec.get("kind") else "study_kind"
        )
        ctx.add(
            level="warning",
            field_path=field,
            message=(
                f"{field} {raw_st.strip()!r} is not a recognised study_type. "
                f"Expected one of {list(study_types)}."
            ),
            check="study_type_unknown",
        )


# ---------------------------------------------------------------------------
# Wave 3b — claim_scope / generality / lifecycle_state soft checks (#21/#22/#25)
# ---------------------------------------------------------------------------


def _wave3b_enums() -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    """The (claim_scopes, generality_axes, generality_levels, lifecycle_states)
    enums, imported defensively so a missing/renamed source module degrades to
    a built-in copy rather than crashing the lint run."""
    try:
        from discovery_engine.reporting.rigor import (
            CLAIM_SCOPES, GENERALITY_AXES, GENERALITY_LEVELS,
        )
    except Exception:  # noqa: BLE001
        CLAIM_SCOPES = (
            "local-implementation", "mechanism", "behavioral", "theoretical", "generality",
        )
        GENERALITY_AXES = (
            "parameter_regime", "initial_conditions", "discretization", "geometry",
            "alt_implementation", "independent_authoring",
        )
        GENERALITY_LEVELS = ("instance_specific", "mechanism", "framework")
    try:
        from discovery_engine.study.verdict import LIFECYCLE_STATES
    except Exception:  # noqa: BLE001
        LIFECYCLE_STATES = (
            "observation", "candidate-explanation", "tested-vs-alternatives",
            "provisional-claim", "generalized", "retired", "superseded",
        )
    return (tuple(CLAIM_SCOPES), tuple(GENERALITY_AXES),
            tuple(GENERALITY_LEVELS), tuple(LIFECYCLE_STATES))


def _check_finding_scope_generality_lifecycle(ctx: _LintContext) -> None:
    """Soft (warning-level) validation of the wave-3b per-finding enums and the
    lifecycle floor (critique #21 / #22 / #25). All additive/optional — a study
    with none of these fields validates silently.

    - ``claim_scope`` outside the enum → warning.
    - ``generality.axes_tested`` value outside the enum → warning.
    - ``generality.level`` outside the enum → warning.
    - ``lifecycle_state`` outside the enum → warning.
    - authored ``lifecycle_state`` BELOW the derived floor → warning (#25).
    """
    findings = ctx.spec.get("findings") or []
    if not isinstance(findings, list):
        return
    claim_scopes, gen_axes, gen_levels, lifecycle_states = _wave3b_enums()

    # Defensive import of the lifecycle-floor helper.
    try:
        from discovery_engine.study.verdict import lifecycle_below_floor
    except Exception:  # noqa: BLE001
        lifecycle_below_floor = None  # type: ignore

    for idx, f in enumerate(findings):
        if not isinstance(f, dict):
            continue
        fid = f.get("id", f"<index-{idx}>")

        # claim_scope enum (#21)
        cs = f.get("claim_scope")
        if isinstance(cs, str) and cs.strip() and cs.strip().lower() not in claim_scopes:
            ctx.add(
                level="warning",
                field_path=f"findings[{idx}].claim_scope",
                message=(
                    f"Finding {fid!r} claim_scope {cs.strip()!r} is not a recognised value. "
                    f"Expected one of {list(claim_scopes)}."
                ),
                check="claim_scope_unknown",
            )

        # generality enums (#22)
        gen = f.get("generality")
        if isinstance(gen, dict):
            axes = gen.get("axes_tested")
            if isinstance(axes, list):
                for aidx, ax in enumerate(axes):
                    if isinstance(ax, str) and ax.strip() and ax.strip() not in gen_axes:
                        ctx.add(
                            level="warning",
                            field_path=f"findings[{idx}].generality.axes_tested[{aidx}]",
                            message=(
                                f"Finding {fid!r} generality axis {ax.strip()!r} is not a "
                                f"recognised value. Expected one of {list(gen_axes)}."
                            ),
                            check="generality_axis_unknown",
                        )
            lvl = gen.get("level")
            if isinstance(lvl, str) and lvl.strip() and lvl.strip() not in gen_levels:
                ctx.add(
                    level="warning",
                    field_path=f"findings[{idx}].generality.level",
                    message=(
                        f"Finding {fid!r} generality.level {lvl.strip()!r} is not a recognised "
                        f"value. Expected one of {list(gen_levels)}."
                    ),
                    check="generality_level_unknown",
                )

        # lifecycle_state enum + floor (#25)
        ls = f.get("lifecycle_state")
        if isinstance(ls, str) and ls.strip():
            if ls.strip().lower() not in lifecycle_states:
                ctx.add(
                    level="warning",
                    field_path=f"findings[{idx}].lifecycle_state",
                    message=(
                        f"Finding {fid!r} lifecycle_state {ls.strip()!r} is not a recognised value. "
                        f"Expected one of {list(lifecycle_states)}."
                    ),
                    check="lifecycle_state_unknown",
                )
            elif lifecycle_below_floor is not None:
                floor = lifecycle_below_floor(f, ctx.spec)
                if floor:
                    ctx.add(
                        level="warning",
                        field_path=f"findings[{idx}].lifecycle_state",
                        message=(
                            f"Finding {fid!r} authored lifecycle_state {ls.strip()!r} sits below "
                            f"the derived floor {floor!r} (the rigor signals already justify a more "
                            "mature state). Raise lifecycle_state to at least the floor."
                        ),
                        check="lifecycle_state_below_floor",
                    )


# ---------------------------------------------------------------------------
# Pass 10B — band provenance checks (stage 3a)
# ---------------------------------------------------------------------------


# _is_numeric_band: see the top-of-file import — was a local reimplementation
# here that disagreed with band_provenance.py / rigor.py (this one didn't
# count pass_if.value, so a value-style test was invisible to the
# /viva-tests cite-bands citation-gap worklist yet counted as "sourced" in rigor's
# gate). Now a single import of the canonical band_provenance.has_numeric_band.


def _check_band_test_missing_cites(ctx: _LintContext) -> None:
    """Numeric-band behavior_tests[] / tests[] without cites → warning.

    Expert-sourced acceptance bands (e.g. ``[0.2, 0.5]`` from Boesen 2024)
    should carry a ``cites: [bib_key]`` so the band's source is machine-
    linked rather than buried in prose notes.  This is a *warning* (not an
    error) because it nudges rather than blocks — back-compat with all
    existing uncited studies.

    Only ``acceptance_criterion`` bands are nudged. A ``regression_pin`` band is
    a threshold set *after* seeing the run (it locks current behavior, and is
    never counted as acceptance evidence), so it has no pre-stated external prior
    to cite — asking it for a source contradicts what a pin is. Conservation /
    sanity / sign gates are authored this way for exactly this reason.
    """
    for section in ("behavior_tests", "tests"):
        items = ctx.spec.get(section) or []
        if not isinstance(items, list):
            continue
        for idx, test in enumerate(items):
            if not isinstance(test, dict):
                continue
            if not _is_numeric_band(test):
                continue
            if str(test.get("gate_class") or "").strip() == "regression_pin":
                continue  # a pin has no external prior to cite
            cites = test.get("cites") or []
            if cites:  # has at least one cite — silent
                continue
            name = test.get("name", f"<index-{idx}>")
            ctx.add(
                level="warning",
                field_path=f"{section}[{idx}]",
                message=(
                    f"Test {name!r} has a numeric acceptance band but no cites. "
                    "Add cites: [bib_key] sourcing the band so its provenance is "
                    "machine-linked (not just prose). See references/papers.bib."
                ),
                check="band_test_missing_cites",
            )


def _check_band_cites_unknown_bib_key(ctx: _LintContext) -> None:
    """cites on behavior_tests, tests, readouts, and calibration_anchor must
    resolve against ``references/papers.bib``.

    Mirrors ``_check_finding_cites_unknown_bib_key``: silent when no
    ``papers.bib`` exists, error otherwise.
    """
    known = _cached(ctx, "bib_keys", lambda: _bib_keys_for_workspace(ctx.ws_root))
    if not known:
        # No bibliography to compare against — silent (same contract as finding check).
        return

    # behavior_tests[] and tests[]
    for section in ("behavior_tests", "tests"):
        items = ctx.spec.get(section) or []
        if not isinstance(items, list):
            continue
        for idx, test in enumerate(items):
            if not isinstance(test, dict):
                continue
            name = test.get("name", f"<index-{idx}>")
            # Direct cites on the test
            cites = test.get("cites") or []
            if isinstance(cites, list):
                for cidx, key in enumerate(cites):
                    if isinstance(key, str) and key not in known:
                        ctx.add(
                            level="error",
                            field_path=f"{section}[{idx}].cites[{cidx}]",
                            message=(
                                f"Test {name!r} cites unknown bib_key {key!r}. "
                                "Add it to references/papers.bib first."
                            ),
                            check="band_cites_unknown_bib_key",
                        )
            # calibration_anchor.cites
            anch = test.get("calibration_anchor") or {}
            if isinstance(anch, dict):
                anch_cites = anch.get("cites") or []
                if isinstance(anch_cites, list):
                    for cidx, key in enumerate(anch_cites):
                        if isinstance(key, str) and key not in known:
                            ctx.add(
                                level="error",
                                field_path=f"{section}[{idx}].calibration_anchor.cites[{cidx}]",
                                message=(
                                    f"Test {name!r} calibration_anchor cites unknown bib_key "
                                    f"{key!r}. Add it to references/papers.bib first."
                                ),
                                check="band_cites_unknown_bib_key",
                            )

    # readouts[]
    readouts = ctx.spec.get("readouts") or []
    if isinstance(readouts, list):
        for idx, ro in enumerate(readouts):
            if not isinstance(ro, dict):
                continue
            name = ro.get("name", f"<index-{idx}>")
            cites = ro.get("cites") or []
            if isinstance(cites, list):
                for cidx, key in enumerate(cites):
                    if isinstance(key, str) and key not in known:
                        ctx.add(
                            level="error",
                            field_path=f"readouts[{idx}].cites[{cidx}]",
                            message=(
                                f"Readout {name!r} cites unknown bib_key {key!r}. "
                                "Add it to references/papers.bib first."
                            ),
                            check="band_cites_unknown_bib_key",
                        )


__all__ = [
    '_bib_keys_for_workspace',
    '_expert_doc_names_for_workspace',
    '_check_decide_phase_missing_findings',
    '_check_finding_without_evidence',
    '_check_finding_without_statement',
    '_check_finding_cites_unknown_bib_key',
    '_check_finding_references_unknown_expert_doc',
    '_workflow_typing_enums',
    '_check_workflow_typing',
    '_wave3b_enums',
    '_check_finding_scope_generality_lifecycle',
    '_check_band_test_missing_cites',
    '_check_band_cites_unknown_bib_key',
]
