"""Derive the investigation knowledge store — Concepts + canonical Verdicts.

This is the derive-first half of the process-bigraph knowledge layer. A `Concept`
is a persistent, revisable synthesis-grouping over member Studies; a canonical
`Verdict` record keeps the Progress (execution) and Verdict (scientific) axes
INDEPENDENT — operation/execution success never sets a scientific verdict.

The derived projection here is read-only: it writes nothing into study.yaml or
investigation.yaml. An authored half (persisted in the investigation's
`knowledge` block) is merged over it by `merge_knowledge` (authored wins, derived
fills, empty never clobbers). Concept *grouping* is an epistemic act performed by
the `consolidate` op — the derived projection seeds one singleton Concept
per Study.

Public API
----------
canonical_verdict(study_spec) -> dict
    {verdict, verdict_source, diverges, rationale, evidence_refs[, progress]}.
derive_knowledge(inv_spec, studies_by_slug) -> dict
    {"concepts": {concept_id: {...}}, "verdicts": {slug: {...}}}.
merge_knowledge(authored, derived) -> dict
    Section-wise, entry-wise merge with the authored-wins / empty-never-clobbers
    rule.
"""
from __future__ import annotations

from typing import Any

from .study_status import _study_tests, bucket_tests
from .study_verdict import roll_up_verdict

_BEHAVIOR_BUCKET = {"pass": "PASS", "fail": "FAIL", "skip": "SKIP", "pending": "PENDING"}


def _merged_run_outcomes(spec: dict) -> dict:
    """Behavior-gate outcomes UNIONED across the study's recorded runs.

    A study's acceptance behaviors are often split across the several composite runs
    that produced them — each run records outcomes only for the gates it computes (e.g.
    the reaction-network gates in one run, protometabolism gates in another). Reading a
    single ``canonical`` run therefore reports a gate recorded in a *sibling* run as
    PENDING — a false 'unverified' flag that the discovery engine surfaces as spurious
    verification debt. Union the runs' outcomes so each behavior counts as whatever the
    run that tested it recorded. Canonical runs are applied last, so on a genuine key
    conflict the canonical value still wins — the verdict path (which reads
    ``canonical_outcomes``) is unchanged; only this gate lookup widens."""
    merged: dict = {}
    runs = [r for r in (spec.get("runs") or []) if isinstance(r, dict) and isinstance(r.get("outcomes"), dict)]
    for r in sorted(runs, key=lambda r: 1 if r.get("canonical") is True else 0):
        merged.update(r["outcomes"])
    return merged


def _study_behaviors(spec: dict) -> dict:
    """Per-behavior pass/fail for a study, keyed by behavior-test name. Lets the
    discovery engine judge an acceptance criterion by whether its NAMED behavior passed
    (not the study's roll-up verdict sign). Reads outcomes unioned across the study's runs
    (see :func:`_merged_run_outcomes`) so a gate recorded in any run is seen. Defensive: a
    spec whose runs can't be read yields {} (the engine then falls back to the verdict),
    never a crashed cycle."""
    try:
        buckets = bucket_tests(_study_tests(spec), _merged_run_outcomes(spec))
    except Exception:
        return {}
    out: dict[str, str] = {}
    for bucket, label in _BEHAVIOR_BUCKET.items():
        for name in buckets.get(bucket, []) or []:
            out[name] = label
    return out


# ---------------------------------------------------------------------------
# Vocabularies
# ---------------------------------------------------------------------------

#: coded roll_up_verdict.result  ->  canonical scientific verdict
_CANON: dict[str, str] = {
    "passed": "supported",
    "failed": "refuted",
    "needs_calibration": "qualified",
    "blocked": "unassessed",
    "not_started": "unassessed",
}

#: authored study.yaml `verdict` values that are already canonical
_AUTHORED_OK = {"supported", "qualified", "inconclusive", "refuted"}

#: the Progress axis — projected verbatim from study.yaml `execution_status`
_PROGRESS_OK = {"proposed", "planned", "running", "completed", "failed"}

#: values that never clobber an existing one during a merge
_EMPTY = (None, "", [], {})

#: verdicts that give the same ANSWER — "the claim holds". `qualified` is a caveated
#: `supported`, so a difference between the two is a refinement, not a divergence. Every
#: other inequality (incl. inconclusive-vs-supported) is still a divergence.
_HOLDS = frozenset({"supported", "qualified"})


# ---------------------------------------------------------------------------
# Canonical verdict (per study)
# ---------------------------------------------------------------------------

