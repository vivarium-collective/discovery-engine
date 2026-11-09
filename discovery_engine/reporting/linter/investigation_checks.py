"""Investigation-level narrative-spine + adversarial-member checks."""
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




# Investigation-level narrative spine — REQUIRED report sections (each can be
# explicitly skipped for a genuinely slim investigation via
# ``narrative_spine_skip: [...]``). These are the AUTHORED sections of the
# investigation report; the computed sections (Decisions needed, Suggested
# additions) are framework signals, not author-required, so they're excluded.
_REQUIRED_INVESTIGATION_SECTIONS = (
    ("executive", "Executive summary"),
    ("scientific_argument", "Scientific argument"),
    ("biological_story", "Biology — the mechanism"),
)


def _inv_section_present(spec: dict, key: str) -> bool:
    """True when an investigation has authored content for a narrative section."""
    v = spec.get(key)
    if key == "biological_story":
        return isinstance(v, str) and bool(v.strip())
    if key == "executive":
        ex = v if isinstance(v, dict) else {}
        return bool(_is_nonempty(ex.get("what_is_this")) or _is_nonempty(ex.get("verdict")))
    if key == "scientific_argument":
        sa = v if isinstance(v, dict) else {}
        return bool(_is_nonempty(sa.get("main_claim")))
    return bool(v)


def _check_investigation_narrative_spine(ctx: "_LintContext") -> None:
    """REQUIRE the investigation-level narrative sections (Executive summary,
    Scientific argument, Biology) — unless explicitly skipped.

    Default severity is ``warning`` (blocking, publication-gating) so these
    sections are effectively required. A genuinely slim investigation opts out
    per-section by listing the section key in ``narrative_spine_skip: [...]``
    (optionally with a ``narrative_spine_skip_reason``); a skipped section is
    treated as satisfied. This is the "required, but explicitly skippable"
    contract.
    """
    spec = ctx.spec or {}
    skip = spec.get("narrative_spine_skip") or []
    if not isinstance(skip, list):
        skip = []
    skip_set = {str(s).strip().lower() for s in skip}
    for key, label in _REQUIRED_INVESTIGATION_SECTIONS:
        if key in skip_set or _inv_section_present(spec, key):
            continue
        ctx.add(
            level="warning",
            field_path=key,
            message=(
                f"Investigation is missing the REQUIRED narrative section "
                f"'{label}' (`{key}`). Author it for a reviewer-ready report, "
                f"or — for a genuinely slim investigation — explicitly opt out "
                f"by adding '{key}' to `narrative_spine_skip: [...]` "
                f"(optionally with `narrative_spine_skip_reason`)."
            ),
            check="investigation_narrative_spine_required",
        )


_ADVERSARIAL_KINDS = frozenset({
    "adversarial", "ablation", "negative_control", "negative-control",
    "negative control",
})


def _study_is_adversarial(spec: dict) -> bool:
    """True when a study is a negative control / adversarial / ablation member.

    Enrichment-plan principles 4 & 5: feature ablations find NECESSARY (not
    merely correlated) mechanisms, and negative controls must fail the same
    gates (vent-specificity). Keys on ``kind: adversarial|ablation|
    negative_control`` or an explicit ``ablation:`` block.
    """
    if not isinstance(spec, dict):
        return False
    kind = str(spec.get("kind") or "").strip().lower()
    if kind in _ADVERSARIAL_KINDS:
        return True
    if spec.get("ablation"):
        return True
    return False


def _check_investigation_missing_adversarial(ctx: _LintContext) -> None:
    """An investigation with no adversarial / ablation member study → warning.

    A campaign that only tells a success story is weak evidence. The
    enrichment-plan design principles require at least one member that passes
    by REJECTION — a negative control that fails the same gates, or a feature
    ablation that removes a mechanism and shows the claim break. Warning: add
    one, or opt out explicitly via ``adversarial_coverage_skip: true`` with a
    ``adversarial_coverage_skip_reason``.
    """
    spec = ctx.spec or {}
    if spec.get("adversarial_coverage_skip") is True:
        return
    member_slugs = spec.get("studies") or []
    if not isinstance(member_slugs, list) or not member_slugs:
        return
    member_slugs = {str(s).strip() for s in member_slugs if str(s).strip()}

    def _build_index() -> dict:
        return {slug: s for slug, s in _iter_study_specs(ctx.ws_root)}

    study_index = _cached(ctx, "study_specs_by_slug", _build_index)
    has_adversarial = any(
        _study_is_adversarial(study_index.get(slug) or {}) for slug in member_slugs
    )
    if has_adversarial:
        return
    ctx.add(
        level="warning",
        field_path="studies",
        message=(
            "Investigation has no adversarial / ablation member study — a "
            "system that should FAIL the same gates (negative control / "
            "vent-specificity) or a feature ablation that removes a mechanism "
            "and shows the claim break. Add one (study `kind: adversarial` or "
            "`kind: ablation`), or opt out with `adversarial_coverage_skip: "
            "true` + a reason. A campaign that only passes is weak evidence."
        ),
        check="investigation_missing_adversarial",
    )


__all__ = [
    '_REQUIRED_INVESTIGATION_SECTIONS',
    '_inv_section_present',
    '_check_investigation_narrative_spine',
    '_ADVERSARIAL_KINDS',
    '_study_is_adversarial',
    '_check_investigation_missing_adversarial',
]
