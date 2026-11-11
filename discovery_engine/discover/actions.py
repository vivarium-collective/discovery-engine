"""Author / write ops for the discover cycle.

The human-authored mutations: resolutions, viz declare/accept/reject,
verdict and capability-gap accept/reject, investigation connection, and the
model-revision apply/review pipeline. These write authored blocks over the
derive-first projection.

Imports flow one way: this module depends on :mod:`discovery_engine.discover._common`
only. The single back-edge to :mod:`discovery_engine.discover.cycle`
(``load_cycle_inputs``) is a lazy in-function import to keep the package acyclic.
"""
from __future__ import annotations

import argparse
import copy
import datetime as _dt
import hashlib
import re
from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML

from discovery_engine.projection.argument import derive_argument, merge_argument
from discovery_engine.investigation.import_ import investigation_members, investigations_dir, studies_dir
from discovery_engine.projection.knowledge import canonical_verdict, derive_knowledge, merge_knowledge
from discovery_engine.projection.narrative import audit, narrate
from discovery_engine.study.run_command import render_command, run_command
from discovery_engine.study.io import atomic_write, load_yaml, load_yaml_mapping, save_yaml_atomic
from discovery_engine.projection.synthesis import open_calls, synthesize_gates
from discovery_engine.reporting.validation import ValidationContext, run_validators

from discovery_engine.discover._common import (
    EPISTEMIC_RELATIONS,
    RESOLUTION_KINDS,
    _ruamel,
    _study_dir_on_disk,
    _viz_target,
)


def _set_relation_inplace(spec, upstream: str, relation: str) -> bool:
    """Set the epistemic ``relation`` to ``upstream`` on the edge where it already
    lives, mutating ``spec`` in place (a plain dict OR a ruamel ``CommentedMap``).

    Precedence: the canonical ``pipeline_gate.prerequisites`` entry first, then the
    legacy ``parent_studies`` backbone (a bare ``{study}`` dict, or a plain string
    promoted to ``{study, relation}``). If the dependency is declared nowhere, an
    epistemic-only ``pipeline_gate.prerequisites`` entry is appended. Returns True iff
    a change was made (idempotent: re-setting the same relation is a no-op)."""
    pg = spec.get("pipeline_gate")
    if isinstance(pg, dict):
        for p in pg.get("prerequisites") or []:
            if isinstance(p, dict) and p.get("study") == upstream:
                if p.get("relation") == relation:
                    return False
                p["relation"] = relation
                return True
    ps = spec.get("parent_studies")
    if isinstance(ps, list):
        for i, e in enumerate(ps):
            if isinstance(e, str) and e == upstream:
                ps[i] = {"study": upstream, "relation": relation}
                return True
            if isinstance(e, dict) and e.get("study") == upstream:
                if e.get("relation") == relation:
                    return False
                e["relation"] = relation
                return True
    pg = spec.setdefault("pipeline_gate", {})
    prereqs = pg.setdefault("prerequisites", [])
    prereqs.append({"study": upstream, "relation": relation})
    return True

def _enrich_file(path: Path | str, upstreams: list[str], relation: str) -> list[str]:
    """Load a study.yaml round-trip (comments preserved), set ``relation`` in place for
    each upstream where its edge lives, and write back only if something changed.
    Returns the upstreams actually changed."""
    y = _ruamel()
    text = Path(path).read_text(encoding="utf-8")
    spec = y.load(text) or {}
    changed = [u for u in upstreams if _set_relation_inplace(spec, u, relation)]
    if changed:
        buf = StringIO()
        y.dump(spec, buf)
        atomic_write(path, buf.getvalue())
    return changed

def add_resolution(inv_spec: dict, kind: str, target: str, rationale: str,
                   by: str | None = None) -> dict:
    """Record a resolution ``{kind, target, rationale, by?}`` in an investigation spec —
    the uniform discharge the discovery crank honors for every frontier-item kind. The op
    that produced an artifact (an adjudication, a bridging study, a met criterion) records
    it here, back-referenced via ``by``. Idempotent on ``(kind, target)``: a second call
    updates the record rather than duplicating. A rationale is required — a resolution
    documents *why* an item is discharged, it does not silently suppress it. Returns the
    updated spec (pure; the caller persists)."""
    if kind not in RESOLUTION_KINDS:
        raise ValueError(f"kind must be one of {RESOLUTION_KINDS}, got {kind!r}")
    if not target:
        raise ValueError("target is required (the frontier item's target, as the crank prints it)")
    if not (rationale or "").strip():
        raise ValueError("a rationale is required — a resolution documents why the item is discharged")
    rec = {"kind": kind, "target": target, "rationale": rationale}
    if by:
        rec["by"] = by
    out = dict(inv_spec)
    kept = [r for r in (out.get("resolutions") or [])
            if not (isinstance(r, dict) and r.get("kind") == kind and r.get("target") == target)]
    kept.append(rec)
    out["resolutions"] = kept
    return out