def canonical_verdict(study_spec: dict) -> dict:
    """Canonical Verdict record for one study, two axes kept independent.

    The derived verdict comes from the coded test roll-up; an authored
    ``verdict`` in study.yaml wins when present. ``progress`` is projected
    separately from ``execution_status`` and never feeds the verdict — a failed
    execution is not a refuted hypothesis, and a completed run may be refuted.
    """
    derived = _CANON.get(roll_up_verdict(study_spec).get("result"), "unassessed")

    # Verdict schema drift: some workspaces author `verdict`, others `conclusion_verdict`
    # (a scalar or a {verdict, status} object). Read either so both schemas resolve uniformly.
    authored = study_spec.get("verdict")
    if not authored:
        cv = study_spec.get("conclusion_verdict")
        authored = cv.get("verdict") if isinstance(cv, dict) else cv
    authored = authored if authored in _AUTHORED_OK else None

    verdict = authored or derived
    source = "authored" if authored else "derived"
    # A divergence is a genuine disagreement on the ANSWER, not any inequality. `qualified`
    # refines `supported` (both say "the claim holds"), so authored=qualified vs
    # derived=supported is NOT a divergence; authored=qualified vs derived=refuted IS, and
    # so is inconclusive-vs-supported (the author withheld what the gates grant).
    diverges = bool(authored) and not (authored == derived
                                       or (authored in _HOLDS and derived in _HOLDS))

    if authored:
        rationale = "Authored verdict recorded in study.yaml."
    else:
        rationale = f"Derived from coded test roll-up (result: {derived})."

    # Recorded findings are PARTIAL, non-tracked evidence: a study with findings but no tracked
    # verdict is CALIBRATED (recorded evidence, pending a tracked run) — distinct from a never-
    # touched study. They do NOT constitute a tracked verdict and never close an acceptance
    # criterion on their own (the verdict stays `unassessed`, so closure stays blocked). This just
    # lets the cycle label "calibrated, pending run" instead of flatly "unassessed / no evidence".
    finding_ids = _finding_ids(study_spec)
    evidence_recorded = bool(finding_ids)
    calibrated = (not authored) and derived == "unassessed" and evidence_recorded
    if calibrated:
        rationale = (f"No tracked verdict yet, but {len(finding_ids)} recorded finding(s) present "
                     f"— calibrated, pending a tracked run.")

    out: dict[str, Any] = {
        "verdict": verdict,
        "verdict_source": source,
        "diverges": diverges,
        "rationale": rationale,
        "evidence_refs": finding_ids,
        "evidence_recorded": evidence_recorded,
        "calibrated": calibrated,
    }

    progress = study_spec.get("execution_status")
    if progress in _PROGRESS_OK:
        out["progress"] = progress

    return out


def _finding_ids(spec: dict) -> list[str]:
    ids: list[str] = []
    for f in spec.get("findings") or []:
        if isinstance(f, dict) and f.get("id"):
            ids.append(str(f["id"]))
    return ids


def _concept_question(spec: dict, slug: str) -> str:
    """A singleton Concept's unifying question — the study's own question, never
    fabricated. Falls back through the real authored fields before the slug."""
    purpose = spec.get("purpose")
    candidates = [
        spec.get("question"),
        purpose.get("question") if isinstance(purpose, dict) else None,
        spec.get("claim"),
        spec.get("title"),
    ]
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c
    return f"Study {slug}"


# ---------------------------------------------------------------------------
# Derive the knowledge projection for an investigation
# ---------------------------------------------------------------------------

def derive_knowledge(inv_spec: dict, studies_by_slug: dict[str, dict]) -> dict:
    """Project the read-only knowledge store from canonical membership + findings.

    ``verdicts`` carries a canonical verdict for every member study that has a
    spec; a member listed in ``studies[]`` with no spec is skipped, not fatal.
    ``concepts`` seeds one derived singleton Concept per study — grouping into
    themed Concepts is the ``consolidate`` act.
    """
    member_slugs = inv_spec.get("studies") or list(studies_by_slug.keys())

    concepts: dict[str, dict] = {}
    verdicts: dict[str, dict] = {}
    behaviors: dict[str, dict] = {}
    for slug in member_slugs:
        spec = studies_by_slug.get(slug)
        if spec is None:
            continue
        verdicts[slug] = canonical_verdict(spec)
        behaviors[slug] = _study_behaviors(spec)
        concepts[slug] = {
            "question": _concept_question(spec, slug),
            "study_refs": [slug],
            "synthesis": None,
            "org_state": "exploring",
            "derived": True,
        }

    return {"concepts": concepts, "verdicts": verdicts, "behaviors": behaviors}


# ---------------------------------------------------------------------------
# Merge authored over derived
# ---------------------------------------------------------------------------

def merge_knowledge(authored: dict | None, derived: dict | None) -> dict:
    """Merge the authored knowledge half over the derived projection.

    Authored wins field-by-field, derived fills, and an empty authored value
    never clobbers a derived one. Applied per section (concepts, verdicts) and
    per entry; an authored-only entry is added.
    """
    out: dict[str, dict] = {}
    for section in ("concepts", "verdicts", "behaviors"):
        a = (authored or {}).get(section) or {}
        d = (derived or {}).get(section) or {}
        merged = {k: dict(v) for k, v in d.items()}
        for key, a_entry in a.items():
            merged[key] = _merge_entry(merged.get(key), a_entry)
        out[section] = merged
    return out


def _merge_entry(derived_entry: dict | None, authored_entry: dict | None) -> dict:
    result = dict(derived_entry or {})
    for k, v in (authored_entry or {}).items():
        if v in _EMPTY:
            continue
        result[k] = v
    return result
