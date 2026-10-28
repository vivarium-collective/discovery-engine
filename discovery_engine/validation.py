"""The validator layer — RFC-0007.

One **validation-record** shape and a **(type, scope)-tagged registry**, a
generalization of the checks the framework already runs (``narrative.audit``,
``rigor``, ``band_provenance``, ``study_contract``, …). A validator is a pure,
derive-first projector ``V(T, C, E) -> [record]``; it never mutates the artifact
it evaluates (evaluation ⊥ intervention — ``set-verdict`` / ``resolve`` / ``relate``
are the only writers).

Two **independent** axes classify every validator:

* **type** — what kind of correctness/justification is tested (8).
* **scope** — which graph artifact is evaluated (7).

The record carries **two** result statuses, and the split is the layer's core
principle — "a successful computational check does not establish biological truth":

* ``execution_status`` — did the check run and pass? Every validator sets it.
* ``scientific_judgment`` — does the artifact's scientific claim hold? ``None`` =
  "this validator does not speak to scientific truth".

A validator *type* that can only establish execution correctness (``structural``,
``computational``, ``visual``) **must** leave ``scientific_judgment`` null; this is
enforced at record construction and re-checked each cycle by
:func:`epistemic_separation_violations` (wired into
``discovery_engine.invariants``).
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Callable

#: what kind of correctness/justification a validator tests.
VALIDATOR_TYPES = (
    "structural", "semantic", "computational", "empirical",
    "logical", "scientific", "narrative", "visual",
)
#: which graph artifact a validator evaluates.
VALIDATOR_SCOPES = (
    "component", "claim", "figure", "study",
    "relationship", "investigation", "narrative",
)
#: did the check run and pass? (orthogonal to the scientific judgment).
EXECUTION_STATUSES = ("passed", "failed", "inconclusive", "error")
#: does the artifact's scientific claim hold? (``None`` = validator asserts no truth).
#: This is the canonical verdict vocabulary; the spec's ``unresolved``/``contested``
#: map to ``unresolved`` / an open challenge (RFC-0007 §6).
JUDGMENT_VALUES = ("supported", "qualified", "refuted", "unresolved")

#: types that can only establish execution correctness — they must leave the
#: scientific judgment null (a rerun that conserves mass is not a supported claim).
_EXECUTION_ONLY_TYPES = frozenset({"structural", "computational", "visual"})


def establishes_judgment(vtype: str) -> bool:
    """True if a validator of this type may assert a ``scientific_judgment``."""
    return vtype not in _EXECUTION_ONLY_TYPES


def validation_record(*, type: str, scope: str, target: str,
                      execution_status: str, scientific_judgment: str | None = None,
                      criteria: list | None = None, findings: list | None = None,
                      recommendations: list | None = None, evidence: list | None = None,
                      validator_id: str | None = None, validator_version: str = "0.1",
                      evaluated_at: str | None = None) -> dict:
    """Build one validation record, validating both axes and the two-axis rule.

    ``evaluated_at`` is left ``None`` unless supplied: the record is produced inside
    the pure, deterministic cycle projection (same evidence → same record), so a
    wall-clock stamp belongs to the persist step, not here."""
    if type not in VALIDATOR_TYPES:
        raise ValueError(f"type must be one of {VALIDATOR_TYPES}, got {type!r}")
    if scope not in VALIDATOR_SCOPES:
        raise ValueError(f"scope must be one of {VALIDATOR_SCOPES}, got {scope!r}")
    if execution_status not in EXECUTION_STATUSES:
        raise ValueError(f"execution_status must be one of {EXECUTION_STATUSES}, got {execution_status!r}")
    if scientific_judgment is not None:
        if scientific_judgment not in JUDGMENT_VALUES:
            raise ValueError(f"scientific_judgment must be one of {JUDGMENT_VALUES} or None, "
                             f"got {scientific_judgment!r}")
        if not establishes_judgment(type):
            raise ValueError(f"a {type!r} validator establishes execution correctness only and "
                             f"must leave scientific_judgment null (got {scientific_judgment!r})")
    return {
        "type": type,
        "scope": scope,
        "target": target,
        "criteria": list(criteria or []),
        "result": {
            "execution_status": execution_status,
            "scientific_judgment": scientific_judgment,
        },
        "findings": list(findings or []),
        "recommendations": list(recommendations or []),
        "evidence": list(evidence or []),
        "provenance": {
            "validator_id": validator_id,
            "validator_version": validator_version,
            "evaluated_at": evaluated_at,
        },
    }


# ---------------------------------------------------------------------------
# Validator context + registry
# ---------------------------------------------------------------------------

@dataclass
class ValidationContext:
    """The already-derived inputs a validator reads (it computes nothing new)."""
    inv_spec: dict = field(default_factory=dict)
    studies: dict = field(default_factory=dict)
    argument: dict = field(default_factory=dict)
    narrative: dict = field(default_factory=dict)
    audit: list = field(default_factory=list)


#: the default process-wide registry (adapters below register into it).
_REGISTRY: list[dict] = []


#: how a validator's records reach the user.
#:   "frontier"  — a real defect; failed/errored records become actionable crank items.
#:   "standing"  — informational feedback (a scorecard dimension); shown in the
#:                 coverage/census view but never floods the actionable frontier.
SURFACES = ("frontier", "standing")


def validator(*, type: str, scope: str, id: str, surfaces: str = "standing",
              registry: list | None = None):
    """Register ``fn(ctx) -> [record]`` under its ``(type, scope)``, id and surface.

    ``surfaces`` defaults to ``"standing"`` — a new validator is feedback until it
    explicitly opts into the frontier, so adding validators can never silently flood
    the actionable crank."""
    if type not in VALIDATOR_TYPES:
        raise ValueError(f"type must be one of {VALIDATOR_TYPES}, got {type!r}")
    if scope not in VALIDATOR_SCOPES:
        raise ValueError(f"scope must be one of {VALIDATOR_SCOPES}, got {scope!r}")
    if surfaces not in SURFACES:
        raise ValueError(f"surfaces must be one of {SURFACES}, got {surfaces!r}")
    reg = _REGISTRY if registry is None else registry

    def deco(fn: Callable):
        reg.append({"type": type, "scope": scope, "id": id, "surfaces": surfaces, "fn": fn})
        return fn

    return deco


def registry() -> list[dict]:
    """The registered validators (a copy of the default registry)."""
    return list(_REGISTRY)


def run_validators(ctx: ValidationContext | None, registry: list | None = None) -> list[dict]:
    """Dispatch every registered validator over ``ctx`` and collect the records.

    A validator that raises is **isolated**: it yields a single ``error``-status
    record (so the failure is visible and surfaces on the frontier) and the cycle
    continues — one buggy validator never takes down the whole projection."""
    reg = _REGISTRY if registry is None else registry
    out: list[dict] = []
    for v in reg:
        surfaces = v.get("surfaces", "standing")
        try:
            records = v["fn"](ctx) or []
        except Exception as exc:  # noqa: BLE001 — isolation is the whole point
            records = [validation_record(
                type=v["type"], scope=v["scope"], target=v["id"],
                execution_status="error",
                findings=[f"validator raised {type(exc).__name__}: {exc}"],
                recommendations=["fix the validator; its records are missing this cycle"],
                validator_id=v["id"])]
            surfaces = "frontier"  # a broken validator is itself actionable
        for r in records:
            r.setdefault("surfaces", surfaces)
        out.extend(records)
    return out


def coverage_matrix(registry: list | None = None) -> dict[str, dict[str, list[str]]]:
    """The type × scope grid of registered validator ids — empty cells included."""
    reg = _REGISTRY if registry is None else registry
    cells: dict[str, dict[str, list[str]]] = {
        t: {s: [] for s in VALIDATOR_SCOPES} for t in VALIDATOR_TYPES}
    for v in reg:
        cells[v["type"]][v["scope"]].append(v["id"])
    return cells


# ---------------------------------------------------------------------------
# Re-validation / staleness (RFC-0007 Phase 4)
# ---------------------------------------------------------------------------

def _record_key(record: dict) -> str:
    """Identity of what a record evaluated: its validator and target."""
    vid = (record.get("provenance") or {}).get("validator_id")
    return f"{vid}|{record.get('target')}"


def record_signature(record: dict) -> str:
    """A stable content hash of a record's *meaning* — type/scope/target, the two
    result statuses, and its findings. Provenance (validator_version, evaluated_at)
    is excluded, so the signature changes only when the evaluation's content does."""
    res = record.get("result") or {}
    payload = {
        "type": record.get("type"), "scope": record.get("scope"),
        "target": record.get("target"),
        "execution_status": res.get("execution_status"),
        "scientific_judgment": res.get("scientific_judgment"),
        "findings": sorted(str(f) for f in (record.get("findings") or [])),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:16]