def record_resolution(inv_path: Path | str, kind: str, target: str, rationale: str,
                      by: str | None = None) -> dict:
    """Load an investigation.yaml, add a resolution, save atomically. Returns the record."""
    path = Path(inv_path)
    spec = add_resolution(load_yaml_mapping(path), kind, target, rationale, by)
    save_yaml_atomic(path, spec)
    return spec["resolutions"][-1]


def review_findings(inv_path: Path | str, *, promote=None, hold=None, promote_all: bool = False,
                    policy=None, reviewer: str = "human") -> dict:
    """Reviewed promotion of FindingCandidates to Discoveries.

    A verified verdict flip is persisted by the cycle as a ``finding_candidates[]`` entry
    (status ``candidate``). This turns a reviewed candidate into a first-class Discovery
    carrying a novelty/validation assessment — the only path that writes ``discoveries[]``.

    - an explicitly named ``--promote`` id is the reviewer's authorization (human in the loop);
    - ``promote_all`` promotes every pending candidate the ``AutonomyPolicy`` authorizes
      (autonomous bulk review) — in HITL mode that is only pre-approved targets;
    - a candidate the assessment finds ``redundant`` (a Discovery already records the flip)
      is held, never duplicated;
    - ``--hold`` parks a candidate without promoting it.

    Loads / mutates / atomically rewrites investigation.yaml. Returns a report dict."""
    from discovery_engine.discover._policy import AutonomyPolicy, decision_record
    from discovery_engine.phases.discovery_record import (
        assess_finding_candidate, promote_finding_candidate, CANDIDATE, PROMOTED, HELD)
    pol = AutonomyPolicy.resolve(policy)
    path = Path(inv_path)
    spec = load_yaml_mapping(path)
    cands = [c for c in spec.get("finding_candidates") or [] if isinstance(c, dict)]
    discs = [d for d in spec.get("discoveries") or [] if isinstance(d, dict)]
    disc_ids = {d.get("id") for d in discs}
    decisions = list(spec.get("review_decisions") or [])
    promote_set, hold_set = set(promote or []), set(hold or [])
    out = {"promoted": [], "held": [], "skipped": [], "pending": []}
    changed = False
    for c in cands:
        cid = c.get("id")
        if c.get("status") != CANDIDATE:
            continue  # already promoted/held
        if cid in hold_set:
            c["status"] = HELD
            out["held"].append(cid); changed = True
            continue
        explicit = cid in promote_set
        if not (explicit or promote_all):
            out["pending"].append(cid)
            continue
        if not explicit and not pol.allows(consequence="discovery", target=cid):
            out["skipped"].append({"id": cid, "reason": "not authorized by autonomy policy"})
            continue
        assessment = assess_finding_candidate(c, discs, reviewer=reviewer)
        if assessment["review_decision"] != "promote":      # redundant → held, never duplicated
            c["status"] = HELD; c["assessment"] = assessment
            out["held"].append(cid); changed = True
            continue
        disc = promote_finding_candidate(c, assessment)
        if disc["id"] not in disc_ids:
            discs.append(disc); disc_ids.add(disc["id"])
        c["status"] = PROMOTED; c["assessment"] = assessment; c["discovery_id"] = disc["id"]
        decisions.append(decision_record(pol, actor=reviewer, consequence="discovery",
                                         target=cid, rationale=assessment["rationale"], result=disc["id"]))
        out["promoted"].append(cid); changed = True
    if changed:
        spec["finding_candidates"] = cands
        spec["discoveries"] = discs
        spec["review_decisions"] = decisions
        save_yaml_atomic(path, spec)
    return out


def record_hypothesis_proposal(inv_path: Path | str, proposal: dict) -> dict:
    """Validate an agent-authored HypothesisProposal and record it (status ``proposed``)
    in ``investigation.yaml::hypothesis_proposals[]``, idempotent by id. The engine
    validates the contract; it never authors the content. Returns the recorded proposal."""
    from discovery_engine.phases.hypothesis_proposal import coerce_hypothesis_proposal
    proposal = coerce_hypothesis_proposal(proposal)   # validate + assign id/status/at
    path = Path(inv_path)
    spec = load_yaml_mapping(path)
    have = [p for p in spec.get("hypothesis_proposals") or [] if isinstance(p, dict)]
    ids = {p.get("id") for p in have}
    if proposal["id"] not in ids:
        have.append(proposal)
        spec["hypothesis_proposals"] = have
        save_yaml_atomic(path, spec)
    return proposal


