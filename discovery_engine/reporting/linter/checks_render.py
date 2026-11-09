"""Render-guarantee checks: hedged verdicts, live-dashboard render probes, and
the static baseline render guarantee.
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
import re as _re
from discovery_engine.reporting.linter.context import *  # noqa: F401,F403




_HEDGE_WORDS = re.compile(
    r"\b(should|probably|likely|presumably|seems?|appears? to|"
    r"expected to|ought to|might)\b",
    re.IGNORECASE,
)


def _iter_strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _iter_strings(v)


def _check_hedged_verdict_when_passed(ctx: _LintContext) -> None:
    """A study whose authored ``gate_status`` is ``passed`` should state its
    conclusion in the indicative, not the conditional. Hedge words ("should",
    "likely", "seems", "expected to") in the conclusion of a PASSED study are the
    wording symptom obra's verification-before-completion skill flags — a pass
    resting on "should" is a claim, not a result. Warning-level; the fix is to
    state the result plainly or downgrade the verdict.
    """
    if str(ctx.spec.get("gate_status") or "").strip().lower() != "passed":
        return
    for fieldname in ("conclusion_logic", "executive"):
        block = ctx.spec.get(fieldname)
        if not block:
            continue
        for text in _iter_strings(block):
            m = _HEDGE_WORDS.search(text)
            if m:
                ctx.add(
                    level="warning",
                    field_path=fieldname,
                    message=(
                        f"passed study hedges its conclusion with {m.group(0)!r} — "
                        "state the result in the indicative or downgrade the "
                        "verdict; a pass resting on hedged language is a claim, "
                        "not evidence."
                    ),
                    check="hedged_verdict_when_passed",
                )
                break  # one finding per field is enough


def _dashboard_render_error(ws_root: Path, slug: str) -> str | None:
    """Ask the running workbench whether ``slug`` loads, via ``GET /api/study/<slug>``.

    Returns the loader's error message when the dashboard rejects the spec (its
    detail page won't render), or ``None`` when it loads, the server isn't
    running, or the check can't be performed.

    The compute lives in the workbench; the plugin reaches it over HTTP (stdlib
    ``urllib``), never by importing it — importing ``vivarium_workbench`` back
    into the plugin would reintroduce the dependency cycle and break the
    ``study_audit --gate`` step that runs ``--no-install-package
    vivarium-workbench`` (see ``tests/test_no_workbench_import.py``).

    Factored out so the check below is unit-testable by monkeypatching this one
    function.
    """
    import json as _json
    import urllib.error
    import urllib.request

    url = _server_url(ws_root)
    if not url:
        return None  # server not running → render check unavailable, skip
    try:
        req = urllib.request.Request(
            f"{url.rstrip('/')}/api/study/{slug}", method="GET"
        )
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            resp.read()
        return None  # 200 → renders
    except urllib.error.HTTPError as e:
        if e.code != 500:
            return None  # 400 invalid slug / 404 not found → not a render block
        try:
            body = _json.loads(e.read().decode("utf-8"))
        except Exception:  # noqa: BLE001
            return None
        err = body.get("error") or ""
        # Only spec-load failures — not transient I/O — count as render-blocked.
        return err if "InvestigationSpecError" in err else None
    except Exception:  # noqa: BLE001 — server unreachable mid-lint, timeout, …
        return None


def _check_renders_via_dashboard(ctx: _LintContext) -> None:
    """Render-guarantee: confirm the study loads through the dashboard (#96).

    The other checks are static YAML analysis; a spec can pass every one of them
    yet still fail to render, because the dashboard's ``load_spec`` rejects it
    (empty baseline, wrong ``expected_behavior`` shape, …) and the study-detail
    route then returns an error instead of the page. Ask the running workbench
    (``GET /api/study/<slug>``) so "passes lint" implies "renders".

    When the server isn't running (e.g. a standalone lint run in CI) the check
    skips silently rather than failing.
    """
    if ctx.slug == "<workspace>":
        return
    err = _dashboard_render_error(ctx.ws_root, ctx.slug)
    if err:
        ctx.add(
            level="error",
            field_path="<render>",
            message=(
                f"Study does not load in the dashboard: {err} — the static lint "
                "passed, but the dashboard's loader rejects this spec, so its "
                "detail page will not render. Fix the field named in the error."
            ),
            check="render_blocked",
        )


def _check_render_guarantee_baseline(ctx: _LintContext) -> None:
    """Static render-guarantee: a v3/v4 study whose baseline the workbench's
    study-detail loader rejects will not render — catch it here, WITHOUT a
    running server and WITHOUT importing vivarium-workbench.

    This mirrors the blocking baseline rules of the workbench's
    ``_validate_study_v3_or_v4`` (``lib/investigations.py``): a v3 study needs a
    non-empty ``baseline`` list whose every entry is a mapping with a ``name``
    and one of ``composite`` / ``step`` / ``process``; a v4 study's
    ``conditions.baseline`` needs a ``composite``. ``_check_renders_via_dashboard``
    covers the same ground only when a server is up and the error surfaces as an
    HTTP 500 — this static twin makes "passes lint" imply "renders" offline too
    (CI, pre-publish), which is where empty-baseline studies have been slipping
    through to a broken detail page.

    Scoped to schema_version 3/4 (the baseline-based shapes); v2 specs use a
    different loader and are left to their own checks.
    """
    if ctx.slug == "<workspace>":
        return
    spec = ctx.spec
    try:
        sv = int(spec.get("schema_version"))
    except (TypeError, ValueError):
        return
    if sv < 3:
        return  # v2 (and unversioned) use a different loader shape

    conditions = spec.get("conditions") if isinstance(spec.get("conditions"), dict) else {}
    cond_baseline = conditions.get("baseline")
    if isinstance(cond_baseline, dict):  # v4 single-object baseline
        if not cond_baseline.get("composite"):
            ctx.add(
                level="error",
                field_path="conditions.baseline.composite",
                message=(
                    "v4 study: 'conditions.baseline' needs a 'composite' — the "
                    "workbench study-detail page will not render otherwise."
                ),
                check="render_blocked",
            )
        return

    baseline = spec.get("baseline")
    if not isinstance(baseline, list) or not baseline:
        ctx.add(
            level="error",
            field_path="baseline",
            message=(
                "v3 study: 'baseline' must be a non-empty list of composites — "
                "the workbench study-detail page refuses to render an empty/absent "
                "baseline (it raises InvestigationSpecError). Add at least one "
                "entry, e.g. `baseline: [{name: <id>, composite: <pkg>.composites.<x>}]` "
                "or reference a bare `process: <ProcessName>`."
            ),
            check="render_blocked",
        )
        return
    for i, c in enumerate(baseline):
        if not isinstance(c, dict):
            ctx.add(level="error", field_path=f"baseline[{i}]",
                    message=f"v3 study: baseline[{i}] must be a mapping.",
                    check="render_blocked")
            continue
        if not c.get("name"):
            ctx.add(level="error", field_path=f"baseline[{i}].name",
                    message=f"v3 study: baseline[{i}].name is required (workbench render-guarantee).",
                    check="render_blocked")
        if not (c.get("composite") or c.get("step") or c.get("process")):
            ctx.add(
                level="error",
                field_path=f"baseline[{i}]",
                message=(
                    f"v3 study: baseline[{i}] requires one of 'composite', 'step', "
                    "or 'process' — the workbench study-detail page will not render "
                    "otherwise."
                ),
                check="render_blocked",
            )


__all__ = [
    '_HEDGE_WORDS',
    '_iter_strings',
    '_check_hedged_verdict_when_passed',
    '_dashboard_render_error',
    '_check_renders_via_dashboard',
    '_check_render_guarantee_baseline',
]