def snapshot_of(records: list[dict]) -> dict[str, str]:
    """``{validator|target: signature}`` — an accepted validation state to re-check
    later. Persist it on the investigation; drift from it is staleness."""
    return {_record_key(r): record_signature(r) for r in (records or [])}


def stale_records(fresh_records: list[dict], snapshot: dict | None) -> list[dict]:
    """Snapshotted evaluations a fresh re-derivation no longer matches — an accepted
    judgment the world moved past. A record whose signature drifted is ``changed``;
    one that is gone (its study/target removed) is ``removed``. A brand-new record
    not in the snapshot is not stale (it is just new)."""
    snapshot = snapshot or {}
    fresh_by_key = {_record_key(r): r for r in (fresh_records or [])}
    out: list[dict] = []
    for key, stored_sig in snapshot.items():
        vid, _, target = key.partition("|")
        r = fresh_by_key.get(key)
        if r is None:
            out.append({"key": key, "validator_id": vid, "target": target, "change": "removed"})
        elif record_signature(r) != stored_sig:
            out.append({"key": key, "validator_id": vid, "target": target, "change": "changed"})
    return out


def epistemic_separation_violations(records: list[dict]) -> list[dict]:
    """Records whose type can only establish execution correctness yet assert a
    scientific judgment — the executable form of "a passing computational check
    does not establish biological truth"."""
    out: list[dict] = []
    for r in records or []:
        if not isinstance(r, dict):
            continue
        judgment = (r.get("result") or {}).get("scientific_judgment")
        vtype = r.get("type")
        if judgment is not None and not establishes_judgment(vtype):
            out.append({"invariant": "epistemic_separation", "target": r.get("target"),
                        "detail": (f"{vtype} validator establishes execution correctness only "
                                   f"but set scientific_judgment {judgment!r}")})
    return out