def set_hypothesis_proposal_status(inv_path: Path | str, proposal_id: str, status: str,
                                   *, by: str = "human", rationale: str | None = None) -> dict:
    """Accept or decline a recorded HypothesisProposal. On ``accepted`` the proposal is
    promoted into the authored ``hypotheses[]`` the evaluator reads (idempotent); on
    ``declined`` it is left out. Authored-wins: this is a human/agent review action, never
    dispatched by ``advance``. Returns ``{id, status, promoted}``."""
    from discovery_engine.phases.hypothesis_proposal import (
        promote_to_hypothesis, ACCEPTED, DECLINED)
    if status not in (ACCEPTED, DECLINED):
        raise ValueError(f"status must be '{ACCEPTED}' or '{DECLINED}', got {status!r}")
    path = Path(inv_path)
    spec = load_yaml_mapping(path)
    props = [p for p in spec.get("hypothesis_proposals") or [] if isinstance(p, dict)]
    target = next((p for p in props if p.get("id") == proposal_id), None)
    if target is None:
        raise ValueError(f"no hypothesis_proposal with id {proposal_id!r}")
    target["status"] = status
    target.setdefault("review", {})
    target["review"] = {"by": by, "rationale": rationale, "at": _dt.datetime.now(
        _dt.timezone.utc).isoformat(timespec="seconds")}
    promoted = False
    if status == ACCEPTED:
        hyps = [h for h in spec.get("hypotheses") or [] if isinstance(h, dict)]
        if not any(h.get("id") == proposal_id for h in hyps):
            hyps.append(promote_to_hypothesis(target))
            spec["hypotheses"] = hyps
            promoted = True
    spec["hypothesis_proposals"] = props
    save_yaml_atomic(path, spec)
    return {"id": proposal_id, "status": status, "promoted": promoted}


def record_critique_report(inv_path: Path | str, report: dict) -> dict:
    """Validate an agent-authored CritiqueReport and record it (status ``open``) in
    ``investigation.yaml::critique_reports[]``, idempotent by id. Advisory — it does not
    change any verdict. The engine validates; the agent authors. Returns the report."""
    from discovery_engine.phases.critique_report import coerce_critique_report
    report = coerce_critique_report(report)          # validate + assign id/status/at
    path = Path(inv_path)
    spec = load_yaml_mapping(path)
    have = [r for r in spec.get("critique_reports") or [] if isinstance(r, dict)]
    ids = {r.get("id") for r in have}
    if report["id"] not in ids:
        have.append(report)
        spec["critique_reports"] = have
        save_yaml_atomic(path, spec)
    return report


def set_critique_report_status(inv_path: Path | str, report_id: str, status: str,
                               *, by: str = "human", rationale: str | None = None) -> dict:
    """Address or dismiss a recorded CritiqueReport (advisory — no state is promoted).
    Returns ``{id, status}``."""
    from discovery_engine.phases.critique_report import ADDRESSED, DISMISSED
    if status not in (ADDRESSED, DISMISSED):
        raise ValueError(f"status must be '{ADDRESSED}' or '{DISMISSED}', got {status!r}")
    path = Path(inv_path)
    spec = load_yaml_mapping(path)
    reports = [r for r in spec.get("critique_reports") or [] if isinstance(r, dict)]
    target = next((r for r in reports if r.get("id") == report_id), None)
    if target is None:
        raise ValueError(f"no critique_report with id {report_id!r}")
    target["status"] = status
    target["review"] = {"by": by, "rationale": rationale, "at": _dt.datetime.now(
        _dt.timezone.utc).isoformat(timespec="seconds")}
    spec["critique_reports"] = reports
    save_yaml_atomic(path, spec)
    return {"id": report_id, "status": status}

def declare_viz(ws_root: Path | str, inv_slug: str, target: str, declare: dict,
                rationale: str, by: str = "human") -> dict:
    """Review-tier write-back for a ``viz_broken`` figure: set ``address`` / ``render`` /
    ``requires`` on the named ``visualizations[]`` entry of ``fig:<study>/<name>``, drop a
    ``rendered:`` field (read by nothing), stamp ``origin: {kind: derived_fix}``, and record
    a ``viz_broken`` resolution. Authored-wins: a rationale is required, the entry must
    exist, and only those three keys may be set. Never dispatched by ``advance``."""
    if not (rationale or "").strip():
        raise ValueError("a rationale is required — --declare edits an authored list")
    vt = _viz_target(target)
    if vt is None:
        raise ValueError(f"target must be fig:<study>/<name>, got {target!r}")
    bad = set(declare) - {"address", "render", "requires"}
    if bad:
        raise ValueError(f"--declare may set only address/render/requires, got {sorted(bad)}")
    if not (declare.get("address") or declare.get("render")):
        raise ValueError("--declare needs an address= or render= (else the entry stays a broken stub)")
    study, name = vt
    sdir = _study_dir_on_disk(ws_root, inv_slug, study)
    if sdir is None:
        raise ValueError(f"no study.yaml for {study!r}")
    y = _ruamel()
    sp = sdir / "study.yaml"
    doc = y.load(sp.read_text(encoding="utf-8"))
    ent = next((e for e in doc.get("visualizations") or []
                if isinstance(e, dict) and e.get("name") == name), None)
    if ent is None:
        raise ValueError(f"{study} declares no visualization named {name!r}")
    from discovery_engine.viz.freshness import BROKEN_STUB, normalize_entry
    if normalize_entry(dict(ent))["kind"] != BROKEN_STUB:
        raise ValueError(f"{target} is not a broken stub — --declare only repairs a figure with "
                         "no renderable identity; re-author a healthy entry through the normal "
                         "study authoring path")
    for k, v in declare.items():
        ent[k] = list(v) if k == "requires" else v
    ent.pop("rendered", None)
    ent["origin"] = {"kind": "derived_fix"}
    from io import StringIO
    buf = StringIO()
    y.dump(doc, buf)
    sp.write_text(buf.getvalue(), encoding="utf-8")
    inv_path = investigations_dir(Path(ws_root)) / inv_slug / "investigation.yaml"
    return record_resolution(inv_path, "viz_broken", target, rationale, by=by)

