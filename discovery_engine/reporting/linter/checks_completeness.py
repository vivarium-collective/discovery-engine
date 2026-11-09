"""Completeness checks: v4 narrative-spine, expert-handoff study-card blocks,
readout-contract + readout-migration, and visualization-file presence.
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




# ---------------------------------------------------------------------------
# v4 narrative-spine completeness check
# ---------------------------------------------------------------------------


# The 14 v4 narrative-spine sections plus their v3 fallbacks (where one
# exists). The 7 ★ sections are the ones a reviewer most needs at the top
# of the rendered report; the other 7 are encouraged but optional.
#
# Each entry: (canonical_field, fallback_field_or_None, is_star).
# - canonical_field is the v4 field name.
# - fallback_field is the v3/legacy field that satisfies the section (so a
#   v3 spec with question + behavior_tests + baseline isn't flagged for
#   missing report/study_card/conditions — it just gets the v4-only fields
#   reported as missing).
# - is_star indicates whether this is a top-of-report ★ section.

_NARRATIVE_SECTIONS = (
    # Executive layer
    ("runtime",                      None,                  False),
    ("report",                       None,                  True),
    ("study_card",                   None,                  True),
    # Framing layer
    ("question",                     "purpose.question",    True),
    ("assumptions",                  "key_assumptions",     False),
    ("conditions",                   "baseline",            True),
    ("enforced_params",              None,                  False),
    # Validation layer
    ("behavior_tests",               "expected_behavior",   True),
    ("readouts",                     "observables",         True),
    ("biological_summary",           None,                  False),
    ("literature_anchors",           None,                  False),
    # Implementation + decisions layer
    ("model_change",                 None,                  False),
    ("implementation_requirements",  None,                  False),
    ("design_pivot_required",        None,                  False),
    ("conclusion_verdicts",          None,                  True),
)


def _is_present(spec: dict, dotted_path: str) -> bool:
    """True iff dotted_path resolves to a non-empty value on spec."""
    cur = spec
    for part in dotted_path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return _is_nonempty(cur)


def _check_narrative_spine_completeness(ctx: _LintContext) -> None:
    """v4 narrative-spine nudge — info-level reminder of which dnaa-style
    sections the study has not authored yet.

    Emits ONE finding per study aggregating ALL missing sections, rather
    than 14 separate findings, so the report shows
    "narrative incomplete: 4 sections missing (report, study_card, ...)".
    Severity is `info` — this lint is a nudge, not a publication blocker;
    a v3 study with question + behavior_tests + baseline still validates
    and can publish, it just won't have the dnaa-style narrative spine.

    See docs/concepts/vivarium-workbench-model.md#v4-narrative-spine for
    the full section list and authoring guidance.
    """
    # Skip workspace-level pseudo-specs (the iterator may include a
    # workspace.yaml-aggregate row that doesn't correspond to a study).
    if ctx.slug == "<workspace>":
        return

    missing_star: list[str] = []
    missing_other: list[str] = []
    for canonical, fallback, is_star in _NARRATIVE_SECTIONS:
        if _is_present(ctx.spec, canonical):
            continue
        if fallback and _is_present(ctx.spec, fallback):
            continue
        (missing_star if is_star else missing_other).append(canonical)

    if not missing_star and not missing_other:
        return

    n_missing = len(missing_star) + len(missing_other)
    star_str = (
        f" ★ {', '.join(missing_star)}"
        if missing_star else ""
    )
    other_str = (
        f" · other: {', '.join(missing_other)}"
        if missing_other else ""
    )
    ctx.add(
        level="info",
        field_path="(narrative spine)",
        message=(
            f"narrative incomplete: {n_missing} of {len(_NARRATIVE_SECTIONS)} "
            f"v4 sections missing.{star_str}{other_str}. "
            "See docs/concepts/vivarium-workbench-model.md#v4-narrative-spine "
            "for the full pattern, or /viva-study fill-overview <slug> "
            "--include-narrative to draft them from plan + expert PDFs."
        ),
        check="narrative_spine_completeness",
    )


# ---------------------------------------------------------------------------
# Expert-handoff readiness — every study card in a generated report should
# show baseline composites, variants planned, simulation runs planned,
# readouts, runs, tests, and visualizations. When any of these blocks is
# absent or empty, the expert reviewer sees a blank section instead of
# planned content they can critique. These five warnings flag the gap
# pre-publication.
#
# All warning-level (non-blocking). The study can still publish; the
# nudge is to add scaffolded content (variant ideas, planned runs,
# placeholder viz with mockup data) so the report tells a complete
# story before the runs land.
# ---------------------------------------------------------------------------


def _check_missing_baseline(ctx: _LintContext) -> None:
    """Warning when a study has no baseline composite(s) declared.

    The dashboard's run-runner needs at least one baseline to know what
    to simulate; the report renderer uses baselines to populate the
    "Conditions" panel of each study card.  An empty/absent baseline
    means the expert sees no concrete starting point.
    """
    if ctx.slug == "<workspace>":
        return
    baseline = ctx.spec.get("baseline")
    has_v3_baseline = isinstance(baseline, list) and len(baseline) > 0
    # v4 conditions.baseline alternative shape
    conditions = ctx.spec.get("conditions") or {}
    cond_baseline = conditions.get("baseline") if isinstance(conditions, dict) else None
    has_v4_baseline = isinstance(cond_baseline, dict) and bool(cond_baseline.get("composite"))
    if has_v3_baseline or has_v4_baseline:
        return
    ctx.add(
        level=_completeness_level(ctx.spec, warn_at="Build"),
        field_path="baseline",
        message=(
            "Study has no baseline composite declared. Every study card in "
            "the generated report should show at least one concrete baseline "
            "(`baseline: [{name, composite, params}]` for v3, or "
            "`conditions.baseline: {composite, params}` for v4) so the "
            "expert reviewer knows what's being simulated. Scaffold one even "
            "if the composite path is a placeholder — see /viva-study baseline-add."
        ),
        check="missing_baseline",
    )


def _check_missing_variants(ctx: _LintContext) -> None:
    """Warning when a study has no `variants:` planned.

    Variants are the planned perturbations / configurations the study
    will run. A study with no variants reads to the expert as "design
    incomplete — no specific experimental conditions yet."
    """
    if ctx.slug == "<workspace>":
        return
    variants = ctx.spec.get("variants") or []
    # v4 alternate location
    conditions = ctx.spec.get("conditions") or {}
    v4_variants = conditions.get("variants") if isinstance(conditions, dict) else []
    if (isinstance(variants, list) and len(variants) > 0) or \
       (isinstance(v4_variants, list) and len(v4_variants) > 0):
        return
    ctx.add(
        level=_completeness_level(ctx.spec, warn_at="Build"),
        field_path="variants",
        message=(
            "Study has no variants declared. Variants are the planned "
            "perturbations / configurations to be tested (parameter sweeps, "
            "alternative conditions, perturbation experiments). A study "
            "with no variants reads to a reviewer as 'design incomplete'. "
            "Scaffold ≥1 variant via /viva-study variant-add — even a "
            "single 'reference' variant with the published parameters is "
            "better than an empty list."
        ),
        check="missing_variants",
    )


def _check_missing_planned_runs(ctx: _LintContext) -> None:
    """Warning when a study has no `planned_runs:` and no `runs:`.

    `planned_runs[]` documents the experiments the study WILL execute
    (with status: planned + n_steps + brief details). `runs[]` is for
    completed runs. A study with neither tells the expert nothing about
    what's been run or what's planned.
    """
    if ctx.slug == "<workspace>":
        return
    planned = ctx.spec.get("planned_runs") or []
    runs = ctx.spec.get("runs") or []
    if (isinstance(planned, list) and len(planned) > 0) or \
       (isinstance(runs, list) and len(runs) > 0):
        return
    ctx.add(
        level=_completeness_level(ctx.spec, warn_at="Simulate"),
        field_path="planned_runs",
        message=(
            "Study has no planned_runs[] and no runs[]. Document the "
            "experiments the study will execute (status: planned, with "
            "n_steps + 1-sentence details) so the expert sees the "
            "planned methodology + compute scope. Use planned_runs[] for "
            "what's coming, runs[] for what's completed."
        ),
        check="missing_planned_runs",
    )


def _check_missing_readouts(ctx: _LintContext) -> None:
    """Warning when a study has no `readouts:` field/content.

    `readouts` describes what the study actually measures — the
    observables that the tests in `expected_behavior` evaluate against.
    Without it, the test rows reference concepts the expert reviewer has
    no anchor for.
    """
    if ctx.slug == "<workspace>":
        return
    readouts = ctx.spec.get("readouts")
    if isinstance(readouts, str) and readouts.strip():
        return
    if isinstance(readouts, list) and len(readouts) > 0:
        return
    ctx.add(
        level=_completeness_level(ctx.spec, warn_at="Simulate"),
        field_path="readouts",
        message=(
            "Study has no readouts declared. Describe what the study "
            "actually measures (the observables the expected_behavior "
            "tests evaluate against) so the expert reviewer has anchors "
            "for the test rows. Free-form string or list of strings."
        ),
        check="missing_readouts",
    )


def _check_missing_conditions_block(ctx: _LintContext) -> None:
    """Warning when a study has no `conditions:` block populated.

    The dashboard's study-detail template (`study-detail.html`) renders
    the "Build" tab from `conditions.baseline` + `conditions.variants` +
    `conditions.model_settings`. Without those, the Build tab is blank
    even if the legacy v3 `baseline:` + `variants:` lists are populated.

    The cleanest path is to use v4 `conditions:` from the start so the
    Build tab is informative.
    """
    if ctx.slug == "<workspace>":
        return
    cond = ctx.spec.get("conditions") or {}
    if not isinstance(cond, dict):
        cond = {}
    has_baseline   = isinstance(cond.get("baseline"), dict) and bool(cond["baseline"].get("composite"))
    has_variants   = isinstance(cond.get("variants"), list)   and len(cond["variants"]) > 0
    has_settings   = isinstance(cond.get("model_settings"), list) and len(cond["model_settings"]) > 0
    # Also accept legacy Build-tab fields as a partial fallback.
    has_legacy = bool(ctx.spec.get("model_change")) or bool(ctx.spec.get("implementation_requirements"))
    if has_baseline or has_variants or has_settings or has_legacy:
        return
    ctx.add(
        level=_completeness_level(ctx.spec, warn_at="Build"),
        field_path="conditions",
        message=(
            "Study has no `conditions:` block (or legacy `model_change` / "
            "`implementation_requirements`). The dashboard's study-detail "
            "Build tab renders from `conditions.{baseline,variants,"
            "model_settings}`. Without it the Build tab is BLANK for "
            "this study, even if v3 `baseline:` + `variants:` are "
            "populated. Add at minimum a `conditions.baseline.composite` "
            "value; see the study lint-checks + data-model reference in "
            "docs/concepts/vivarium-workbench-model.md."
        ),
        check="missing_conditions_block",
    )


def _check_missing_simulation_set(ctx: _LintContext) -> None:
    """Warning when a study has no `simulation_set:` entries.

    The dashboard's study-detail template renders the "Simulations" tab
    from `simulation_set:` (a list of detailed run specs: name, kind,
    status, base_model, duration_steps, seeds, metrics, pass_fail_tests).
    Without it, the Simulations tab is blank.
    """
    if ctx.slug == "<workspace>":
        return
    ss = ctx.spec.get("simulation_set") or []
    if isinstance(ss, list) and len(ss) > 0:
        return
    ctx.add(
        level=_completeness_level(ctx.spec, warn_at="Simulate"),
        field_path="simulation_set",
        message=(
            "Study has no `simulation_set:` entries. The dashboard's "
            "study-detail Simulations tab renders from this list (each "
            "entry: name, kind, status, base_model, duration_steps, "
            "seeds, metrics, pass_fail_tests). Without it the "
            "Simulations tab is BLANK. For studies that have v3 "
            "`planned_runs:` instead, translate each entry into a "
            "`simulation_set` entry (the v3 field stays as back-compat)."
        ),
        check="missing_simulation_set",
    )


def _has_visualization_surface(ctx: _LintContext) -> bool:
    """True iff the study has a visualization on any surface the dashboard /
    generated report actually renders: a declared ``visualizations:`` entry, a
    declared ``embed_visualizations:`` entry, or an auto-discovered figure in
    ``reports/figures/<slug>/`` or ``studies/<slug>/{charts,viz}/``.

    NOTE: this is the LENIENT surface (figures count). The hard gate
    :func:`_check_missing_visualizations` uses the stricter
    :func:`_has_declared_visualization` (declared viz only); this lenient helper
    now only feeds that gate's "a figure exists but isn't declared" hint.
    """
    viz = ctx.spec.get("visualizations") or []
    if isinstance(viz, list) and len(viz) > 0:
        return True
    embeds = ctx.spec.get("embed_visualizations") or []
    if isinstance(embeds, list) and len(embeds) > 0:
        return True
    wp = WorkspacePaths.load(ctx.ws_root)
    if (wp.reports / "figures" / ctx.slug).is_dir() and any(
        (wp.reports / "figures" / ctx.slug).glob("*.html")
    ):
        return True
    for sub in ("charts", "viz"):
        d = wp.studies / ctx.slug / sub
        if d.is_dir() and any(d.iterdir()):
            return True
    return False


def _has_declared_visualization(ctx: _LintContext) -> bool:
    """True iff the study DECLARES a visualization the workbench's Visualizations
    surface can render: a non-empty ``visualizations:`` list (each entry an
    ``address: local:<Class>`` / registered v2 Visualization) or a non-empty
    ``embed_visualizations:`` list.

    Deliberately STRICTER than :func:`_has_visualization_surface`: a loose figure
    file on disk (``reports/figures/<slug>/`` or ``studies/<slug>/{charts,viz}/``)
    does NOT count — the dashboard renders *declared* viz, not orphan HTML, so a
    study whose only "viz" is an on-disk file still shows an empty Visualizations
    surface. This is the predicate the hard gate below uses.
    """
    viz = ctx.spec.get("visualizations") or []
    if isinstance(viz, list) and len(viz) > 0:
        return True
    embeds = ctx.spec.get("embed_visualizations") or []
    return isinstance(embeds, list) and len(embeds) > 0


def _check_missing_visualizations(ctx: _LintContext) -> None:
    """ERROR (blocking, override-able): EVERY study must DECLARE ≥1 visualization.

    Visualizations are the cards the dashboard / generated report renders inline;
    a study with none gives the reviewer nothing to see and leaves the workbench's
    Visualizations surface empty. The requirement binds every study — in-design
    ones included — because a study without a planned figure is incomplete by
    construction; for a pre-run study, declare ≥1 PLANNED-mockup viz (synthetic
    data + explanatory caption) showing what the chart WILL look like.

    STRICT on what counts (see :func:`_has_declared_visualization`): only a
    declared ``visualizations:`` / ``embed_visualizations:`` entry satisfies it —
    a loose figure file on disk does not, since the dashboard renders declared viz.

    Escape hatch: a study genuinely too early for even a mockup opts out by
    recording this finding's ``override_key`` in
    ``.pbg/report-lint-overrides.json`` (or ``/viva-report --force``), which
    downgrades the error to a warning — the same mechanism every hard gate uses.
    """
    if ctx.slug == "<workspace>":
        return
    if _has_declared_visualization(ctx):
        return
    # A loose on-disk figure doesn't satisfy the rule, but if one exists say so —
    # the fix is usually just to DECLARE it, not to author a new chart.
    figure_hint = (
        "An on-disk figure was found for this study but is not declared — a figure "
        "file alone is not rendered on the workbench's Visualizations surface. "
        if _has_visualization_surface(ctx) else ""
    )
    ctx.add(
        level="error",
        field_path="visualizations",
        message=(
            "Every study must declare at least one visualization — a "
            "`visualizations:` entry (address: local:<Class> / a registered v2 "
            "Visualization) or an `embed_visualizations:` entry. " + figure_hint +
            "This study declares none, so the report renders an empty "
            "Visualizations section. Author one with /viva-viz (a real chart for a "
            "completed run, or a PLANNED-mockup for a study still in design). A "
            "study too early for even a mockup can opt out by recording this "
            "finding's override_key in .pbg/report-lint-overrides.json (or run "
            "/viva-report --force)."
        ),
        check="missing_visualizations",
    )


def _check_machine_projected_tests(ctx: _LintContext) -> None:
    """Warning when v4 tests[] look auto-projected from expected_behavior[].

    Symptom: every `tests[i].name` matches an `expected_behavior[j].name`,
    AND the `tests[i]` entries are missing the hand-authored markers (no
    `classification`, no real `pass_if`, measure looks like a stringified
    dict). The dashboard renders these as "AI slop" cards: Claim is just
    the slug echoed, Measure shows {expr: "{kind: 'observable-comparison',
    observable: 'process-bigraph-process'}"}, status UNCLASSIFIED.

    The expected pattern: 3-5 hand-authored v4 tests per study with real
    falsifiable claims, classification (primary/supporting/diagnostic/
    regression), structured measure dicts, and pass_if criteria.
    """
    if ctx.slug == "<workspace>":
        return
    tests = ctx.spec.get("tests")
    eb = ctx.spec.get("expected_behavior")
    if not isinstance(tests, list) or len(tests) == 0:
        return
    if not isinstance(eb, list) or len(eb) == 0:
        return

    eb_names = {(e or {}).get("name") for e in eb if isinstance(e, dict)}
    matched = 0
    slop_signals = 0
    for t in tests:
        if not isinstance(t, dict):
            continue
        if t.get("name") in eb_names:
            matched += 1
        # Slop signals: no classification AND measure is a single-key dict
        # like {expr: "<stringified content>"} (the auto-projection shape).
        if not t.get("classification"):
            m = t.get("measure")
            if isinstance(m, dict) and set(m.keys()) <= {"expr"}:
                slop_signals += 1

    # Trip if EITHER (a) >=80% of tests share a name with expected_behavior
    # AND >=80% show the slop signals, OR (b) every single test is slop-signaled.
    n = len(tests)
    if n == 0:
        return
    name_overlap = matched / n
    slop_fraction = slop_signals / n
    if not (slop_fraction >= 0.8 or (name_overlap >= 0.8 and slop_fraction >= 0.5)):
        return

    ctx.add(
        level="warning",
        field_path="tests",
        message=(
            f"v4 tests[] look auto-projected from expected_behavior[] "
            f"(name overlap {matched}/{n}={int(100*name_overlap)}%, "
            f"missing classification + stringified measure on "
            f"{slop_signals}/{n}={int(100*slop_fraction)}% of entries). "
            "Auto-projection produces 'AI slop' cards: Claim is just the slug, "
            "Measure is a dict-stringified-as-string, status UNCLASSIFIED. "
            "Replace with 3-5 hand-authored v4 tests per study: real falsifiable "
            "claims with quantitative thresholds, classification "
            "(primary|supporting|diagnostic|regression), structured measure "
            "dicts (source/observable/reduce/units), pass_if criteria, "
            "requires_simulation pointers, and bib cites."
        ),
        check="machine_projected_tests",
    )


def _check_readout_migration_status(ctx: _LintContext) -> None:
    """Surface each study's readout migration status (SP2b-ii).

    Calls the pure ``readout_migration_status`` (a dry-run — no write) and
    emits findings when readouts are not yet canonical:

    - ``migratable`` (resolvable, canonical form differs) → INFO suggestion to
      run ``/viva-study migrate-readouts <slug>`` (the safe auto-canonicalize).
    - ``needs_human`` (unresolvable prose/derived) → WARNING: these can't be
      parsed and must be re-authored against the composite's real observables
      (``/viva-study check-observables`` + ``GET /api/observables``). This is
      the surface for the 37 ``unresolved`` readouts SP2b-i flagged.

    Silent when both buckets are empty (every readout already canonical).
    The WRITE happens only via the skills; this check is read-only.
    """
    if ctx.slug == "<workspace>":
        return
    study_dir = WorkspacePaths.load(ctx.ws_root).studies / ctx.slug
    if not (study_dir / "study.yaml").is_file():
        return  # legacy investigations/<slug>/spec.yaml — no study.yaml to migrate
    from discovery_engine.study.readout_migration import readout_migration_status
    status = readout_migration_status(study_dir)
    migratable = status.get("migratable") or []
    # The migration surface is mis-dialect readouts (prose ·-groups, derived,
    # ambiguous identifiers). A readout with NO selector field at all is a
    # plain authoring gap (descriptive-only), not a migration target — that's
    # covered by the missing-readouts nudge, so exclude it here.
    needs_human = [
        h for h in (status.get("needs_human") or [])
        if "no identifier, store_path, or index_by field found" not in (h.get("reason") or "")
    ]

    if migratable:
        names = ", ".join(r.get("name", "<unnamed>") for r in migratable)
        ctx.add(
            level="info",
            field_path="readouts",
            message=(
                f"{len(migratable)} readout(s) can be safely canonicalized "
                f"({names}). Run `/viva-study migrate-readouts {ctx.slug}` to "
                "rewrite them to the canonical index_by form (meaning-"
                "preserving, comment-safe). /viva-report also canonicalizes "
                "migratable readouts before rendering."
            ),
            check="readout_migration_status",
        )

    if needs_human:
        names = ", ".join(h.get("name", "<unnamed>") for h in needs_human)
        ctx.add(
            level="warning",
            field_path="readouts",
            message=(
                f"{len(needs_human)} readout(s) can't be parsed into a canonical "
                f"selector (needs_human: {names}). These are never auto-guessed — "
                "re-author each against the composite's real observables via "
                "`/viva-study check-observables` + `GET /api/observables` "
                "(`/viva-study migrate-readouts` drives this). Until then they "
                "stay unresolved and can't be evaluated."
            ),
            check="readout_migration_status",
        )


def fetch_study_observable_check(
    ws_root: Path, slug: str, *, timeout: float = 2.0
) -> dict | None:
    """Fetch a study's readout↔contract verdict from a *running* workbench.

    The workbench validates every readout against its baseline composites'
    real structure at ``GET /api/study-observable-check?study=<slug>`` (it runs
    ``available_observables`` + ``validate_readouts`` in an env worker, reusing
    the server's already-built composites — so this never builds anything here).

    Returns the parsed payload, or ``None`` when no workbench is running / it is
    unreachable / the response isn't usable. ``None`` makes
    ``_check_readout_contract`` a graceful no-op, keeping the offline linter
    deterministic (a check that only fires against a live server). The call is
    a short-timeout GET; any failure is swallowed into ``None``.
    """
    import urllib.error
    import urllib.parse
    import urllib.request

    url = _server_url(ws_root)
    if not url:
        return None
    endpoint = f"{url.rstrip('/')}/api/study-observable-check?" + urllib.parse.urlencode(
        {"study": slug}
    )
    try:
        with urllib.request.urlopen(endpoint, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


# Readout-contract statuses this check acts on. ``ok`` is clean; ``aspirational``
# is runtime-only (bulk ids — not statically checkable); ``unresolved`` is owned
# by _check_readout_migration_status. Only the two below are defects this gate
# should surface. ``not_in_structure`` is the never-fabricate flag (a selector
# pointing at an observable NO baseline composite exposes) → blocking error;
# ``unit_mismatch`` is #1396's typed check (a declared unit dimensionally
# incompatible with the port contract) → warning (version-tolerant, so not a
# hard block on its own).
_READOUT_CONTRACT_LEVEL = {"not_in_structure": "error", "unit_mismatch": "warning"}


def _check_readout_contract(ctx: _LintContext) -> None:
    """Surface the #1396 readout↔contract audit in the lint gate.

    Wires ``GET /api/study-observable-check`` (which runs ``validate_readouts``
    against the baseline composites' real structure) into report linting so a
    never-fabricate ``not_in_structure`` blocks a reviewer-facing render, and a
    ``unit_mismatch`` warns. Reaches a *running* workbench and skips gracefully
    when none is up (see ``fetch_study_observable_check``) — this keeps the
    offline/CI linter deterministic while adding the typed-contract gate
    whenever a server is available.
    """
    payload = fetch_study_observable_check(ctx.ws_root, ctx.slug)
    if not payload:
        return  # no running workbench / unreachable — graceful skip
    results = payload.get("readouts")
    if not isinstance(results, list):
        return  # error-shaped (e.g. no baseline) — nothing to surface
    for res in results:
        if not isinstance(res, dict):
            continue
        level = _READOUT_CONTRACT_LEVEL.get(res.get("status"))
        if level is None:
            continue
        name = res.get("name", "<unnamed>")
        detail = (res.get("detail") or "").strip()
        if res.get("status") == "not_in_structure":
            body = (
                f"readout {name!r}: selector points at an observable no baseline "
                "composite exposes (never-fabricate). Fix the selector or add the "
                "observable to the composite — a readout that can't resolve against "
                "the real contract must not ship in a reviewer-facing report."
            )
        else:  # unit_mismatch
            body = (
                f"readout {name!r}: declared unit is dimensionally incompatible "
                "with the port contract. Reconcile the readout's units with the "
                "composite's typed port."
            )
        if detail:
            body = f"{body} ({detail})"
        ctx.add(
            level=level,
            field_path=f"readouts[{name}]",
            message=body,
            check="readout_contract",
        )


def _check_speculative_readout_paths(ctx: _LintContext) -> None:
    """Warning when a readout claims `path:` that doesn't resolve on disk.

    Symptom: readouts[i] has `path: out/trajectories/foo.csv` (looks
    authoritative) AND the file doesn't exist (resolves to nothing). The
    Path column in the dashboard reads as a real artifact location when
    it's just a planning hint. Reviewers can't tell what's real.

    Status: implemented + no file at path = ERROR (mislabel).
    Status: planned + speculative path with no TBD marker = WARNING.
    """
    if ctx.slug == "<workspace>":
        return
    readouts = ctx.spec.get("readouts")
    if not isinstance(readouts, list):
        return
    for i, r in enumerate(readouts):
        if not isinstance(r, dict):
            continue
        path = (r.get("path") or "").strip()
        status = (r.get("status") or "").strip().lower()
        if not path:
            continue
        # Skip readouts whose path explicitly marks itself as TBD or planned.
        low = path.lower()
        if low.startswith("tbd") or " — planned at " in path.lower() or "(planned)" in low:
            continue
        # Try to resolve against the workspace root.
        full = ctx.ws_root / path
        exists = full.exists()
        if not exists and "*" in path:
            # Glob fallback (handles e.g. out/trajectories/millard_*.csv)
            parts = path.split("/")
            for j, part in enumerate(parts):
                if "*" in part:
                    base = ctx.ws_root / "/".join(parts[:j]) if j > 0 else ctx.ws_root
                    pattern = "/".join(parts[j:])
                    try:
                        exists = any(base.glob(pattern))
                    except Exception:
                        exists = False
                    break
        if exists:
            continue

        if status == "implemented":
            ctx.add(
                level="error",
                field_path=f"readouts[{i}].path",
                message=(
                    f"Readout '{r.get('name', '<unnamed>')}' has status: implemented "
                    f"but path {path!r} does not resolve on disk. Either the artifact "
                    f"was never persisted (demote status to 'planned' + change path "
                    f"to 'TBD'), or the path is wrong (fix it). 'implemented' implies "
                    f"the data exists at the claimed location — reviewers will click."
                ),
                check="speculative_readout_path",
            )
        else:
            ctx.add(
                level="warning",
                field_path=f"readouts[{i}].path",
                message=(
                    f"Readout '{r.get('name', '<unnamed>')}' is status: {status or '<unset>'} "
                    f"with speculative path {path!r} (no file resolves). The Path "
                    f"column in the dashboard reads as authoritative — reviewers "
                    f"will click and find nothing. Prefix with 'TBD' or '(planned)' "
                    f"so the speculative nature is visible, e.g. "
                    f"'TBD — planned at {path}'."
                ),
                check="speculative_readout_path",
            )


def _check_visualization_files(ctx: _LintContext) -> None:
    """Guard against the silent "figure exists but never renders" failure.

    The dashboard auto-discovers a study's interactive figures ONLY from the
    canonical ``<ws_root>/reports/figures/<slug>/*.html``. Three ways an author
    silently ends up with an invisible figure, each caught here as a warning:

      1. A declared ``embed_visualizations[].url`` whose file does not exist
         under the workspace.
      2. A declared ``visualizations[].address: image:<path>`` whose file does
         not exist relative to the study directory.
      3. An HTML figure parked in the look-alike
         ``<ws_root>/workspace/reports/figures/<slug>/`` (a common mistake) that
         the canonical auto-discovery will never see.

    All warning-level: a figure that doesn't render is a quality gap, not a
    schema error, and the author may be mid-authoring.
    """
    if ctx.slug == "<workspace>":
        return
    wp = WorkspacePaths.load(ctx.ws_root)
    slug = ctx.slug
    canonical = wp.reports / "figures" / slug

    # 1. embed_visualizations[].url must resolve to a real file.
    embeds = ctx.spec.get("embed_visualizations") or []
    if isinstance(embeds, list):
        for i, emb in enumerate(embeds):
            if not isinstance(emb, dict):
                continue
            url = str(emb.get("url") or "").strip()
            if not url or url.startswith(("http://", "https://")):
                continue  # remote/external — not ours to verify
            target = ctx.ws_root / url.lstrip("/")
            if not target.is_file():
                ctx.add(
                    level="warning",
                    field_path=f"embed_visualizations[{i}].url",
                    message=(
                        f"Embed {emb.get('name') or url!r} points at {url!r}, which "
                        f"does not exist under the workspace — the figure will not "
                        f"render. Write it to reports/figures/{slug}/<name>.html "
                        f"(the canonical, auto-discovered location)."
                    ),
                    check="viz_file_missing",
                )

    # 2. visualizations[].address: image:<path> must resolve (relative to study dir).
    study_dir = wp.studies / slug
    viz = ctx.spec.get("visualizations") or []
    if isinstance(viz, list):
        for i, v in enumerate(viz):
            if not isinstance(v, dict):
                continue
            addr = str(v.get("address") or "").strip()
            if not addr.startswith("image:"):
                continue  # local:/dotted addresses handled elsewhere
            relp = addr[len("image:"):].strip()
            if not relp or relp.startswith(("http://", "https://", "/")):
                continue
            if not (study_dir / relp).is_file():
                ctx.add(
                    level="warning",
                    field_path=f"visualizations[{i}].address",
                    message=(
                        f"Visualization {v.get('name') or addr!r} references {addr!r}, "
                        f"but no file exists at {relp!r} relative to the study "
                        f"directory — it will not render."
                    ),
                    check="viz_file_missing",
                )

    # 3. Misplaced figures: HTML under the look-alike workspace/reports/figures/
    #    that the canonical auto-discovery will never surface.
    alt = ctx.ws_root / "workspace" / "reports" / "figures" / slug
    try:
        same = alt.resolve() == canonical.resolve()
    except OSError:
        same = False
    if alt.is_dir() and not same:
        orphans = [
            p.name for p in sorted(alt.glob("*.html"))
            if not (canonical / p.name).is_file()
        ]
        if orphans:
            ctx.add(
                level="warning",
                field_path="reports/figures",
                message=(
                    f"Figure(s) {', '.join(orphans)} live in the non-canonical "
                    f"workspace/reports/figures/{slug}/ — the dashboard does NOT "
                    f"auto-discover there, so they will not render. Move them to "
                    f"reports/figures/{slug}/."
                ),
                check="viz_misplaced",
            )


__all__ = [
    '_NARRATIVE_SECTIONS',
    '_is_present',
    '_check_narrative_spine_completeness',
    '_check_missing_baseline',
    '_check_missing_variants',
    '_check_missing_planned_runs',
    '_check_missing_readouts',
    '_check_missing_conditions_block',
    '_check_missing_simulation_set',
    '_has_visualization_surface',
    '_has_declared_visualization',
    '_check_missing_visualizations',
    '_check_machine_projected_tests',
    '_check_readout_migration_status',
    'fetch_study_observable_check',
    '_READOUT_CONTRACT_LEVEL',
    '_check_readout_contract',
    '_check_speculative_readout_paths',
    '_check_visualization_files',
]