# ---------------------------------------------------------------------------
# Shipped adapters (RFC-0007 Phase 1) — thin wrappers over existing checks
# ---------------------------------------------------------------------------

@validator(type="logical", scope="claim", id="logical.claim.provenance", surfaces="frontier")
def _provenance(ctx: ValidationContext) -> list[dict]:
    """An assessed claim (supported/qualified/refuted) that traces to no evidence.
    The same check ``discovery_engine.invariants`` ran inline, now a record."""
    recs: list[dict] = []
    claims = (getattr(ctx, "argument", None) or {}).get("claims") or {}
    for cid, c in claims.items():
        if not isinstance(c, dict):
            continue
        v = c.get("verdict")
        if v in ("supported", "qualified", "refuted") and not (c.get("evidence_refs") or []):
            recs.append(validation_record(
                type="logical", scope="claim", target=cid,
                execution_status="failed", scientific_judgment=None,
                criteria=["evidence_provenance"],
                findings=[f"claim is {v} but traces to no evidence"],
                recommendations=["attach evidence_refs, or downgrade the verdict to unresolved"],
                validator_id="logical.claim.provenance"))
    return recs


@validator(type="scientific", scope="study", id="scientific.study.rigor")
def _rigor(ctx: ValidationContext) -> list[dict]:
    """The rigor scorecard as a record per study: a GAP dimension fails the study's
    method rigor, a WARN leaves it inconclusive. Measures method quality, not the
    claim's truth — so it asserts no scientific_judgment."""
    from . import rigor  # lazy: rigor pulls a large module graph
    recs: list[dict] = []
    for slug, spec in (getattr(ctx, "studies", None) or {}).items():
        if not isinstance(spec, dict):
            continue
        dims = (rigor.study_rigor(spec) or {}).get("dimensions") or []
        gaps = [d for d in dims if d.get("severity") == "gap"]
        warns = [d for d in dims if d.get("severity") == "warn"]
        flagged = gaps or warns
        status = "failed" if gaps else ("inconclusive" if warns else "passed")
        recs.append(validation_record(
            type="scientific", scope="study", target=slug,
            execution_status=status, scientific_judgment=None,
            criteria=[d.get("id") for d in flagged],
            findings=[f"{d.get('label')}: {d.get('detail')}" for d in flagged],
            recommendations=[f"address rigor dimension '{d.get('id')}'" for d in flagged],
            validator_id="scientific.study.rigor"))
    return recs