def _viz_prop(spec: dict, vp: str) -> dict:
    prop = next((p for p in spec.get("viz_proposals") or [] if isinstance(p, dict) and p.get("id") == vp), None)
    if prop is None:
        raise ValueError(f"unknown viz proposal {vp!r} — run `discover cycle` to persist proposals first")
    if prop.get("status", "proposed") != "proposed":
        raise ValueError(f"viz proposal {vp!r} is already {prop.get('status')!r}; only a 'proposed' "
                         "proposal can be accepted or rejected")
    return prop

def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")

def accept_viz_proposal(ws_root: Path | str, inv_slug: str, vp: str, *, address: str | None = None,
                        rationale: str, by: str = "human") -> dict:
    """Author-gated accept: in one call, append a ``visualizations[]`` entry to the proposal's
    study (``origin: {kind: viz_proposal, ref: vp}``, ``covers: [observable]``) and mark the
    proposal ``accepted`` with a history event. Requires a rationale and an ``address`` (the
    suggested catalog address when omitted; a ``needs_author`` proposal has none, so the
    author must supply it). The entry is declared but unrendered -> the frontier item becomes
    ``viz_missing``. Never dispatched by ``advance``. Returns the updated proposal."""
    from discovery_engine.viz.proposals import slugify
    if not (rationale or "").strip():
        raise ValueError("a rationale is required — accepting a proposal edits an authored list")
    inv_path = investigations_dir(Path(ws_root)) / inv_slug / "investigation.yaml"
    spec = load_yaml_mapping(inv_path)
    prop = _viz_prop(spec, vp)
    sug = prop.get("suggested") or {}
    address = address or sug.get("address")
    if not address:
        raise ValueError(f"{vp} is needs_author — supply address=… (a class or figure the author owns)")
    obs = (prop.get("source") or {}).get("observable")
    sdir = _study_dir_on_disk(ws_root, inv_slug, prop.get("study"))
    if sdir is None:
        raise ValueError(f"no study.yaml for {prop.get('study')!r}")
    y = _ruamel()
    sp = sdir / "study.yaml"
    doc = y.load(sp.read_text(encoding="utf-8"))
    vis = doc.get("visualizations")
    if vis is None:
        doc["visualizations"] = vis = []
    name = slugify(sug.get("title") or obs)
    taken = {e.get("name") for e in vis if isinstance(e, dict)}
    if name in taken:
        name = f"{name}-{str(vp).rsplit(':', 1)[-1]}"
    entry: dict = {"name": name, "address": address}
    if sug.get("inputs_map"):
        entry["config"] = {"inputs_map": dict(sug["inputs_map"])}
    entry["covers"] = [obs] if obs else []
    entry["origin"] = {"kind": "viz_proposal", "ref": vp}
    vis.append(entry)
    from io import StringIO
    buf = StringIO()
    y.dump(doc, buf)
    sp.write_text(buf.getvalue(), encoding="utf-8")
    prop["status"] = "accepted"
    prop["history"] = [*(prop.get("history") or []),
                       {"at": _now(), "event": "accepted", "by": by, "rationale": rationale,
                        "address": address, "entry": name}]
    save_yaml_atomic(inv_path, spec)
    return prop

def refresh_narrative(ws_root: Path | str, inv_slug: str, *, rationale: str, by: str = "human") -> dict:
    """Accept-gated refresh of the Narrative tab: stamp ``executive.narrative_basis`` to the
    current member-study basis and append a ``narrative_history`` entry. A blank rationale is
    refused (a human must have re-read the prose); never called from ``persist_cycle``."""
    from discovery_engine.discover.cycle import load_cycle_inputs
    from discovery_engine.projection.narrative_freshness import stamp_narrative
    if not (rationale or "").strip():
        raise ValueError("a rationale is required — refreshing the narrative certifies the prose was re-read")
    inv_path, spec, studies = load_cycle_inputs(ws_root, inv_slug)
    stamp_narrative(spec, studies, rationale=rationale, by=by)
    _validate_extensions({"executive": spec["executive"]})
    save_yaml_atomic(inv_path, spec)
    return spec["executive"]