@validator(type="empirical", scope="claim", id="empirical.claim.band_provenance")
def _band_provenance(ctx: ValidationContext) -> list[dict]:
    """Numeric acceptance bands with no cited provenance — the empirical grounding a
    claim leans on without a source (one record per study, findings per band)."""
    from . import band_provenance as bp
    recs: list[dict] = []
    for slug, spec in (getattr(ctx, "studies", None) or {}).items():
        if not isinstance(spec, dict):
            continue
        missing = bp.bands_missing_provenance(spec)
        if not missing:
            continue
        recs.append(validation_record(
            type="empirical", scope="claim", target=f"claim:{slug}",
            execution_status="failed", scientific_judgment=None,
            criteria=["band_provenance"],
            findings=[f"band on '{m.get('name')}' ({m.get('field_path')}) has no cited provenance"
                      for m in missing],
            recommendations=["cite each acceptance band's source (/viva-tests cite-bands)"],
            validator_id="empirical.claim.band_provenance"))
    return recs


@validator(type="logical", scope="relationship", id="logical.relationship.edge_integrity",
           surfaces="frontier")
def _edge_integrity(ctx: ValidationContext) -> list[dict]:
    """Does the asserted relation hold? Checks each epistemic edge an investigation
    declares: a **dangling** edge (to a study not in the investigation) is a hard
    integrity defect (failed); a `builds-on` edge onto a **refuted** parent is a weak
    foundation (inconclusive — later work leans on a claim that did not hold). A
    `tests` edge onto a refuted parent is not flagged: confirming a refutation is
    normal science, not a broken relation."""
    from .relations import epistemic_relations  # lazy
    studies = getattr(ctx, "studies", None) or {}
    members = set((getattr(ctx, "inv_spec", None) or {}).get("studies") or []) | set(studies)
    claims = (getattr(ctx, "argument", None) or {}).get("claims") or {}
    recs: list[dict] = []
    for slug, spec in studies.items():
        if not isinstance(spec, dict):
            continue
        for rel in epistemic_relations(spec):
            up, relation = rel.get("study"), rel.get("relation")
            if not up or not relation:
                continue
            target = f"{slug} -{relation}-> {up}"
            if up not in members:
                recs.append(validation_record(
                    type="logical", scope="relationship", target=target,
                    execution_status="failed", criteria=["edge_integrity"],
                    findings=[f"relation to unknown study '{up}' (not an investigation member)"],
                    recommendations=["fix the edge target, or add the study to the investigation"],
                    validator_id="logical.relationship.edge_integrity"))
                continue
            up_verdict = (claims.get(f"claim:{up}") or {}).get("verdict")
            if relation == "builds-on" and up_verdict == "refuted":
                recs.append(validation_record(
                    type="logical", scope="relationship", target=target,
                    execution_status="inconclusive", criteria=["edge_integrity"],
                    findings=[f"'{relation}' leans on '{up}', whose claim is refuted"],
                    recommendations=["re-examine whether the relation still holds"],
                    validator_id="logical.relationship.edge_integrity"))
    return recs