def accept_verdict(ws_root: Path | str, inv_slug: str, *, rationale: str, by: str = "human") -> dict:
    """Accept-gated verdict accept: set ``executive.verdict_status`` to the engine-computed
    ``executive.computed_verdict_status`` and append a ``verdict_history`` entry. Blank rationale
    refused; errors when no computed status exists yet."""
    from discovery_engine.projection.narrative_freshness import renew_executive
    if not (rationale or "").strip():
        raise ValueError("a rationale is required — accepting the verdict edits authored prose")
    inv_path = investigations_dir(Path(ws_root)) / inv_slug / "investigation.yaml"
    spec = load_yaml_mapping(inv_path)
    computed = (spec.get("executive") or {}).get("computed_verdict_status")
    if not computed:
        raise ValueError("no executive.computed_verdict_status to accept — run the status refresh first")
    renew_executive(spec, computed_verdict_status=computed,
                    accept_verdict_rationale=rationale, by=by)
    _validate_extensions({"executive": spec["executive"]})
    save_yaml_atomic(inv_path, spec)
    return spec["executive"]

def reject_viz_proposal(ws_root: Path | str, inv_slug: str, vp: str, *, rationale: str,
                        by: str = "human") -> dict:
    """Terminal reject (rationale required): the content-addressed id means it never re-emits."""
    if not (rationale or "").strip():
        raise ValueError("a rationale is required — a decision on a proposal documents why")
    inv_path = investigations_dir(Path(ws_root)) / inv_slug / "investigation.yaml"
    spec = load_yaml_mapping(inv_path)
    prop = _viz_prop(spec, vp)
    prop["status"] = "rejected"
    prop["history"] = [*(prop.get("history") or []),
                       {"at": _now(), "event": "rejected", "by": by, "rationale": rationale}]
    save_yaml_atomic(inv_path, spec)
    return prop

def _cap_gap_load(ws_root, inv_slug: str, cg: str) -> tuple[Path, dict, dict]:
    inv_path = investigations_dir(Path(ws_root)) / inv_slug / "investigation.yaml"
    spec = load_yaml_mapping(inv_path)
    gap = next((g for g in spec.get("capability_gaps") or [] if isinstance(g, dict) and g.get("id") == cg), None)
    if gap is None:
        raise ValueError(f"unknown capability gap {cg!r} — run `discover cycle` to persist gaps first")
    return inv_path, spec, gap

def accept_capability_gap(ws_root: Path | str, inv_slug: str, cg: str, *, rationale: str,
                          by: str = "human") -> dict:
    """Rationale-gated accept of a persisted capability gap (load -> module validate -> atomic
    save). Authorizes a build; never builds. Returns the updated gap."""
    from discovery_engine.feedback import capability_gaps as _cg
    inv_path, spec, gap = _cap_gap_load(ws_root, inv_slug, cg)
    _cg.accept_capability_gap(gap, rationale=rationale, by=by)
    save_yaml_atomic(inv_path, spec)
    return gap

def reject_capability_gap(ws_root: Path | str, inv_slug: str, cg: str, *, rationale: str,
                          by: str = "human") -> dict:
    """Terminal reject (rationale required); the content-addressed id never re-emits."""
    from discovery_engine.feedback import capability_gaps as _cg
    inv_path, spec, gap = _cap_gap_load(ws_root, inv_slug, cg)
    _cg.reject_capability_gap(gap, rationale=rationale, by=by)
    save_yaml_atomic(inv_path, spec)
    return gap

def _close_built_viz_proposals(on_disk: dict, viz_state: dict, studies: dict) -> list[str]:
    """§2E: flip an ``accepted`` proposal to ``built`` once its materialized entry (matched by
    ``origin.ref``) classifies ``fresh`` (rendered AND current). Mutates ``on_disk``;
    ``rejected``/``built`` are terminal and untouched. Returns the closed ids."""
    from discovery_engine.viz.freshness import FRESH, classify_viz, normalize_entry
    from discovery_engine.phases import model_architect as _ma
    try:
        bound = _ma.bind_model_versions(studies or {})
    except Exception:  # noqa: BLE001
        bound = {}
    done: list[str] = []
    for p in on_disk.get("viz_proposals") or []:
        if not isinstance(p, dict) or p.get("status") != "accepted":
            continue
        study = p.get("study")
        entry = next((e for e in (studies.get(study) or {}).get("visualizations") or []
                      if isinstance(e, dict) and (e.get("origin") or {}).get("ref") == p.get("id")), None)
        st = (viz_state or {}).get(study)
        if entry is None or st is None:
            continue
        vs = normalize_entry(entry)
        row = st.get(vs["name"])
        arms = bound.get(study) or {}
        mv = next(iter(arms.values()))["id"] if arms else None
        if not row or classify_viz(vs, row, st.get("_latest"), mv=mv) != FRESH:
            continue
        meta = row.get("meta") or {}
        p["built"] = {"at": _now(), "chart": row.get("path"),
                      "source_run_id": meta.get("source_run_id"), "content_hash": row.get("content_hash")}
        p["status"] = "built"
        p["history"] = [*(p.get("history") or []),
                        {"at": p["built"]["at"], "event": "built", "by": "engine",
                         "chart": row.get("path"), "source_run_id": meta.get("source_run_id")}]
        done.append(p["id"])
    return done