# Hygiene guards (RFC-0007) — keep retired bloat and deprecated fields from creeping back
# into the data model each cycle. Lists mirror docs/concepts/vivarium-workbench-model.md.
_DEPRECATED_STUDY_FIELDS = ("consolidation_note", "split_note", "program", "concept", "gate_reason")
_DEPRECATED_INV_FIELDS = ("plan_acts",)
_RETIRED_INV_CACHES = ("knowledge_cycle",)


@validator(type="structural", scope="study", id="structural.study.deprecated_fields",
           surfaces="standing")
def _deprecated_study_fields(ctx: ValidationContext) -> list[dict]:
    """Flag a study still carrying a deprecated, unread field (standing feedback)."""
    recs: list[dict] = []
    for slug, spec in (getattr(ctx, "studies", None) or {}).items():
        if not isinstance(spec, dict):
            continue
        present = [f for f in _DEPRECATED_STUDY_FIELDS if f in spec]
        if present:
            recs.append(validation_record(
                type="structural", scope="study", target=slug,
                execution_status="failed", criteria=["no_deprecated_fields"],
                findings=[f"deprecated field(s) present: {', '.join(present)}"],
                recommendations=["remove the deprecated field(s) — no renderer consumes them"],
                validator_id="structural.study.deprecated_fields"))
    return recs


@validator(type="structural", scope="investigation", id="structural.investigation.hygiene",
           surfaces="frontier")
def _investigation_hygiene(ctx: ValidationContext) -> list[dict]:
    """Flag a retired bloat cache (``knowledge_cycle``) or a deprecated investigation
    field creeping back — a real regression, so it surfaces on the frontier."""
    inv = getattr(ctx, "inv_spec", None) or {}
    problems = [f"retired bloat cache `{f}` present (re-run `discover cycle` → `cycle_state`)"
                for f in _RETIRED_INV_CACHES if f in inv]
    problems += [f"deprecated field `{f}` present" for f in _DEPRECATED_INV_FIELDS if f in inv]
    # decisions_needed that are already resolved — stale narrative drift the persist-time
    # renewal step clears (moves them to decisions_resolved). Surfaces here so the drift is
    # visible even before the next persist.
    ex = inv.get("executive") or {}
    resolved = {r.get("target") for r in (inv.get("resolutions") or []) if isinstance(r, dict)}
    stale_decisions = [(d.get("question") if isinstance(d, dict) else d)
                       for d in (ex.get("decisions_needed") or [])
                       if (d.get("question") if isinstance(d, dict) else d) in resolved]
    if stale_decisions:
        problems.append(
            f"{len(stale_decisions)} `decisions_needed` entr"
            f"{'y is' if len(stale_decisions) == 1 else 'ies are'} already resolved but still "
            f"listed as open (stale — re-run `discover cycle` to renew)")
    if not problems:
        return []
    return [validation_record(
        type="structural", scope="investigation", target="investigation",
        execution_status="failed", criteria=["data_model_hygiene"],
        findings=problems,
        recommendations=["re-run `discover cycle` to replace the cache; remove deprecated fields"],
        validator_id="structural.investigation.hygiene")]


# Load-bearing secondary prose the derive-first cycle DEPENDS ON but does not generate
# (content-flow audit, 2026-10-27): the cycle copies these into propositions / the
# executive opening, or degrades to an explicit stub. Flag an absent one on the frontier
# so the gap is visible in the cycle instead of silently stubbing — the authoring stays a
# `/viva-*` skill's job, but its absence becomes a tracked, actionable item.
_STUDY_PROSE = ("claim",)            # the cycle's sole proposition source; absent → ⟨no claim⟩ stub
_INV_PROSE = ("what_is_this", "verdict_detail")  # the state-first opening + verdict support


@validator(type="structural", scope="study", id="structural.study.narrative_completeness",
           surfaces="frontier")
def _study_narrative_completeness(ctx: ValidationContext) -> list[dict]:
    """Flag a member study missing load-bearing authored prose the cycle leans on: the
    one-line ``claim`` (its proposition source), or an interpretive ``findings[].statement``
    when the study has recorded findings. A record per gapped study; the fix is to author
    it (``/viva-study`` / ``/viva-biology-forward``), not to fabricate it in the cycle."""
    recs: list[dict] = []
    for slug, spec in (getattr(ctx, "studies", None) or {}).items():
        if not isinstance(spec, dict):
            continue
        missing = [f"executive prose `{f}`" for f in _STUDY_PROSE if not str(spec.get(f) or "").strip()]
        findings = [f for f in (spec.get("findings") or []) if isinstance(f, dict)]
        if findings and not any(str(f.get("statement") or f.get("summary") or "").strip() for f in findings):
            missing.append("an interpretive `findings[].statement`")
        if missing:
            recs.append(validation_record(
                type="structural", scope="study", target=slug,
                execution_status="failed", scientific_judgment=None,
                criteria=["narrative_completeness"],
                findings=[f"missing authored prose: {m}" for m in missing],
                recommendations=["author via `/viva-study` (claim/conclusion) or "
                                 "`/viva-biology-forward` (findings) — the cycle will then surface it"],
                validator_id="structural.study.narrative_completeness"))
    return recs


@validator(type="structural", scope="investigation", id="structural.investigation.narrative_completeness",
           surfaces="frontier")
def _investigation_narrative_completeness(ctx: ValidationContext) -> list[dict]:
    """Flag the investigation opening missing load-bearing authored prose: the state-first
    ``executive.what_is_this`` and the ``executive.verdict_detail``. The cycle renders and
    reasons over these but never writes them; a gap surfaces here as an actionable item."""
    ex = (getattr(ctx, "inv_spec", None) or {}).get("executive") or {}
    missing = [f"executive.{f}" for f in _INV_PROSE if not str(ex.get(f) or "").strip()]
    if not missing:
        return []
    return [validation_record(
        type="structural", scope="investigation", target="investigation",
        execution_status="failed", scientific_judgment=None,
        criteria=["narrative_completeness"],
        findings=[f"missing authored prose: {m}" for m in missing],
        recommendations=["author via `/viva-investigation` (set-overview / verdict)"],
        validator_id="structural.investigation.narrative_completeness")]


@validator(type="narrative", scope="narrative", id="narrative.narrative.coherence")
def _narrative_coherence(ctx: ValidationContext) -> list[dict]:
    """Evidential coherence of the reading — one record summarizing ``audit`` gaps
    (the unsupported transitions the narrative asserts)."""
    gaps = getattr(ctx, "audit", None) or []
    if not gaps:
        return [validation_record(
            type="narrative", scope="narrative", target="narrative",
            execution_status="passed", criteria=["evidential_coherence"],
            validator_id="narrative.narrative.coherence")]
    return [validation_record(
        type="narrative", scope="narrative", target="narrative",
        execution_status="failed", criteria=["evidential_coherence"],
        findings=[g.get("reason", "unsupported transition") for g in gaps],
        recommendations=[(g.get("followup") or {}).get("title", "") for g in gaps],
        validator_id="narrative.narrative.coherence")]