def review_revision(inv_path: Path | str, proposal_id: str, event: str, rationale: str,
                    by: str = "human") -> dict:
    """Approve or reject a model-revision proposal: set its ``status`` and append a
    ``{at, event, by, rationale}`` event to its ``history``. That is the ONLY mutation (no
    verdict / evidence / finding change). A rationale is required; an unknown id errors."""
    if event not in ("approved", "rejected"):
        raise ValueError(f"event must be 'approved' or 'rejected', got {event!r}")
    if not (rationale or "").strip():
        raise ValueError("a rationale is required — a decision on a revision documents why")
    path = Path(inv_path)
    spec = load_yaml_mapping(path)
    props = spec.get("revision_proposals") or []
    prop = next((p for p in props if isinstance(p, dict) and p.get("id") == proposal_id), None)
    if prop is None:
        raise ValueError(f"unknown revision proposal {proposal_id!r} in {path}")
    if prop.get("status", "proposed") != "proposed":
        raise ValueError(f"proposal {proposal_id!r} is already {prop.get('status')!r}; "
                         "only a 'proposed' proposal can be approved or rejected")
    prop["status"] = event
    hist = list(prop.get("history") or [])
    hist.append({"at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                 "event": event, "by": by, "rationale": rationale})
    prop["history"] = hist
    save_yaml_atomic(path, spec)
    return prop

def _complement_band(band) -> dict | None:
    """The band disjoint from a numeric ``{low, high}`` pass band, or None when it can't be derived."""
    if not isinstance(band, dict):
        return None
    lo, hi = band.get("low"), band.get("high")
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
    if num(lo) and lo > 0:
        return {"op": "<", "value": lo}
    if num(hi):
        return {"op": ">", "value": hi}
    return None

def _pick_candidate(prop: dict, change_index: int | None) -> dict | None:
    cands = [c for c in (prop.get("candidate_changes") or []) if isinstance(c, dict)]

    def derived(c):
        if c.get("kind") == "composite":
            return bool(c.get("replace_with"))
        if c.get("kind") == "param":
            return bool(c.get("name")) and bool(c.get("to"))
        return False
    if change_index is not None:
        c = cands[change_index] if 0 <= change_index < len(cands) else None
        return c if c is not None and derived(c) else None
    return next((c for c in cands if derived(c)), None)

def apply_revision(ws_root: Path | str, inv_slug: str, proposal_id: str,
                   change_index: int | None = None) -> dict:
    """Turn an APPROVED model-revision proposal into a child study + model version +
    discriminable hypotheses (decision §4 "Apply"). Never edits the parent study.

    Refuses (writes nothing; ``status: needs_build`` + ``mechanism``) unless the candidate
    composite resolves and the child is run-ready. Engine-written hypotheses / finding stubs
    carry ``origin`` and ``statement: null`` — the engine never writes prose."""
    from discovery_engine.discover.cycle import load_cycle_inputs
    from discovery_engine.phases import evaluation as _ev
    from discovery_engine.growth import refinement as _sr
    from discovery_engine.growth import seeding as _ss
    from discovery_engine.phases.model_architect import model_version, resolve_composite

    ws = Path(ws_root)
    inv_path, inv_spec, studies = load_cycle_inputs(ws, inv_slug)
    prop = next((p for p in inv_spec.get("revision_proposals") or []
                 if isinstance(p, dict) and p.get("id") == proposal_id), None)
    if prop is None:
        raise ValueError(f"unknown revision proposal {proposal_id!r} in {inv_path}")
    if prop.get("status", "proposed") != "approved":
        raise ValueError(f"proposal {proposal_id!r} is {prop.get('status', 'proposed')!r}; "
                         "only an 'approved' proposal can be applied")
    src = prop.get("source") or {}
    parent_slug = src.get("study")
    parent = studies.get(parent_slug)
    if not isinstance(parent, dict):
        raise ValueError(f"proposal source study {parent_slug!r} not found")
    cand = _pick_candidate(prop, change_index)
    if cand is None:
        return {"status": "needs_build", "study_path": None, "model_version": None,
                "mechanism": "no fully-derived candidate change — a human must author the mechanism "
                             "(/viva-expert) before this proposal can be applied"}

    base = next((a for a in parent.get("baseline") or [] if isinstance(a, dict)), {})
    new_slug = f"{parent_slug}-rev-{str(proposal_id).rsplit(':', 1)[-1]}"
    mc: dict = {"ref": proposal_id, "note": f"revision {proposal_id}"}
    if cand["kind"] == "composite":
        mc["composite"] = cand["replace_with"]
        composite = cand["replace_with"]
    elif cand["kind"] == "param":
        mc["params"] = {cand["name"]: cand["to"][0]}
        composite = base.get("composite")
    else:
        return {"status": "needs_build", "study_path": None, "model_version": None,
                "mechanism": f"candidate kind {cand['kind']!r} is not derivable; needs an author"}

    if not resolve_composite(composite, ws)["resolves"]:
        return {"status": "needs_build", "study_path": None, "model_version": None,
                "mechanism": f"composite {composite!r} does not resolve in this environment — "
                             "BUILD route: wrap/build it (/viva-expert), then re-apply"}

    child = _ss.build_seeded_study(parent, inv_spec, new_slug=new_slug, finding_id=src.get("finding"),
                                   model_change=mc)
    child["model_change"] = {**(child.get("model_change") or {}), **mc}
    if cand["kind"] == "param":
        refined, _ = _sr.refine_study(child, needs=[{"kind": _sr.SWEEP}], sweep_param=cand["name"],
                                      sweep_values=list(cand["to"]))
        child.update({k: refined[k] for k in ("variants",) if k in refined})
    pg = dict(child.get("pipeline_gate") or {})
    pg["prerequisites"] = [*(pg.get("prerequisites") or []),
                           {"study": parent_slug, "relation": "alternative-to"}]
    child["pipeline_gate"] = pg
    test = prop.get("discriminating_test") or {}
    obs = src.get("observable")
    if test.get("behavior_test"):
        child["findings"] = [{"id": f"f-{new_slug}-{test['behavior_test']}",
                              "statement": None,
                              "evidence": {"from_test": test["behavior_test"],
                                           **({"measure": obs} if obs else {})},
                              "origin": {"kind": "revision", "ref": proposal_id}}]
    gaps = _ss.is_run_ready(child)
    if gaps:
        return {"status": "needs_build", "study_path": None, "model_version": None,
                "mechanism": "child study not run-ready: " + "; ".join(gaps)}

    origin = {"kind": "revision", "ref": proposal_id}
    hyps: list[dict] = []
    band = test.get("pass_if")
    other = _complement_band(band)
    if obs and isinstance(band, dict) and other:
        hyps = [
            {"id": f"h-{new_slug}-formalism-artifact", "study": new_slug,
             "statement": None, "predictions": [{"observable": obs, "expected": dict(band)}],
             "status": "open", "origin": dict(origin)},
            {"id": f"h-{new_slug}-real-divergence", "study": new_slug,
             "statement": None, "predictions": [{"observable": obs, "expected": other}],
             "status": "open", "origin": dict(origin)},
        ]
    elif obs and isinstance(band, dict):
        hyps = [{"id": f"h-{new_slug}-formalism-artifact", "study": new_slug, "statement": None,
                 "predictions": [{"observable": obs, "expected": dict(band)}],
                 "status": "open", "origin": dict(origin)}]

    params = dict(base.get("params") or {})
    params.update(mc.get("params") or {})
    parent_mv = prop.get("affected_model_version")
    # parent_ids is left to bind_model_versions (regenerable: _study_origin maps the revision
    # child to its seeded_from parent's FIRST baseline arm mv, == affected_model_version normally).
    mv = model_version(composite, params, parent_ids=[parent_mv] if parent_mv else [], origin=origin)
    invalidated = _ev.invalidate_dependents({"kind": "model", "id": parent_slug}, inv_spec, studies)

    path = _write_seeded_study(ws, inv_slug, new_slug, child)
    doc = load_yaml_mapping(inv_path)  # plain dicts, as review_revision does (atomic safe dump)
    if hyps:
        if not isinstance(doc.get("hypotheses"), list):
            doc["hypotheses"] = []
        doc["hypotheses"].extend(hyps)
    if not isinstance(doc.get("model_versions"), list):
        doc["model_versions"] = []
    if not any(isinstance(m, dict) and m.get("id") == mv["id"] for m in doc["model_versions"]):
        doc["model_versions"].append(mv)
    for p in doc.get("revision_proposals") or []:
        if isinstance(p, dict) and p.get("id") == proposal_id:
            p["status"] = "applied"
            hist = p.get("history")
            if not isinstance(hist, list):
                hist = p["history"] = []
            hist.append({"at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                         "event": "applied", "by": "human", "child_study": new_slug,
                         "model_version": mv["id"]})
    save_yaml_atomic(inv_path, doc)
    return {"status": "applied", "study_path": str(path), "study": new_slug,
            "model_version": mv["id"], "hypotheses": [h["id"] for h in hyps],
            "invalidated": invalidated}

def relate(study_spec: dict, upstream: str, relation: str) -> dict:
    """Return a NEW study spec with the epistemic ``relation`` to ``upstream`` set.

    The relation is set on the edge where it already lives: a
    ``pipeline_gate.prerequisites`` entry (the canonical gate dependency — it then
    carries both the organizational ``condition`` and the epistemic ``relation``, the
    RFC-0006 unified form) is preferred, then a ``parent_studies`` backbone entry (a
    bare ``{study}`` dict, or a plain string promoted to ``{study, relation}``). Only
    when the dependency is declared nowhere is an epistemic-only prerequisite appended.
    Non-mutating; raises ``ValueError`` on an off-vocabulary relation.
    """
    if relation not in EPISTEMIC_RELATIONS:
        raise ValueError(f"relation must be one of {EPISTEMIC_RELATIONS}, got {relation!r}")
    spec = copy.deepcopy(study_spec)
    _set_relation_inplace(spec, upstream, relation)
    return spec

def connect_investigation(ws_root: Path | str, slug: str,
                          default_relation: str = "builds-on") -> dict[str, dict[str, list[str]]]:
    """Connect an investigation's narrative arc by authoring the missing epistemic edges.

    *Auto-enriches* every bare prerequisite in the **canonical** field
    (``pipeline_gate.prerequisites`` with no ``relation``) with ``default_relation``,
    persisting each change comment-preserving. Already-authored relations are left intact.

    A dependency declared **only** in the legacy ``parent_studies`` backbone (a plain
    string, or a bare ``{study}`` dict) is **not** blanket-asserted — that field can hold
    organizational parents that are not scientific edges, so defaulting them all to
    ``builds-on`` would over-connect the argument graph. Those are *surfaced* for an
    explicit, intentional :func:`relate` instead of silently skipped.

    Returns ``{"enriched": {slug: [ups]}, "needs_relate": {slug: [ups]}}``.
    """
    from discovery_engine.discover.cycle import load_cycle_inputs
    if default_relation not in EPISTEMIC_RELATIONS:
        raise ValueError(f"default_relation must be one of {EPISTEMIC_RELATIONS}, got {default_relation!r}")
    _, inv, studies = load_cycle_inputs(ws_root, slug)
    enriched: dict[str, list[str]] = {}
    needs_relate: dict[str, list[str]] = {}
    for s in inv.get("studies") or list(studies):
        spec = studies.get(s)
        if spec is None:
            continue
        pg = spec.get("pipeline_gate") or {}
        prereqs = pg.get("prerequisites") or [] if isinstance(pg, dict) else []
        pg_bare = [p["study"] for p in prereqs
                   if isinstance(p, dict) and p.get("study") and not p.get("relation")]
        pg_slugs = {p["study"] for p in prereqs
                    if isinstance(p, dict) and p.get("study")}
        ps_bare: list[str] = []
        for e in spec.get("parent_studies") or []:
            if isinstance(e, str) and e:
                up = e
            elif isinstance(e, dict) and e.get("study") and not e.get("relation"):
                up = e["study"]
            else:
                continue
            if up not in pg_slugs and up not in ps_bare:
                ps_bare.append(up)
        if pg_bare:
            done = _enrich_file(_study_path(ws_root, slug, s), pg_bare, default_relation)
            if done:
                enriched[s] = done
        if ps_bare:
            needs_relate[s] = ps_bare
    return {"enriched": enriched, "needs_relate": needs_relate}

def _study_path(ws_root: Path | str, slug: str, study: str) -> Path:
    ws = Path(ws_root)
    for base in (studies_dir(ws), investigations_dir(ws) / slug / "studies"):
        sp = base / study / "study.yaml"
        if sp.exists():
            return sp
    return studies_dir(ws) / study / "study.yaml"

def _validate_extensions(block: dict) -> None:
    """Validate the new investigation blocks against the bundled JSON Schema (write boundary only)."""
    import json
    import jsonschema
    schema = json.loads((Path(__file__).parent.parent / "schemas" / "investigation-extensions.schema.json")
                        .read_text(encoding="utf-8"))
    jsonschema.validate(json.loads(json.dumps(block, default=str)), schema)

def _apply_refinement(ws: Path, inv_slug: str, slug: str, refined: dict) -> Path:
    """I/O: merge the refinement fields (robustness / variants / baseline / refined_from)
    into the existing study.yaml, comment-preserving. Pure transform lives in
    study_refinement.refine_study."""
    y = _ruamel()
    for base in (studies_dir(ws), investigations_dir(ws) / inv_slug / "studies"):
        sp = base / slug / "study.yaml"
        if sp.exists():
            with sp.open(encoding="utf-8") as fh:
                doc = y.load(fh)
            for key in ("robustness", "variants", "baseline", "refined_from"):
                if key in refined:
                    doc[key] = refined[key]
            with sp.open("w", encoding="utf-8") as fh:
                y.dump(doc, fh)
            return sp
    raise FileNotFoundError(f"study.yaml for {slug} not found")

def _write_seeded_study(ws: Path, inv_slug: str, slug: str, study: dict) -> Path:
    """I/O: write studies/<slug>/study.yaml (comment-preserving) and register the slug
    in the investigation's ``studies`` list. Pure authoring lives in study_seeding."""
    y = _ruamel()
    sdir = studies_dir(ws) / slug
    sdir.mkdir(parents=True, exist_ok=True)
    sp = sdir / "study.yaml"
    with sp.open("w", encoding="utf-8") as fh:
        y.dump(study, fh)
    inv_path = investigations_dir(ws) / inv_slug / "investigation.yaml"
    if inv_path.exists():
        with inv_path.open(encoding="utf-8") as fh:
            inv = y.load(fh)
        members = inv.get("studies")
        if isinstance(members, list) and slug not in members:
            members.append(slug)
            with inv_path.open("w", encoding="utf-8") as fh:
                y.dump(inv, fh)
    return sp
