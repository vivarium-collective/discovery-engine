"""Discover cycle — the write/wire layer that drives the RFC-0005/0006 ops.

The knowledge / argument / narrate / audit / synthesize modules are pure
derive-first projections: they take dicts, return dicts, and (by design) persist
nothing. That left four of the six cycle operations unreachable from any verb and
the canonical verdict un-writable — which is why an investigation's study verdicts
read empty even when the gate roll-up would derive them.

This module closes that gap:

* :func:`run_cycle` chains all six ops into ONE projection — one invocation is one
  turn of the discovery cycle (derive verdicts -> derive the argument graph ->
  narrate -> audit for unsupported transitions -> roll up the synthesis spine ->
  collect the open calls). It is pure: give it an ``inv_spec`` and its member
  studies and it returns the whole bundle, authored blocks already merged over the
  derived projection.
* :func:`set_verdict` is the authored canonical-verdict write op the derive-on-read
  layer lacked — it records a human's verdict (and rationale) on a study so
  ``canonical_verdict`` honors it (authored wins).
* :func:`persist_cycle` writes the projection into ``investigation.yaml`` as a
  regenerable ``knowledge_cycle`` cache, WITHOUT touching the sparse authored
  ``knowledge`` / ``argument`` blocks — derive-first stays intact, so the cache can
  always be thrown away and recomputed.
* :func:`load_cycle_inputs` reads an investigation + its member studies from disk.

The science advances by re-running the cycle: fill verdicts -> narrate the arc ->
the audit surfaces the gaps -> close them by extending models/studies -> re-run.
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

from .argument_store import derive_argument, merge_argument
from .investigation_import import investigation_members, investigations_dir, studies_dir
from .knowledge_store import canonical_verdict, derive_knowledge, merge_knowledge
from .narrative import audit, narrate
from .run_command import render_command, run_command
from .study_io import atomic_write, load_yaml, load_yaml_mapping, save_yaml_atomic
from .synthesis import open_calls, synthesize_gates
from .validation import ValidationContext, run_validators


def _ruamel() -> YAML:
    """Comment-preserving round-trip YAML — the same config the canonicalize/reconcile
    writers use (``study_canonicalize._ruamel``). A ``width`` well past any real line
    keeps long scalars on one line so an edge enrichment is a one-field diff, not a
    whole-file reflow that drops load-bearing ``# …`` annotations."""
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y


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

#: the canonical scientific verdict vocabulary a human may author
VERDICT_VOCAB = ("supported", "qualified", "inconclusive", "refuted", "unassessed")

#: the epistemic relation vocabulary a prerequisite edge may carry (RFC-0005 §4).
#: These are the facet ``argument_store`` reads to build claim->claim edges; a bare
#: ``condition: tests-passed`` prerequisite is organizational only and yields no
#: argument edge until its epistemic relation is authored.
EPISTEMIC_RELATIONS = ("builds-on", "tests", "refutes", "alternative-to", "motivates")
# The frontier-item kinds a resolution can discharge (mirror discovery_engine priorities).
RESOLUTION_KINDS = ("unaddressed_criterion", "open_challenge", "narrative_gap",
                    "open_decision", "divergence", "open_question",
                    "validation_finding", "stale_validation", "pending_gate",
                    "revision_proposed", "model_disagreement", "anomaly",
                    "viz_broken", "viz_missing", "viz_proposed", "viz_stale",
                    "viz_misplaced", "viz_duplicated", "viz_untied")


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
    from .viz_freshness import BROKEN_STUB, normalize_entry
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
    from .viz_proposals import slugify
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
    from .narrative_freshness import stamp_narrative
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
    from .narrative_freshness import renew_executive
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
    from . import capability_gaps as _cg
    inv_path, spec, gap = _cap_gap_load(ws_root, inv_slug, cg)
    _cg.accept_capability_gap(gap, rationale=rationale, by=by)
    save_yaml_atomic(inv_path, spec)
    return gap


def reject_capability_gap(ws_root: Path | str, inv_slug: str, cg: str, *, rationale: str,
                          by: str = "human") -> dict:
    """Terminal reject (rationale required); the content-addressed id never re-emits."""
    from . import capability_gaps as _cg
    inv_path, spec, gap = _cap_gap_load(ws_root, inv_slug, cg)
    _cg.reject_capability_gap(gap, rationale=rationale, by=by)
    save_yaml_atomic(inv_path, spec)
    return gap


def load_workspace_build_hook(ws_root: Path | str | None):
    """Import ``<pkg>.build_hook`` and call ``register_build_hook(registry)``; the hook is
    ``registry['build_capability']`` (``fn(gap, ws_root) -> {status, process, detail, gap_id}``).
    Same package discovery as ``register_evaluators``. None if absent; a broken hook is skipped."""
    import sys
    from .study_evaluator import _workspace_evaluator_packages
    if ws_root is None:
        return None
    key = str(Path(ws_root).resolve())
    if key not in sys.path:
        sys.path.insert(0, key)
    registry: dict = {}
    for pkg in _workspace_evaluator_packages(ws_root):
        try:
            mod = __import__(f"{pkg}.build_hook", fromlist=["register_build_hook"])
            reg = getattr(mod, "register_build_hook", None)
            if callable(reg):
                reg(registry)
        except Exception:  # noqa: BLE001 -- a broken workspace hook must never crash the cycle
            continue
    fn = registry.get("build_capability")
    return fn if callable(fn) else None


def _close_built_viz_proposals(on_disk: dict, viz_state: dict, studies: dict) -> list[str]:
    """§2E: flip an ``accepted`` proposal to ``built`` once its materialized entry (matched by
    ``origin.ref``) classifies ``fresh`` (rendered AND current). Mutates ``on_disk``;
    ``rejected``/``built`` are terminal and untouched. Returns the closed ids."""
    from .viz_freshness import FRESH, classify_viz, normalize_entry
    from . import model_architect as _ma
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
    from . import evaluation as _ev
    from . import study_refinement as _sr
    from . import study_seeding as _ss
    from .model_architect import model_version, resolve_composite

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


# ---------------------------------------------------------------------------
# run_cycle — one invocation = one turn of the discovery cycle
# ---------------------------------------------------------------------------

def run_cycle(inv_spec: dict, studies_by_slug: dict[str, dict], *,
              evidence_state: dict | None = None, catalog: list | None = None,
              viz_state: dict | None = None, viz_catalog: list | None = None) -> dict:
    """Project one full discovery cycle over an investigation + its studies.

    Pure: no disk I/O. Each derived projection is merged over its authored block
    (``inv_spec['knowledge']`` / ``inv_spec['argument']``) with the
    authored-wins / empty-never-clobbers rule, then narrate/audit/synthesize read
    the merged argument graph. ``catalog`` (optional) is a pre-resolved capability
    catalog passed in as data by an impure caller; absent, it is derived unresolved.
    """
    knowledge = merge_knowledge(inv_spec.get("knowledge"),
                                derive_knowledge(inv_spec, studies_by_slug))
    argument = merge_argument(inv_spec.get("argument"),
                              derive_argument(inv_spec, studies_by_slug))

    narrative = narrate(argument, inv_spec.get("question"), inv_spec.get("audience"))
    gaps = audit(narrative, argument)
    gates = synthesize_gates(inv_spec, argument["claims"])
    calls = open_calls(inv_spec, argument["claims"], gaps)

    # RFC-0007: run the registered validators over the derived cycle. Each returns
    # typed (type, scope) validation records; the frontier projects the failing ones
    # and invariants() checks execution ⊥ scientific-judgment across them.
    ctx = ValidationContext(inv_spec=inv_spec, studies=studies_by_slug,
                            argument=argument, narrative=narrative, audit=gaps,
                            viz_state=viz_state or {}, viz_catalog=viz_catalog or [])

    bundle = {
        "knowledge": knowledge,
        "argument": argument,
        "narrative": narrative,
        "audit": gaps,
        "gates": gates,
        "open_calls": calls,
        "validations": run_validators(ctx),
    }
    # Suggestions for new studies (the branch op, made first-class + expert-gated).
    bundle["suggestions"] = suggest_studies(bundle, inv_spec, studies_by_slug)
    # Closed-loop feedback (pure): the raw signals and the typed revision proposals they
    # yield, so the frontier can raise them as non-blocking items.
    from . import evaluation as _ev
    from .hypotheses import anomalies as _anoms
    from .model_architect import build_catalog
    from .model_revision import propose_model_revisions
    mvs = project_model_versions(studies_by_slug, inv_spec.get("model_versions"))
    evaluation = _ev.compare_hypotheses(inv_spec, studies_by_slug, evidence_state)
    from .model_revision import _contradicted_studies
    bad = _contradicted_studies(evidence_state, evaluation)  # evidence problem -> REPAIR, not revise
    bundle["signals"] = {
        "model_disagreements": [r for r in _ev.model_disagreement_map(inv_spec, studies_by_slug)
                                if r.get("study") not in bad],
        "anomalies": [a for a in _anoms(inv_spec or {}, _ev._keyed_specs(studies_by_slug))
                      if a.get("study") not in bad],
    }
    bundle["revision_proposals"] = propose_model_revisions(
        inv_spec, studies_by_slug, evaluation, evidence_state, mvs,
        catalog if catalog is not None else build_catalog(studies_by_slug))
    if viz_state:  # hermetic: no harvested viz state -> no viz proposals
        from .viz_proposals import propose_visualizations
        bundle["viz_proposals"] = propose_visualizations(
            inv_spec, studies_by_slug, bundle, viz_state, viz_catalog)
    # Always assessed. Headless (no catalog): scope_of is None for every address, so an unresolved
    # port is `not_assessed` (no false alarm) while an AUTHORED `scope:` is still honored.
    from .viz_appropriateness import investigation_viz_appropriateness
    bundle["viz_appropriateness"] = investigation_viz_appropriateness(
        studies_by_slug, _viz_scope_of(viz_catalog))
    from .capability_gaps import propose_capability_gaps
    # The catalog (carrying `capability` + `builds`) is the same one the acquisition route reads.
    gaps = propose_capability_gaps(
        inv_spec, studies_by_slug, catalog if catalog is not None else build_catalog(studies_by_slug))
    if gaps:
        bundle["capability_gaps"] = gaps
    return bundle


def _viz_scope_of(viz_catalog):
    """address -> the viz class's input-port schema, from the injected ``viz_catalog`` (the same
    catalog the viz loop threads). Unlisted / port-less addresses -> None (scope UNKNOWN)."""
    ports = {}
    for c in viz_catalog or []:
        if isinstance(c, dict) and c.get("address") and isinstance(c.get("inputs"), dict):
            ports[c["address"]] = c["inputs"]
    return lambda address: ports.get(address)


def _cycle_with_evidence(ws_root, inv_slug, inv_spec, studies) -> dict:
    """run_cycle with the Phase-A evidence state computed FIRST, so contradicted
    (evidence-problem) studies are known to the revision proposer and the frontier."""
    from . import evidence_integrity as _ei
    ev = _ei.reconcile_evidence_state(
        inv_spec, studies, _load_runs_by_study(Path(ws_root), inv_slug, studies))
    from .model_architect import build_catalog
    viz = viz_inventory(ws_root, inv_slug, studies)  # the one impure viz harvest
    return run_cycle(inv_spec, studies, evidence_state=ev,
                     catalog=build_catalog(studies, ws_root),  # resolved: enables composite swaps
                     viz_state=viz, viz_catalog=None)  # workbench catalog supplied by a later task


# ---------------------------------------------------------------------------
# set_verdict — the authored canonical-verdict write op
# ---------------------------------------------------------------------------

def set_verdict(study_path: Path | str, verdict: str, rationale: str | None = None) -> dict:
    """Author a canonical verdict on a study (authored wins over the derived roll-up).

    Writes ``verdict`` (and, when given, ``verdict_rationale``) into study.yaml and
    returns the resulting :func:`canonical_verdict` record. Raises ``ValueError`` on
    an off-vocabulary verdict.
    """
    if verdict not in VERDICT_VOCAB:
        raise ValueError(f"verdict must be one of {VERDICT_VOCAB}, got {verdict!r}")
    path = Path(study_path)
    spec = load_yaml_mapping(path)
    spec["verdict"] = verdict
    if rationale:
        spec["verdict_rationale"] = rationale
    save_yaml_atomic(path, spec)
    return canonical_verdict(spec)


# ---------------------------------------------------------------------------
# relate / connect — author the epistemic edges that connect the argument graph
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# persist_cycle — regenerable cache, authored blocks untouched
# ---------------------------------------------------------------------------

def build_cycle_state(result: dict, inv_spec: dict, *, model_versions: list | None = None,
                      studies: dict | None = None, evidence_state: dict | None = None) -> dict:
    """Project a **slim, actionable** summary of the cycle — the few dozen lines a
    human, the dashboard, or the next cycle actually needs, instead of the full
    regenerable projection. Carries the cycle's *outputs* that previously never fed
    forward: the value-ranked opportunities, suggestions, diagnosis, validator census,
    and drift. The full `knowledge`/`argument`/`narrative` are recomputable on demand
    via :func:`run_cycle`, so they are not dumped here."""
    from . import discovery_engine as de
    clo = de.closure(result, inv_spec)
    na = de.next_action(result, inv_spec)
    opps = de.opportunities(result, inv_spec)
    diag = de.diagnose(result, inv_spec)
    frontier = de.frontier(result, inv_spec)
    recs = result.get("validations") or []
    census: dict[str, int] = {}
    for r in recs:
        st = (r.get("result") or {}).get("execution_status")
        census[st] = census.get(st, 0) + 1
    sugg = result.get("suggestions") or []
    from .validation import stale_records
    state = {
        "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "closed": clo["closed"],
        "frontier_size": clo["open_items"],
        "next_action": ({"op": na["op"], "kind": na["kind"], "target": na["target"]}
                        if na else None),
        "top_opportunities": [{"source": o["source"], "action": o["action_type"],
                               "score": o["value"]["score"]} for o in opps[:8]],
        "suggestions": {"ready": sum(1 for s in sugg if s.get("status") == "ready"),
                        "awaiting_expert": sum(1 for s in sugg if s.get("status") == "awaiting_expert")},
        "diagnosis": {"flags": [f["flag"] for f in diag["flags"]],
                      "validator_pass_rate": diag["validator_pass_rate"]},
        # the recommendation->action bridge: the next moves + how they'd be executed,
        # for the Decide surface (nothing runs until a human/--autonomous step does).
        "execution_plan": [{"source": p["source"], "action": p["action_type"],
                            "score": p["score"], "consequence": p["consequence"],
                            "command": p["command"]}
                           for p in de.execute_plan(result, inv_spec)[:8]],
        "validator_census": census,
        "drift": {"divergences": sum(1 for i in frontier if i["kind"] == "divergence"),
                  "stale_validations": len(stale_records(recs, inv_spec.get("validation_snapshot")))},
    }
    vrecs = [r for r in recs if r.get("type") == "visual" and r.get("scope") == "figure"]
    if vrecs:
        crit = lambda r: (r.get("criteria") or [None])[0]  # noqa: E731
        state["viz_census"] = {
            "fresh": sum(1 for r in vrecs if crit(r) == "fresh"),
            "stale": sum(1 for r in vrecs if crit(r) == "stale"),
            "unrendered": sum(1 for r in vrecs if crit(r) == "unrendered"),
            "broken": sum(1 for r in vrecs if crit(r) == "broken_stub"),
            "untracked_files": sum(1 for r in vrecs if crit(r) == "untracked"),
        }
        vps = [p for p in inv_spec.get("viz_proposals") or [] if isinstance(p, dict)]
        state["viz_census"]["proposals"] = {
            "open": sum(1 for i in frontier if i["kind"] == "viz_proposed"),
            "accepted": sum(1 for p in vps if p.get("status") == "accepted"),
            "built": sum(1 for p in vps if p.get("status") == "built")}
        state["drift"]["stale_viz"] = state["viz_census"]["stale"]
    va = result.get("viz_appropriateness")
    if va:
        rows = [r for rs in va["per_study"].values() for r in rs]
        count = lambda k: sum(1 for r in rows for i in r["issues"] if i["kind"] == k)  # noqa: E731
        state["viz_appropriateness"] = {
            "misplaced": count("misplaced_scope"), "untied": count("untied"),
            "duplicated": len(va["duplicated_cross_arm"]), "not_assessed": va["not_assessed"],
            "any_issue": bool(va["any_issue"]),
            "untied_surfaced": bool(((inv_spec or {}).get("cycle_flags") or {}).get("viz_untied"))}
    if studies is not None:
        state["freshness"] = _tab_freshness(inv_spec or {}, studies)
    if model_versions is not None:
        state["model_versions"] = [m["id"] for m in model_versions]
    if studies is not None:
        # ScientificState snapshot (decision §3.5): what the next cycle diffs against.
        from . import evaluation as _ev
        from .hypotheses import anomalies as _anoms
        cmp = _ev.compare_hypotheses(inv_spec, studies, evidence_state)
        n_anoms = len(_anoms(inv_spec or {}, _ev._keyed_specs(studies)))
        state["evaluation_snapshot"] = _ev.snapshot_evaluation(
            cmp, evidence_state, model_versions or [], n_anomalies=n_anoms)
    return state


def _tab_freshness(inv_spec: dict, studies: dict) -> dict:
    """Per-tab freshness for the summary. The computed verdict is what ``investigation_status``
    already wrote on the executive -- not recomputed here."""
    from .narrative_freshness import investigation_freshness
    return investigation_freshness(
        inv_spec, studies,
        computed_verdict_status=(inv_spec.get("executive") or {}).get("computed_verdict_status"))


def persist_cycle(inv_path: Path | str, inv_spec: dict, result: dict, *, studies: dict | None = None,
                  evidence_state: dict | None = None, viz_state: dict | None = None,
                  catalog: list | None = None) -> dict:
    """Write the slim ``cycle_state`` summary into investigation.yaml.

    Re-reads the on-disk spec (so concurrent authored edits are never clobbered),
    sets only the regenerable ``cycle_state``, and atomically rewrites. The authored
    blocks are left alone. **Retires the old bloated ``knowledge_cycle`` cache** (a
    multi-thousand-line dump of the full projection that nothing read); the full
    projection is recomputable via :func:`run_cycle`.
    """
    from .validation import _DEPRECATED_INV_FIELDS, _RETIRED_INV_CACHES
    path = Path(inv_path)
    on_disk = load_yaml_mapping(path) if path.exists() else dict(inv_spec)
    for dead in (*_RETIRED_INV_CACHES, *_DEPRECATED_INV_FIELDS):
        on_disk.pop(dead, None)  # retire bloat caches + deprecated fields (0 readers)
    _renew_executive_decisions(on_disk)  # move resolved decisions out of the open list
    mvs = project_model_versions(studies, on_disk.get("model_versions")) if studies is not None else None
    if mvs is not None:
        _validate_extensions({"model_versions": mvs})
        on_disk["model_versions"] = mvs
    new = [p for p in result.get("revision_proposals") or [] if isinstance(p, dict)]
    if new:
        have = [r for r in on_disk.get("revision_proposals") or []]
        ids = {r.get("id") for r in have if isinstance(r, dict)}
        for p in new:
            if p.get("id") not in ids:  # content-addressed id: existing rows keep status/history
                have.append(p)
                ids.add(p.get("id"))
        _validate_extensions({"revision_proposals": have})
        on_disk["revision_proposals"] = have
    newv = [p for p in result.get("viz_proposals") or [] if isinstance(p, dict)]
    if newv:
        havev = list(on_disk.get("viz_proposals") or [])
        idsv = {r.get("id") for r in havev if isinstance(r, dict)}
        for p in newv:
            if p.get("id") not in idsv:  # content-addressed: existing rows keep status/history
                havev.append(p)
                idsv.add(p.get("id"))
        _validate_extensions({"viz_proposals": havev})
        on_disk["viz_proposals"] = havev
    if studies is not None and viz_state:
        _close_built_viz_proposals(on_disk, viz_state, studies)
    newg = [p for p in result.get("capability_gaps") or [] if isinstance(p, dict)]
    if newg:
        haveg = list(on_disk.get("capability_gaps") or [])
        idsg = {r.get("id") for r in haveg if isinstance(r, dict)}
        for p in newg:
            if p.get("id") not in idsg:  # content-addressed: existing rows keep status/history
                haveg.append(p)
                idsg.add(p.get("id"))
        _validate_extensions({"capability_gaps": haveg})
        on_disk["capability_gaps"] = haveg
    if studies is not None and on_disk.get("capability_gaps"):
        from .capability_gaps import close_built_capability_gaps
        from .model_architect import build_catalog
        close_built_capability_gaps(on_disk["capability_gaps"],
                                    catalog if catalog is not None else build_catalog(studies), studies)
    on_disk["cycle_state"] = build_cycle_state(result, inv_spec, model_versions=mvs, studies=studies,
                                               evidence_state=evidence_state)
    if studies is not None:
        _evaluate_applied_proposals(on_disk, studies,
                                    (on_disk["cycle_state"].get("evaluation_snapshot") or {}).get("fingerprint"))
    if studies is not None:  # per-tab freshness against the post-renew on-disk spec
        on_disk["cycle_state"]["freshness"] = _tab_freshness(on_disk, studies)
    _validate_extensions({"cycle_state": on_disk["cycle_state"]})
    save_yaml_atomic(path, on_disk)
    return on_disk["cycle_state"]


def _evaluate_applied_proposals(on_disk: dict, studies: dict, fingerprint: str | None) -> list[str]:
    """Close the feedback loop (decision §3.4/§4): move an ``applied`` proposal to ``evaluated``
    ONLY once the discriminating disposition is DECIDED. Requires the child to have a code-graded
    run (``computed_outcomes``), then:

    * ``resolved`` -- the source disagreement is no longer live (adjudicated / settled);
    * ``persists`` -- the child's measured value (``evidence.observed``) lies OUTSIDE the
      proposal's discriminating band, i.e. the new arm genuinely REPRODUCED the gap;
    * otherwise (graded but the gap is not yet adjudicated and the result does not reproduce it)
      the proposal stays ``applied`` and is re-evaluated on a later cycle -- a revision that
      later succeeds is never terminally marked failed.

    Writes ``evaluated: {cycle_fingerprint, outcome}``, ``status: evaluated`` and a history
    event. Mutates ``on_disk``; returns evaluated ids."""
    from . import evaluation as _ev
    done: list[str] = []
    live = None
    for p in on_disk.get("revision_proposals") or []:
        if not isinstance(p, dict) or p.get("status") != "applied":
            continue
        child = next((e.get("child_study") for e in reversed(p.get("history") or [])
                      if isinstance(e, dict) and e.get("event") == "applied"), None)
        cspec = studies.get(child) if child else None
        runs = (cspec or {}).get("runs")
        if not any(isinstance(r, dict) and r.get("computed_outcomes") for r in runs or []):
            continue  # the discriminating run has not happened / been graded yet
        if live is None:
            live = [(r.get("study"), r.get("observable"))
                    for r in _ev.model_disagreement_map(on_disk, studies) if r.get("disagreement")]
        src = p.get("source") or {}
        if (src.get("study"), src.get("observable")) not in live:
            outcome = "resolved"
        elif _reproduces_gap(p, cspec):
            outcome = "persists"
        else:
            continue  # undecided: awaiting adjudication
        p["evaluated"] = {"cycle_fingerprint": fingerprint, "outcome": outcome}
        p["status"] = "evaluated"
        hist = p.get("history") if isinstance(p.get("history"), list) else []
        hist.append({"at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
                     "event": "evaluated", "by": "engine", "outcome": outcome})
        p["history"] = hist
        done.append(p.get("id"))
    return done


def _reproduces_gap(prop: dict, child: dict | None) -> bool:
    """True iff the child's discriminating finding has a numeric ``observed`` that falls outside
    the proposal's ``discriminating_test.pass_if`` ``{low, high}`` band (the gap is reproduced).
    False when anything is missing -- undecided is never read as failure."""
    band = (prop.get("discriminating_test") or {}).get("pass_if")
    if not isinstance(band, dict):
        return False
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
    lo, hi = band.get("low"), band.get("high")
    if not (num(lo) or num(hi)):
        return False
    for f in (child or {}).get("findings") or []:
        ev_ = f.get("evidence") if isinstance(f, dict) else None
        if isinstance(ev_, dict) and ev_.get("from_test") and num(ev_.get("observed")):
            o = ev_["observed"]
            return (num(lo) and o < lo) or (num(hi) and o > hi)
    return False


def project_model_versions(studies: dict, existing: list | None = None) -> list[dict]:
    """Regenerable ``model_versions[]``: a projection of the bound arms. ``bound_to`` and
    ``resolves`` are recomputed; ``origin`` is the one non-derivable field, kept from disk once written."""
    from .model_architect import bind_model_versions, resolve_composite
    prior = {m.get("id"): m for m in (existing or []) if isinstance(m, dict)}
    out: dict[str, dict] = {}
    for slug, arms in bind_model_versions(studies).items():
        for arm, mv in arms.items():
            row = out.get(mv["id"])
            if row is None:
                old = prior.get(mv["id"]) or {}
                row = out[mv["id"]] = {
                    "id": mv["id"], "composite": mv["composite"], "params": mv["params"],
                    "parent_ids": list(mv["parent_ids"]),  # projection: always recomputed
                    "origin": old.get("origin") or mv["origin"],
                    "bound_to": [],
                    "resolves": bool(resolve_composite(mv["composite"], None)["resolves"]),
                }
            row["bound_to"].append({"study": slug, "arm": arm})
    return list(out.values())


def _validate_extensions(block: dict) -> None:
    """Validate the new investigation blocks against the bundled JSON Schema (write boundary only)."""
    import json
    import jsonschema
    schema = json.loads((Path(__file__).parent / "schemas" / "investigation-extensions.schema.json")
                        .read_text(encoding="utf-8"))
    jsonschema.validate(json.loads(json.dumps(block, default=str)), schema)


def _renew_executive_decisions(on_disk: dict) -> None:
    """Reconcile the authored ``executive.decisions_needed`` with what the cycle has
    resolved, in place. A decision whose target appears in ``resolutions`` is no longer
    open — move it to ``executive.decisions_resolved`` (dedup by question) and drop it from
    ``decisions_needed`` — so the authored narrative can't drift out of step with the
    derived state (the drift the ``structural.investigation.hygiene`` guard flags). Leaves
    the still-open decisions untouched; authored prose elsewhere is not rewritten."""
    ex = on_disk.get("executive")
    if not isinstance(ex, dict):
        return
    decisions = ex.get("decisions_needed") or []
    if not decisions:
        return
    resolved = {r.get("target") for r in (on_disk.get("resolutions") or []) if isinstance(r, dict)}

    def _q(d):
        return d.get("question") if isinstance(d, dict) else d

    still_open = [d for d in decisions if _q(d) not in resolved]
    newly_resolved = [d for d in decisions if _q(d) in resolved]
    if not newly_resolved:
        return
    archived = list(ex.get("decisions_resolved") or [])
    seen = {_q(d) for d in archived}
    for d in newly_resolved:
        if _q(d) not in seen:
            archived.append(d)
            seen.add(_q(d))
    ex["decisions_needed"] = still_open
    ex["decisions_resolved"] = archived


def _parent_in_parens(text: str, members: set) -> str | None:
    """The member study a decision names as its *subject*, e.g. 'Agent motion model
    (og-ecology follow-up): …' → 'og-ecology'. Only the parenthetical before the first
    colon counts (the subject position): a project-scope decision that merely mentions a
    study mid-sentence ('Publication scope: … (og-vent-environment -> …)') names no
    subject study and yields None, so it is not turned into a study suggestion."""
    subject = (text or "").split(":", 1)[0]
    for group in re.findall(r"\(([^)]+)\)", subject):
        token = group.strip().split()[0].rstrip(":,.").strip() if group.strip() else ""
        if token in members:
            return token
    return None


def suggest_studies(cycle_result: dict, inv_spec: dict, studies: dict) -> list[dict]:
    """Suggestions for new studies the cycle thinks should run next — the unified,
    expert-in-the-loop 'what to build' view (RFC-0005 branch op, made first-class).

    Each suggestion is ``{id, title, motivation, parent_study, source,
    awaiting_expert, status}``. It gathers the three sources that were scattered
    before: authored ``followup_proposals`` on member studies, the narrative-gap
    ``followup``s the audit derives, and each ``decisions_needed`` entry that names a
    member study in parentheses — the last are **gated on expert input** (``status:
    awaiting_expert``) so a new study is proposed but never seeded until the expert
    answers. Pure/read-only: it proposes, it does not create or run anything."""
    members = set(inv_spec.get("studies") or list(studies))
    out: list[dict] = []
    seen: set[str] = set()

    def add(sid, title, motivation, parent, source, awaiting):
        if not sid or sid in seen:
            return
        seen.add(sid)
        out.append({"id": sid, "title": title or sid, "motivation": motivation or "",
                    "parent_study": parent, "source": source,
                    "awaiting_expert": awaiting,
                    "status": "awaiting_expert" if awaiting else "ready"})

    # 1. authored followup proposals (an expert/agent already proposed the study)
    for slug, spec in (studies or {}).items():
        if not isinstance(spec, dict):
            continue
        for fp in spec.get("followup_proposals") or []:
            if not isinstance(fp, dict) or fp.get("status") in ("seeded", "done", "rejected"):
                continue
            add(fp.get("id"), fp.get("title"), fp.get("motivation"), slug, "followup_proposal", [])

    # 2. narrative-gap followups (the audit's auto-generated bridging studies)
    for g in cycle_result.get("audit") or []:
        fu = (g.get("followup") or {}) if isinstance(g, dict) else {}
        if fu.get("id"):
            add(fu["id"], fu.get("title"), fu.get("motivation"), fu.get("parent_study"),
                "narrative_gap", [])

    # 3. decisions_needed that name a member study → a study gated on that expert input
    ex = inv_spec.get("executive") or {}
    for d in ex.get("decisions_needed") or []:
        q = d.get("question") if isinstance(d, dict) else (d if isinstance(d, str) else None)
        if not q:
            continue
        parent = _parent_in_parens(q, members)
        if not parent:
            continue  # project-scope decision (publication/rule-set scope), not a study
        sid = f"seed-{parent}-{hashlib.sha256(q.encode()).hexdigest()[:6]}"
        add(sid, f"Follow-up to {parent} (pending an expert decision)", q, parent,
            "expert_decision", [q])

    # 4. frontier DIVERGENCES and unanswered OPEN QUESTIONS → a proposed discriminating
    #    study. This is the "build a new model when an open question stands" loop made
    #    first-class: a divergence is a verdict conflict a better-controlled run could
    #    settle; an open_question names a behavior no member study yet measures. Both are
    #    proposed gated on the expert (status: awaiting_expert) — surfaced to seed, never
    #    auto-created — so the frontier itself, not only authored follow-ups, drives what to
    #    build next.
    for oc in cycle_result.get("open_calls") or []:
        if not isinstance(oc, dict) or oc.get("kind") not in ("divergence", "open_question"):
            continue
        title = (oc.get("title") or "").strip()
        tgt = str(oc.get("target") or oc.get("claim") or oc.get("question") or "")
        key = title or tgt
        if not key:
            continue
        parent = next((m for m in members if m and m in tgt), None)
        sid = f"discriminate-{hashlib.sha256(key.encode()).hexdigest()[:8]}"
        if oc.get("kind") == "divergence":
            t = f"Discriminating study — resolve divergence: {title[:70]}"
            m = ("A verdict divergence stands on the current evidence. Propose a controlled "
                 "run designed to discriminate the competing claims and settle it empirically.")
        else:
            t = f"New study — address open question: {title[:70]}"
            m = ("An open question no member study yet answers. Propose a study that measures "
                 "the behavior it asks about.")
        add(sid, t, m, parent, f"frontier_{oc.get('kind')}", [key])

    # 5. GENERATIVE loop: a REFUTED finding (finding status in {contradicts, refuted})
    #    spawns a builds-on FOLLOW-UP study — pre-populated from the finding via
    #    build_child_seed_from_finding (question/mechanism/expected_outcome, seeded_from,
    #    carried behavior_tests) — plus, once any refutation exists, a single SYNTHESIS
    #    "frontier" study that folds the refutations into the next discriminating question.
    #    A refutation is a concrete signal, so these are proposed `ready` (not expert-gated);
    #    select_for_launch then applies the autonomy policy. This makes "a negative result
    #    generates the next study" a first-class part of the cycle.
    import dataclasses as _dc

    from .seed_from_followup import build_child_seed_from_finding
    refutations: list[tuple[str, str]] = []
    for slug, spec in (studies or {}).items():
        if not isinstance(spec, dict):
            continue
        for f in spec.get("findings") or []:
            if not isinstance(f, dict):
                continue
            verdict = str(f.get("status") or f.get("verdict") or "").strip().lower()
            if verdict not in ("contradicts", "refuted"):
                continue
            fid = f.get("id")
            if not fid:
                continue
            refutations.append((slug, fid))
            sid = f"followup-{slug}-{hashlib.sha256(str(fid).encode()).hexdigest()[:8]}"
            seed = None
            try:
                seed = _dc.asdict(build_child_seed_from_finding(spec, slug, sid, fid))
            except Exception:  # noqa: BLE001 — a malformed finding never breaks the proposal view
                seed = None
            motivation = str(f.get("next_action") or f.get("statement")
                             or "A finding was refuted; investigate why and what holds instead.")[:300]
            before = len(out)
            add(sid, f"Follow-up — investigate a refuted finding in {slug}", motivation,
                slug, "refuted_finding", [])
            if seed is not None and len(out) > before:
                out[-1]["seed"] = seed
    if refutations:
        n_studies = len({s for s, _ in refutations})
        key = ",".join(sorted(f"{s}:{fid}" for s, fid in refutations))
        sid = f"synthesize-frontier-{hashlib.sha256(key.encode()).hexdigest()[:8]}"
        add(sid, "Synthesis — fold the refuted findings into the next frontier",
            f"{len(refutations)} refuted finding(s) across {n_studies} study(ies): synthesize what "
            "they rule out and pose the next discriminating question.",
            None, "synthesis_frontier", [])

    return out


def _default_runner(command: str, ws_root: Path | str, *,
                    worktree: Path | str | None = None, timeout: int = 3600) -> dict:
    """Run a consequential executor command as a subprocess in the workspace, capturing its
    outcome. This is the real sim/seed invocation the autonomous bridge makes.

    The RUNNING interpreter's venv bin is prepended to the subprocess PATH so a bare console script
    in the command (e.g. ``vivarium-workbench``) resolves to THIS venv's binary — not some other venv
    earlier on PATH, which would run against a different workspace package and fail to find the
    composite (observed: a bare ``vivarium-workbench`` resolved to a sibling venv and reported the
    study's composite 'not found').

    ``worktree`` (G5): when given, the worktree is additionally prepended to ``PYTHONPATH`` so a
    FRESHLY-BUILT composite living in that worktree resolves OVER the canonical editable install —
    letting the cycle run a just-built composite without a workspace-wide reinstall. ``worktree=None``
    is today's behavior exactly. The workbench injects its own worktree-aware runner at the cycle
    call sites; this is the no-injection fallback, conforming to the same signature."""
    import os
    import subprocess
    import sys
    env = dict(os.environ)
    env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")
    if worktree is not None:
        env["PYTHONPATH"] = str(worktree) + os.pathsep + env.get("PYTHONPATH", "")
    proc = subprocess.run(command, shell=True, cwd=str(ws_root), env=env,
                          capture_output=True, text=True, timeout=timeout)
    return {"returncode": proc.returncode,
            "ok": proc.returncode == 0,
            "run_id": _latest_run_id_for_command(command, ws_root) if proc.returncode == 0 else None,
            "tail": (proc.stdout or proc.stderr or "")[-400:]}


def _latest_run_id_for_command(command: str, ws_root: Path | str) -> str | None:
    """Best-effort run_id for the RunResult: parse the study slug from the run command and read the
    newest row from its ``runs.db`` via the run registry. ``None`` when not resolvable — the
    workbench's injected runner is where run_id is authoritative; the fallback just reports what it
    can without parsing stdout."""
    import re as _re
    m = _re.search(r"run[ -]study\s+([A-Za-z0-9][\w-]*)", command)
    if not m:
        return None
    slug = m.group(1)
    for base in ("studies", ""):
        db = Path(ws_root) / base / slug / "runs.db" if base else Path(ws_root) / slug / "runs.db"
        if db.exists():
            try:
                from .run_registry import list_runs
                rows = list_runs(db) or []
                if rows:
                    last = rows[-1]
                    return str(last.get("run_id") or last.get("name") or "") or None
            except Exception:  # noqa: BLE001
                return None
    return None


def _env_decider(target: str, detail: str) -> dict:
    """An AI decider that drives an open decision by shelling out to the agent command in
    ``$VIVA_DECIDER_CMD``. The command receives the decision as ``{"target","detail"}`` JSON
    on stdin and must print ``{"choice","rationale"}`` JSON on stdout (the option that
    maximizes discovery, and why). The engine stays LLM-free; the AI lives in that command
    (e.g. ``claude -p`` with the decide skill). Raises if the var is unset so --ai-decide
    fails loud rather than silently deferring."""
    import json
    import os
    import subprocess
    cmd = os.environ.get("VIVA_DECIDER_CMD")
    if not cmd:
        raise RuntimeError("--ai-decide needs $VIVA_DECIDER_CMD set to an AI agent command "
                           "that reads {target,detail} JSON and prints {choice,rationale} JSON")
    proc = subprocess.run(cmd, shell=True, input=json.dumps({"target": target, "detail": detail}),
                          capture_output=True, text=True, timeout=1800)
    return json.loads(proc.stdout)


def execute_action(ws_root: Path | str, inv_slug: str, *, autonomous: bool = False,
                   runner=None, decider=None, target: str | None = None,
                   action: str | None = None, render_hook=None, build_hook=None,
                   worktree: Path | str | None = None) -> dict:
    """The bridge's execution step: take the top value-ranked action and carry it out,
    gated by consequence.

    - A side-effect-free action (``auto_safe``, e.g. re-snapshot) always runs.
    - A consequential action (``simulation`` / ``simulation+study`` — run a study, seed
      one) runs **only** with ``autonomous=True``, by invoking its real executor command
      as a subprocess (``runner``, injectable for tests); the outcome is recorded.
    - A ``steer`` (an open expert decision) is normally ``human-only``, BUT when an AI
      ``decider`` is supplied and ``autonomous=True`` the AI drives it: ``decider`` is
      called with the decision and returns the option that maximizes discovery plus a
      rationale; the choice is recorded as a resolution (``[ai-driven]``) so the decision
      is made and its gated study becomes ready. The engine itself never reasons — the
      ``decider`` is the AI, injected by the caller (a skill / agent / this session).
    - A ``review`` (verdict overwrite) or a ``human-only`` action with no ``decider``
      never auto-runs — it is handed back.

    Executed actions are appended to the investigation's ``executed_actions`` log and the
    frontier is re-derived so the caller sees the change. Returns a record of what
    happened."""
    from . import discovery_engine as de
    inv_path, inv_spec, studies = load_cycle_inputs(ws_root, inv_slug)
    cyc = _cycle_with_evidence(ws_root, inv_slug, inv_spec, studies)
    before = de.closure(cyc, inv_spec)["open_items"]
    plan = de.execute_plan(cyc, inv_spec)
    if not plan:
        return {"status": "nothing-to-do", "frontier": before}
    # Default: the single highest-value move (plan[0]). With `target`, drive that specific
    # frontier item instead — e.g. an AI decider driving a decision that a higher-value
    # verification run would otherwise rank ahead of.
    top = plan[0] if target is None else next((p for p in plan if p["source"] == target
                                  and (action is None or p["action_type"] == action)), None)
    if top is None:
        return {"status": "no-such-action", "target": target, "frontier": before}
    rec = {"action": top["action_type"], "source": top["source"], "command": top["command"],
           "consequence": top["consequence"], "score": top["score"]}

    if top["auto_safe"]:
        snap = snapshot_validations(ws_root, inv_slug)
        rec["status"] = "executed"
        rec["outcome"] = {"ok": True, "detail": f"snapshot-validations: {len(snap)} records accepted"}
    elif top["consequence"] in ("simulation", "simulation+study"):
        if not autonomous:
            rec["status"] = "gated"
            rec["outcome"] = {"ok": None, "detail": "consequential — re-run with --autonomous to execute"}
        else:
            # Run-readiness: refuse to deploy a sim that can't produce an evaluable result
            # (the run-spec contract). A run whose emitter/observables don't satisfy the
            # study's behavior-test measures yields an empty, unverifiable readout, so the
            # result never flows back into the cycle. Gate rather than waste the run.
            slug = top["source"].split(":", 1)[1] if str(top["source"]).startswith("claim:") else top["source"]
            study_spec = studies.get(slug)
            ready = run_readiness(study_spec, ws_root) if study_spec else {"ready": True, "gaps": []}
            if not ready["ready"]:
                rec["status"] = "gated"
                rec["outcome"] = {"ok": None, "detail": f"run not ready — {'; '.join(ready['gaps'])}",
                                  "readiness": ready}
            else:
                # Fill the <ws> placeholder with the real workspace root, then refuse to
                # shell out a still-templated command (e.g. a branch op's
                # <parent>/<proposal>) rather than running a broken literal placeholder.
                command = (top["command"] or "").replace("<ws>", str(ws_root))
                rec["command"] = command
                if re.search(r"<[^>]+>", command):
                    rec["status"] = "gated"
                    rec["outcome"] = {"ok": None, "detail": f"command needs manual values, run it yourself: {command}"}
                else:
                    rec["outcome"] = (runner or _default_runner)(command, ws_root, worktree=worktree)
                    rec["status"] = "executed" if rec["outcome"].get("ok") else "failed"
    elif top["consequence"] == "render":
        # A figure (re)render. Autonomous-only; refused unless the declared entry is
        # RESOLVABLE (the readiness analog: never a broken_stub / static figure with no
        # recipe). The executor is the injected `render_hook` (in-process) else the
        # templated `render-viz` subprocess via `runner`. Never fabricates: a render that
        # leaves no file is `failed`, and a failed render leaves any existing figure alone.
        if not autonomous:
            rec["status"] = "gated"
            rec["outcome"] = {"ok": None, "detail": "consequential — re-run with --autonomous to execute"}
        else:
            vt = _viz_target(top["source"])
            entry = _viz_entry(studies, *vt) if vt else None
            if entry is None or not _viz_resolvable(entry):
                rec["status"] = "gated"
                rec["outcome"] = {"ok": None, "detail": "figure is not renderable (no resolvable "
                                  "address/render) — declare one: discover viz <inv> "
                                  f"{top['source']} --declare address=… --rationale '…'"}
            else:
                from .viz_freshness import normalize_entry
                vstudy, vname = vt
                sdir = _study_dir_on_disk(ws_root, inv_slug, vstudy)
                latest = viz_inventory(ws_root, inv_slug, {vstudy: studies[vstudy]})[vstudy]["_latest"]
                command = render_command(vstudy, vname, str(ws_root))
                rec["command"] = command
                _out_rel = normalize_entry(entry)["output"]
                chart = (sdir / _out_rel) if (sdir and _out_rel) else None

                def _snap():
                    if chart is None or not chart.is_file():
                        return None
                    return (chart.stat().st_mtime_ns, chart.read_bytes())
                import time as _time
                before_snap, t0 = _snap(), _time.time()
                try:
                    if render_hook is not None:
                        out = render_hook(sdir, entry, latest) or {}
                    else:
                        out = (runner or _default_runner)(command, ws_root, worktree=worktree) or {}
                except Exception as e:  # noqa: BLE001 -- a crashing renderer is a failed render
                    out = {"ok": False, "detail": f"render raised {type(e).__name__}: {e}"}
                if out.get("ok"):
                    # Never launder a stale figure as fresh: `ok` counts only if the figure
                    # actually CHANGED, or the hook stamped valid meta for the latest run
                    # during this render. Otherwise the render produced nothing -> failed.
                    from .viz_freshness import read_meta
                    after = _snap()
                    changed = after is not None and after != before_snap
                    m = read_meta(chart) if after is not None else None
                    _want = _pinned_run(entry, latest)
                    stamped = bool(after is not None and m and _want
                                   and out.get("source_run_id") == _want
                                   and m.get("source_run_id") == _want
                                   and (m.get("rendered_at") or 0) >= t0)
                    if not (changed or stamped):
                        out = {**out, "ok": False,
                               "detail": f"render reported ok but produced no new figure at {chart}"}
                rec["outcome"] = out
                rec["status"] = "executed" if out.get("ok") else "failed"
                if rec["status"] == "executed":
                    _post_render_stamp(sdir, entry, latest, command)
    elif top["action_type"] == "steer" and decider is not None and autonomous:
        # AI drives the expert decision: pick the option that maximizes discovery.
        detail = next((i["rationale"] for i in de.frontier(cyc, inv_spec)
                       if i["kind"] == "open_decision" and i["target"] == top["source"]), top["source"])
        choice = decider(top["source"], detail) or {}
        pick, why = choice.get("choice") or "(unspecified)", choice.get("rationale") or ""
        record_resolution(inv_path, "open_decision", top["source"],
                          f"[ai-driven · maximize-discovery] chose {pick}. {why}", by="discovery-ai")
        rec["status"] = "executed"
        rec["outcome"] = {"ok": True, "detail": f"ai-decided: {pick}", "rationale": why}
    elif top["action_type"] == "revise_model" and decider is not None and autonomous \
            and str(top["source"]).startswith("rp:"):
        # AI decider approves a model-revision proposal (history is the only mutation).
        detail = next((i["rationale"] for i in de.frontier(cyc, inv_spec)
                       if i["kind"] == "revision_proposed" and i["target"] == top["source"]), top["source"])
        choice = decider(top["source"], detail)
        choice = choice if isinstance(choice, dict) else {}
        verb = {"approve": "approved", "reject": "rejected"}.get(str(choice.get("choice") or "").strip().lower())
        why = (choice.get("rationale") or "").strip()
        disk = next((p for p in load_yaml_mapping(inv_path).get("revision_proposals") or []
                     if isinstance(p, dict) and p.get("id") == top["source"]), None)
        if disk is not None and disk.get("status", "proposed") != "proposed":
            rec["status"] = "gated-human"
            rec["outcome"] = {"ok": None, "detail": f"already {disk.get('status')} — not re-decided"}
        elif verb is None or not why:
            rec["status"] = "gated-human"
            rec["outcome"] = {"ok": None, "detail": "decider gave no usable approve/reject choice "
                              "with a rationale — needs a human decision"}
        else:
            review_revision(inv_path, top["source"], verb, f"[ai-driven] {why}", by="discovery-ai")
            rec["status"] = "executed"
            rec["outcome"] = {"ok": True, "detail": f"ai-{verb}: {top['source']}", "rationale": why}
    elif top["action_type"] == "build_viz" and str(top["source"]).startswith("vp:"):
        # A viz proposal is REVIEW tier: accept is author-gated. An AI decider may act only on
        # a DERIVABLE proposal (catalog address present); a needs_author one, or any decision
        # without a rationale, is handed back. advance never reaches here (not in its loop).
        disk = next((p for p in load_yaml_mapping(inv_path).get("viz_proposals") or []
                     if isinstance(p, dict) and p.get("id") == top["source"]), None)
        sug = (disk or {}).get("suggested") or {}
        if decider is None or not autonomous:
            rec["status"] = "gated-human"
            rec["outcome"] = {"ok": None, "detail": "review — needs a human accept/reject"}
        elif disk is None or disk.get("status", "proposed") != "proposed":
            rec["status"] = "gated-human"
            rec["outcome"] = {"ok": None, "detail": "proposal not persisted/open — run `discover cycle` first"}
        elif sug.get("needs_author") or not sug.get("address"):
            rec["status"] = "gated-human"
            rec["outcome"] = {"ok": None, "detail": "needs_author — a human must supply the address"}
        else:
            choice = decider(top["source"], top["source"])
            choice = choice if isinstance(choice, dict) else {}
            verb = str(choice.get("choice") or "").strip().lower()
            why = (choice.get("rationale") or "").strip()
            if verb not in ("approve", "accept", "reject") or not why:
                rec["status"] = "gated-human"
                rec["outcome"] = {"ok": None, "detail": "decider gave no usable choice with a rationale"}
            else:
                if verb == "reject":
                    reject_viz_proposal(ws_root, inv_slug, top["source"], rationale=f"[ai-driven] {why}",
                                        by="discovery-ai")
                else:
                    accept_viz_proposal(ws_root, inv_slug, top["source"], address=sug["address"],
                                        rationale=f"[ai-driven] {why}", by="discovery-ai")
                rec["status"] = "executed"
                rec["outcome"] = {"ok": True, "detail": f"ai-{verb}: {top['source']}", "rationale": why}
    elif top["action_type"] == "build_process" and str(top["source"]).startswith("cg:"):
        # A capability gap is REVIEW tier and NEVER auto-built. An AI decider may accept/reject a
        # DERIVABLE `proposed` gap only with a rationale; an `accepted` gap (authorized, with a
        # rationale on record) is built via the injected/workspace `build_hook`. needs_author, no
        # rationale, no decider or no hook -> gated-human. advance never reaches here.
        disk = next((g for g in load_yaml_mapping(inv_path).get("capability_gaps") or []
                     if isinstance(g, dict) and g.get("id") == top["source"]), None)
        status = (disk or {}).get("status", "proposed")
        if disk is None or status not in ("proposed", "accepted"):
            rec["status"] = "gated-human"
            rec["outcome"] = {"ok": None, "detail": "gap not persisted/open — run `discover cycle` first"}
        elif not autonomous:
            rec["status"] = "gated-human"
            rec["outcome"] = {"ok": None, "detail": "review — needs a human accept/reject (or --autonomous)"}
        elif status == "accepted":
            hook = build_hook if build_hook is not None else load_workspace_build_hook(ws_root)
            if hook is None or not (disk.get("accept") or {}).get("rationale"):
                rec["status"] = "gated-human"
                rec["outcome"] = {"ok": None, "detail": "accepted but no build_hook (or no accept rationale) — "
                                  "register <pkg>.build_hook.register_build_hook"}
            else:
                from .capability_gaps import build_capability_gap
                from .model_architect import build_catalog
                inv_path_, spec_, gap_ = _cap_gap_load(ws_root, inv_slug, top["source"])
                build_capability_gap(gap_, hook, ws_root=ws_root,
                                     catalog=lambda: build_catalog(load_cycle_inputs(ws_root, inv_slug)[2], ws_root))
                # Re-read: the hook may have edited the investigation (e.g. registered the new study).
                inv_path_, spec_, fresh_ = _cap_gap_load(ws_root, inv_slug, top["source"])
                fresh_.clear()
                fresh_.update(gap_)
                save_yaml_atomic(inv_path_, spec_)
                rec["status"] = "executed" if gap_["status"] == "built" else "failed"
                rec["outcome"] = {"ok": gap_["status"] == "built", "detail": f"{gap_['status']}: {top['source']}",
                                  "built": gap_.get("built")}
        elif decider is None:
            rec["status"] = "gated-human"
            rec["outcome"] = {"ok": None, "detail": "review — needs a human accept/reject"}
        elif disk.get("suggested", {}).get("needs_author") or not disk.get("derivable"):
            rec["status"] = "gated-human"
            rec["outcome"] = {"ok": None, "detail": "needs_author — a human must accept (no derivable build path)"}
        else:
            choice = decider(top["source"], top["source"])
            choice = choice if isinstance(choice, dict) else {}
            verb = str(choice.get("choice") or "").strip().lower()
            why = (choice.get("rationale") or "").strip()
            if verb not in ("approve", "accept", "reject") or not why:
                rec["status"] = "gated-human"
                rec["outcome"] = {"ok": None, "detail": "decider gave no usable choice with a rationale"}
            else:
                fn = reject_capability_gap if verb == "reject" else accept_capability_gap
                fn(ws_root, inv_slug, top["source"], rationale=f"[ai-driven] {why}", by="discovery-ai")
                rec["status"] = "executed"
                rec["outcome"] = {"ok": True, "detail": f"ai-{verb}: {top['source']}", "rationale": why}
    else:  # review / human-only with no decider
        rec["status"] = "gated-human"
        rec["outcome"] = {"ok": None, "detail": f"{top['consequence']} — needs a human decision"}

    if rec["status"] == "executed":
        _append_executed(inv_path, rec)
        _, inv2, studies2 = load_cycle_inputs(ws_root, inv_slug)
        rec["frontier_before"] = before
        rec["frontier_after"] = de.closure(_cycle_with_evidence(ws_root, inv_slug, inv2, studies2), inv2)["open_items"]
    return rec


def _append_executed(inv_path: Path | str, record: dict) -> None:
    """Append an execution record to the investigation's ``executed_actions`` log (an
    audit trail of what the bridge ran). Re-reads on disk, writes only this field."""
    path = Path(inv_path)
    on_disk = load_yaml_mapping(path) if path.exists() else {}
    log = list(on_disk.get("executed_actions") or [])
    log.append({"action": record["action"], "source": record["source"],
                "command": record["command"], "status": record["status"],
                "outcome": (record.get("outcome") or {}).get("detail")
                or ("ok" if (record.get("outcome") or {}).get("ok") else "see runner output"),
                "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")})
    on_disk["executed_actions"] = log
    save_yaml_atomic(path, on_disk)


def run_readiness(study_spec: dict, ws_root: Path | str | None = None) -> dict:
    """Is a study's run fully specified to yield an evaluable, visualized, contract-checked
    result that flows back into the cycle? A simulation deployed with an incomplete spec
    produces an empty / unverifiable run (the readout the gate needs is never emitted), so
    the result never reaches the models, findings, or workbench.

    Checks the fields a deployed run needs:

    - **observables** --- the run must emit the observables every behavior-test measure
      needs (a derived-scalar measure resolved to its SOURCE observables via
      :func:`study_evaluator.required_observables_for_measure`), matched against the study's
      declared ``observables`` (by full path or leaf name).
    - **emitter** --- an emitter/store is declared.
    - **visualizations**, **behavior tests**, **contracts** --- declared.

    Returns ``{ready, complete, gaps, observables{required, declared, missing}, has_*}``.
    ``ready`` means the run can produce an *evaluable* result (emitter + observables);
    ``complete`` additionally requires viz + tests + contracts (the full quality bar)."""
    from .study_evaluator import load_workspace_derived_scalars, required_observables_for_measure
    tests = study_spec.get("behavior_tests") or study_spec.get("tests") or []
    required: set = set()
    # The derived-scalar registry THIS environment can see. A non-empty registry means the
    # workspace evaluator package loaded and defines computers — so a derived measure whose path is
    # absent from it will SKIP at grade time (the env that runs readiness is the env that grades).
    # An empty registry means no package / no evaluators here: can't assess, so don't false-flag.
    _registry = load_workspace_derived_scalars(ws_root) if ws_root is not None else {}
    _registry_trustworthy = bool(_registry)
    # A behavior test whose measure declares a KIND that needs a readout but no `path`
    # cannot be graded — the evaluator records SKIP (agent bucket), so the gate never
    # resolves to PASS/FAIL even after a run. Surface those so the "build" step (author the
    # measure's path / its derived-scalar computer) is explicit, not a silent SKIP.
    unevaluable: list[str] = []
    # Derived measures whose computer is NOT registered in this env → they will grade SKIP even
    # after a run (observed: several tracked runs graded SKIP because the run
    # env-worker imported a stale venv without the merged `register_derived_scalars` evaluators).
    unresolved_measures: list[str] = []
    for t in tests:
        if not isinstance(t, dict):
            continue
        m = t.get("measure")
        if not isinstance(m, dict):
            m = {}  # a bare-string measure (a composite-observable function) needs no declared
                    # readout — it is computed from the raw trajectory; treat as readiness-neutral.
        kind, path = m.get("kind"), m.get("path")
        if kind and kind not in ("config_value", "cross_run", "run_delta") and not path:
            unevaluable.append(t.get("name") or "<unnamed>")
        if kind in ("derived_scalar", "derived") and path and _registry_trustworthy \
                and _registry.get(path) is None:
            unresolved_measures.append(t.get("name") or path)
        required |= required_observables_for_measure(m, ws_root)
    declared = [str(o) for o in (study_spec.get("observables") or [])]
    declared_leaves = {o.split("/")[-1] for o in declared}
    missing = sorted(o for o in required
                     if o not in declared and o.split("/")[-1] not in declared_leaves)
    # A study that has already RUN and been assessed demonstrably emitted the observables its
    # gates need (they are in the run store) — even when its declared `observables:` list is
    # empty, as it routinely is. Readiness must then distinguish "already produced the
    # readouts" from "never run and can't"; a static-only `missing` check otherwise reports
    # every already-run study as un-runnable (observed on a real workspace: all 21 run studies).
    runs = study_spec.get("runs") or []
    assessed = bool(study_spec.get("verdict") or study_spec.get("conclusion_verdict")
                    or any(isinstance(r, dict) and r.get("outcomes") for r in runs))
    already_run = bool(runs) and assessed
    has_emitter = bool(study_spec.get("emitter")
                       or any(isinstance(r, dict) and r.get("emitter")
                              for r in (study_spec.get("runs") or [])))
    has_viz = bool(study_spec.get("visualizations"))
    has_tests = bool(tests)
    has_contracts = bool(study_spec.get("contracts") or study_spec.get("process_contracts"))

    # A declared visualization is useless until it is RENDERED to studies/<slug>/viz/*.html — the
    # workbench's study-charts endpoint AND the study report both glob that directory, so a
    # declared-but-unrendered viz shows "No figures yet" (a silent gap; observed on a real workspace,
    # where all 5 studies declared a viz but rendered none). Verify the figure exists on disk.
    viz_rendered = has_viz
    if has_viz and ws_root is not None:
        _slug = study_spec.get("name")
        if _slug:
            try:
                _vdir = studies_dir(Path(ws_root)) / _slug / "viz"
                viz_rendered = _vdir.is_dir() and any(_vdir.glob("*.html"))
            except Exception:  # noqa: BLE001 — a path problem is not a render verdict
                viz_rendered = has_viz

    # Does the baseline composite (the MODEL) actually resolve? A study whose composite does not
    # exist cannot run, yet observable/emitter/etc. checks would otherwise call it ready (observed
    # on a real workspace: all 5 baseline composites were ModuleNotFoundError while readiness said
    # ready). Resolve WITHOUT executing composite code (find_spec), and only when the workspace
    # package is importable at all, so a missing package isn't misread as a missing model.
    from .model_architect import _top_importable, resolve_composite
    composite_missing: str | None = None
    _baseline = study_spec.get("baseline") or []
    comp = (_baseline[0].get("composite")
            if _baseline and isinstance(_baseline[0], dict) else None) or study_spec.get("composite")
    if comp and isinstance(comp, str) and "." in comp:
        if _top_importable(comp) and not resolve_composite(comp, ws_root)["resolves"]:
            composite_missing = comp

    gaps: list[str] = []
    if composite_missing and not already_run:
        gaps.append(f"baseline composite not built / not importable: {composite_missing}")
    if missing and not already_run:
        gaps.append(f"run must emit observables the gates need: {missing}")
    if unevaluable:
        gaps.append(f"behavior tests with no measure.path (will SKIP, gate never resolves): {unevaluable}")
    if unresolved_measures:
        gaps.append(f"derived measures with no registered evaluator in this environment (they will "
                    f"grade SKIP; the grading env-worker must import the workspace package that "
                    f"defines register_derived_scalars — editable-install/venv-sync gap): {unresolved_measures}")
    if not has_emitter:
        gaps.append("no emitter declared")
    if not has_viz:
        gaps.append("no visualizations declared")
    elif not viz_rendered:
        gaps.append("visualizations declared but not rendered (no studies/<slug>/viz/*.html — "
                    "the workbench shows 'No figures yet')")
    if not has_tests:
        gaps.append("no behavior tests declared")
    if not has_contracts:
        gaps.append("no contracts declared")

    # `ready` = the run can produce an EVALUABLE result: every gate's observables are
    # emittable. A study-level emitter is NOT required — a fresh study's composite declares
    # its own emitter and the run records it only afterward; demanding a study-level emitter
    # wrongly blocked never-run studies whose composite self-emits. Emitter/viz/contracts are
    # `complete` (the quality bar), not `ready`.
    # ready = can produce an EVALUABLE result: observables emittable AND the model exists. An
    # already-run study is ready regardless (it demonstrably did both).
    ready = already_run or (not missing and not composite_missing)
    measures_resolvable = not unresolved_measures
    complete = (ready and has_emitter and viz_rendered and has_tests and has_contracts
                and measures_resolvable)
    return {"ready": ready, "complete": complete, "gaps": gaps, "already_run": already_run,
            "composite_resolves": composite_missing is None,
            "measures_resolvable": measures_resolvable,
            "observables": {"required": sorted(required), "declared": sorted(declared),
                            "missing": missing},
            "unevaluable_tests": unevaluable, "unresolved_measures": unresolved_measures,
            "has_emitter": has_emitter, "has_visualizations": has_viz,
            "visualizations_rendered": viz_rendered,
            "has_tests": has_tests, "has_contracts": has_contracts}


def _viz_target(source) -> tuple[str, str] | None:
    """``fig:<study>/<name>`` -> ``(study, name)`` else None."""
    src = str(source)
    if not src.startswith("fig:") or "/" not in src:
        return None
    study, _, name = src[4:].partition("/")
    return (study, name) if study and name else None


def _viz_entry(studies: dict, study: str, name: str) -> dict | None:
    for e in (studies.get(study) or {}).get("visualizations") or []:
        if isinstance(e, dict) and e.get("name") == name:
            return e
    return None


def _viz_resolvable(entry: dict) -> bool:
    """A declared entry the render executor can act on: a `class` or `command` kind. Never
    a broken_stub (no identity) or a static figure with no `render:` recipe."""
    from .viz_freshness import normalize_entry
    return normalize_entry(entry)["kind"] in ("class", "command")


def _pinned_run(entry: dict, latest: dict | None) -> str | None:
    """The run a figure must be stamped against: the entry's explicit ``source_run`` pin
    (spec 2E) when present, else the study's latest run."""
    pin = (entry or {}).get("source_run")
    if pin:
        return str(pin)
    return (latest or {}).get("run_id") or None


def _post_render_stamp(sdir, entry: dict, latest: dict | None, command: str | None = None) -> bool:
    """Best-effort (the `_post_run_sync` discipline): make sure the rendered figure carries
    a ``<output>.meta.json`` stamped against the latest run, when the hook left it
    unstamped (or stamped against an older run). Never creates a figure; returns True iff
    it wrote a stamp. With no run to stamp against, writes nothing (mtime fallback)."""
    want = _pinned_run(entry, latest)
    if sdir is None or not want:
        return False
    try:
        import time
        from .viz_freshness import normalize_entry, read_meta, stamp_meta
        out = normalize_entry(entry)["output"]
        chart = Path(sdir) / out if out else None
        if chart is None or not chart.is_file():
            return False
        meta = read_meta(chart)
        if meta and meta.get("source_run_id") == want:
            return False
        stamp_meta(chart, source_run_id=want,
                   generation_id=(meta or {}).get("generation_id"),
                   rendered_at=time.time(), command=command or (entry.get("render") or ""),
                   model_version=(meta or {}).get("model_version"))
        return True
    except Exception:  # noqa: BLE001 -- stamping is best-effort, never fails an advance
        return False


# An applied revision's child runs as a `test` op (applied proposals are off the frontier);
# `revise_model` is review-consequence, never dispatched.
_ADVANCE_OPS = ("test", "branch", "build_viz", "refine_viz")


def _advance_study_for(source, studies: dict) -> str | None:
    """The on-disk study an advance op would run, or None (gated). The applied child of a
    revision runs as a plain ``test`` op on ``claim:<child>`` -- applied proposals are off the
    frontier, so there is deliberately no ``rp:`` resolution here: an unapplied proposal
    resolves to nothing and is never run."""
    src = str(source)
    slug = src.split(":", 1)[1] if src.startswith("claim:") else src
    return slug if slug in studies else None


def _arm_mvs(slug: str, spec: dict) -> dict:
    from .model_architect import bind_model_versions
    try:
        return bind_model_versions({slug: spec}).get(slug) or {}
    except Exception:  # noqa: BLE001 -- a malformed arm must not break advance
        return {}


def _run_mv_id(run: dict, arms: dict) -> str | None:
    """A run's model-version id: the id stamped at run time, else (legacy run) derived from
    its recorded params + its arm's composite."""
    from .model_architect import model_version
    if run.get("model_version"):
        return str(run["model_version"])
    arm = arms.get(run.get("variant")) if run.get("variant") else next(iter(arms.values()), None)
    if not arm:
        return None
    params = run.get("params")
    return model_version(arm["composite"], params if isinstance(params, dict) and params
                         else arm["params"])["id"]


def _has_run_at_current_mv(slug: str, spec: dict) -> bool:
    """True iff some run's mv equals the CURRENT mv of that run's arm. Studies whose arms
    can't be bound keep the legacy rule (any run => already ran)."""
    runs = [r for r in spec.get("runs") or [] if isinstance(r, dict)]
    if not runs:
        return False
    arms = _arm_mvs(slug, spec)
    if not arms:
        return True
    for r in runs:
        arm = arms.get(r.get("variant")) if r.get("variant") else next(iter(arms.values()))
        if arm and _run_mv_id(r, arms) == arm["id"]:
            return True
    return False


def _study_dir_on_disk(ws_root, inv_slug: str, slug: str) -> Path | None:
    for base in (studies_dir(Path(ws_root)), investigations_dir(Path(ws_root)) / inv_slug / "studies"):
        if (base / slug / "study.yaml").exists():
            return base / slug
    return None


def _run_names(sdir) -> set:
    if sdir is None:
        return set()
    return {r.get("name") for r in load_yaml_mapping(sdir / "study.yaml").get("runs") or []
            if isinstance(r, dict)}


def _dispatch_classify(p: dict, studies: dict, ws_root, inv_slug: str) -> tuple[str, str | None, list]:
    """Classify one ``execute_plan`` item for the ADVANCE phase, independent of per-run
    loop state (seen / gated / seen_slugs). Returns ``(state, slug, gaps)``:

    - ``state`` is ``"run"`` (advance would dispatch it now), ``"needs_build"`` (a test op
      whose study's run spec is incomplete), or ``"skip"`` (not an advance op, wrong
      consequence tier, an unresolvable/already-stamped render, already run at the current
      model version, or no on-disk study).
    - ``slug`` is the study the action would run (``None`` when nothing resolves).
    - ``gaps`` is the run-readiness gap list for ``needs_build`` (else ``[]``).

    This is the single source of truth both :func:`advance_cycle` (what it dispatches) and
    :func:`dispatchable_actions` (the honest "runnable" count) consult, so the two can't
    drift apart.
    """
    op = p["action_type"]
    if op not in _ADVANCE_OPS:
        return ("skip", None, [])
    if op in ("build_viz", "refine_viz"):
        vt = _viz_target(p["source"])
        entry = _viz_entry(studies, *vt) if vt else None
        if p["consequence"] != "render" or entry is None or not _viz_resolvable(entry):
            return ("skip", None, [])          # review-tier / unresolvable render stays gated
        from .viz_freshness import normalize_entry, read_meta
        sd = _study_dir_on_disk(ws_root, inv_slug, vt[0])
        lat = viz_inventory(ws_root, inv_slug, {vt[0]: studies[vt[0]]})[vt[0]]["_latest"]
        chart = sd / normalize_entry(entry)["output"] if sd else None
        meta = read_meta(chart) if chart is not None and chart.is_file() else None
        _want = _pinned_run(entry, lat)
        if _want and meta and meta.get("source_run_id") == _want:
            return ("skip", vt[0], [])         # already freshly stamped against the latest run
        return ("run", vt[0], [])
    if p["consequence"] not in ("simulation", "simulation+study"):
        return ("skip", None, [])              # review-tier (e.g. revise_model) stays gated
    slug = _advance_study_for(p["source"], studies)
    spec = studies.get(slug) if slug else None
    if not isinstance(spec, dict):
        return ("skip", None, [])              # unapplied / review-tier proposal resolves to nothing
    if _has_run_at_current_mv(slug, spec):
        return ("skip", slug, [])              # already ran this exact model version
    rr = run_readiness(spec, ws_root)
    if rr["ready"]:
        return ("run", slug, [])
    return ("needs_build", slug, rr["gaps"])


def dispatchable_actions(ws_root, inv_slug: str, cycle_result: dict, inv_spec: dict,
                         studies: dict) -> list[dict]:
    """The ``execute_plan`` items the ADVANCE phase would actually dispatch right now — the
    honest "runnable" set. Uses the same per-item gate as :func:`advance_cycle`
    (:func:`_dispatch_classify`): run-ready ``test``/``branch`` on an on-disk study not
    already run at its current model version, plus resolvable, not-already-stamped renders.
    Pure / read-only — runs nothing. ``N runnable == what advance_cycle would dispatch``."""
    from . import discovery_engine as de
    plan = de.execute_plan(cycle_result, inv_spec)
    return [p for p in plan
            if _dispatch_classify(p, studies, ws_root, inv_slug)[0] == "run"]


def _post_run_sync(sdir, before_runs: set) -> None:
    """Best-effort: sync the study's runs.db into study.yaml (idempotent; fills
    computed_outcomes) when the runner left it unsynced, then stamp each new run with the
    model version it ran so a later in-place composite change is detected as stale."""
    if sdir is None:
        return
    try:
        spec = load_yaml_mapping(sdir / "study.yaml")
        new = [r for r in spec.get("runs") or [] if isinstance(r, dict) and r.get("name") not in before_runs]
        if not new or not all(r.get("computed_outcomes") for r in new):
            from . import study_outcomes
            study_outcomes.sync(sdir)
        from io import StringIO
        arms = _arm_mvs(sdir.name, load_yaml_mapping(sdir / "study.yaml"))
        y = _ruamel()
        doc = y.load((sdir / "study.yaml").read_text(encoding="utf-8"))
        changed = False
        for r in doc.get("runs") or []:
            if isinstance(r, dict) and r.get("name") not in before_runs and "model_version" not in r:
                arm = arms.get(r.get("variant")) if r.get("variant") else next(iter(arms.values()), None)
                if arm:
                    r["model_version"] = arm["id"]
                    changed = True
        if changed:
            buf = StringIO()
            y.dump(doc, buf)
            (sdir / "study.yaml").write_text(buf.getvalue(), encoding="utf-8")
        # Link each finding to the run that computed it (provenance.run_ids) and fill the
        # absent code-owned slots, so the evidence ledger can classify it VERIFIED /
        # CONTRADICTED on the next cycle instead of leaving it "provisional — no
        # resolvable registered run". Fill-absent-only: never overwrites an authored
        # observed value or verdict. This is the write-back that turns an executed run
        # into a defensible (or honestly-contradicted) finding.
        from . import finding_observations
        finding_observations.populate_finding_observations(sdir)
    except Exception:  # noqa: BLE001 -- sync is best-effort, never fails an advance
        pass


def advance_cycle(ws_root: Path | str, inv_slug: str, *, autonomous: bool = False,
                  runner=None, max_runs: int = 12, render_hook=None,
                  worktree: Path | str | None = None) -> dict:
    """The cycle's ADVANCE phase — build-if-needed and RUN the open questions, so turning
    the crank moves the science forward instead of only describing it.

    Repeatedly: crank, take the top **run-ready** ``test`` op (an ``open_question`` /
    ``pending_gate`` on an existing study), run it via :func:`execute_action`, re-crank —
    until no runnable open work remains, a study needs BUILDING (not run-ready: its run
    spec is incomplete, per :func:`run_readiness`), or only human-gated items are left. A
    ``test`` op is tried once (``seen``), so a run that only SKIPs its gates doesn't loop.

    This makes "run the open questions" a defined phase of a cycle, not an afterthought.
    Without ``autonomous`` it reports what it WOULD run (dry). Returns
    ``{runs, needs_build, frontier_before, frontier_after}``."""
    from . import discovery_engine as de
    ran: list[dict] = []
    needs_build: list[dict] = []
    seen: set = set()
    seen_slugs: set = set()
    gated_ops: set = set()      # (op, source) already gated: not retried, but they do not claim the study
    _, inv0, st0 = load_cycle_inputs(ws_root, inv_slug)
    before = de.closure(_cycle_with_evidence(ws_root, inv_slug, inv0, st0), inv0)["open_items"]
    for _ in range(max_runs):
        _, inv, studies = load_cycle_inputs(ws_root, inv_slug)
        plan = de.execute_plan(_cycle_with_evidence(ws_root, inv_slug, inv, studies), inv)
        target = None
        target_slug = target_op = None
        for p in plan:
            op, source = p["action_type"], p["source"]
            if source in seen or (op, source) in gated_ops:
                continue
            # Intrinsic admissibility — the SAME gate dispatchable_actions reports, so the
            # banner's "runnable" count matches what this loop dispatches. seen / gated_ops
            # / seen_slugs are this advance's per-run state, applied around it.
            state, slug, gaps = _dispatch_classify(p, studies, ws_root, inv_slug)
            if state == "skip":
                continue
            # a study already claimed by a dispatched op this advance is not re-run
            # (a render claims no study, so renders ignore seen_slugs)
            if op not in ("build_viz", "refine_viz") and slug in seen_slugs:
                seen.add(source)
                continue
            if state == "needs_build":
                needs_build.append({"study": slug, "gaps": gaps})
                seen.add(source)
                continue
            target, target_slug, target_op = source, slug, op
            break
        if target is None:
            break                                   # no runnable test op left
        if not autonomous:
            seen.add(target)
            seen_slugs.add(target_slug)
            ran.append({"target": target, "status": "would-run (pass --autonomous to execute)"})
            break
        sdir = _study_dir_on_disk(ws_root, inv_slug, target_slug)
        before_runs = _run_names(sdir)
        rec = execute_action(ws_root, inv_slug, autonomous=True, runner=runner, target=target,
                             action=target_op, render_hook=render_hook, worktree=worktree)
        if target_op in ("build_viz", "refine_viz"):
            # a render claims neither the study nor stops the loop; a failed/gated render
            # is not retried this advance (gated_ops), a done one is not re-dispatched.
            if rec["status"] == "executed":
                seen.add(target)
                _vt = _viz_target(target)
                _, _inv, _st = load_cycle_inputs(ws_root, inv_slug)
                _e = _viz_entry(_st, *_vt) if _vt else None
                _lat = viz_inventory(ws_root, inv_slug, {_vt[0]: _st[_vt[0]]})[_vt[0]]["_latest"] if _e else None
                if _e is not None:
                    _post_render_stamp(sdir, _e, _lat, rec.get("command"))
            else:
                gated_ops.add((target_op, target))
            ran.append({"target": target, "status": rec["status"],
                        "outcome": (rec.get("outcome") or {}).get("detail")})
            continue
        if rec["status"] == "gated":
            gated_ops.add((target_op, target))
        else:
            seen.add(target)
        if rec["status"] == "executed":
            _post_run_sync(sdir, before_runs)
        ran.append({"target": target, "status": rec["status"],
                    "outcome": (rec.get("outcome") or {}).get("detail")})
        if rec["status"] == "gated":
            continue            # a gated op neither stops the loop nor claims its study
        seen_slugs.add(target_slug)   # only a dispatched target claims its study
        if rec["status"] != "executed":
            break               # hard failure
    _, inv2, st2 = load_cycle_inputs(ws_root, inv_slug)
    after = de.closure(_cycle_with_evidence(ws_root, inv_slug, inv2, st2), inv2)["open_items"]
    return {"runs": ran, "needs_build": needs_build,
            "frontier_before": before, "frontier_after": after}


def summarize_advance(result: dict) -> str:
    """Human-readable advance log: what ran, what needs building, the frontier delta."""
    L = [f"frontier {result['frontier_before']} → {result['frontier_after']} "
         f"({len(result['runs'])} run attempt(s))"]
    for r in result["runs"]:
        L.append(f"  [{r['status']}] {r['target']}" + (f" — {r['outcome']}" if r.get("outcome") else ""))
    for b in result["needs_build"]:
        L.append(f"  [needs build] {b['study']}: {'; '.join(b['gaps'][:2])}")
    if not result["runs"] and not result["needs_build"]:
        L.append("  (no runnable open questions — nothing to advance)")
    return "\n".join(L)


def build_completion_map(ws_root: Path | str, inv_slug: str) -> dict:
    """The single 'what's left to complete this cycle' view: the gate state, the
    verification debt (gates met by verdict only), the expert decisions that gate the
    next studies, and the ordered next-step commands. Pure read — assembles the frontier,
    closure, suggestions, and the execution plan into one map."""
    from . import discovery_engine as de
    _, inv_spec, studies = load_cycle_inputs(ws_root, inv_slug)
    cyc = _cycle_with_evidence(ws_root, inv_slug, inv_spec, studies)
    clo = de.closure(cyc, inv_spec)
    front = de.frontier(cyc, inv_spec)
    plan = de.execute_plan(cyc, inv_spec)
    suggestions = [s for s in (cyc.get("suggestions") or []) if s.get("status") == "awaiting_expert"]
    # Presentation readiness ⊥ scientific closure (P1): an investigation can be CLOSED (every
    # acceptance criterion met) while still not PUBLISHABLE — a member study's figures, evaluators,
    # or contracts haven't materialized (observed on a real workspace: closed-ish arc, empty viz
    # tabs). `publishable` is every member study `complete` (composite resolves + viz rendered +
    # measures resolvable + emitter/tests/contracts); `not_publishable` names what each is missing.
    study_completion: dict = {}
    for slug, spec in (studies or {}).items():
        if isinstance(spec, dict):
            r = run_readiness(spec, ws_root)
            study_completion[slug] = {"complete": r["complete"], "gaps": r["gaps"]}
    publishable = bool(study_completion) and all(c["complete"] for c in study_completion.values())
    not_publishable = {s: c["gaps"] for s, c in study_completion.items() if not c["complete"]}
    return {
        "closed": clo["closed"],
        "publishable": publishable,
        "not_publishable": not_publishable,
        "study_completion": study_completion,
        "open_items": clo["open_items"],
        "unaddressed_criteria": clo["unaddressed_criteria"],
        "pending_gates": [{"target": i["target"], "detail": i["rationale"]}
                          for i in front if i["kind"] == "pending_gate"],
        "gating_decisions": [{"target": i["target"], "detail": i["rationale"]}
                             for i in front if i["kind"] == "open_decision"],
        "suggested_studies": [{"parent": s.get("parent_study"),
                               "decision": (s.get("awaiting_expert") or [""])[0]}
                              for s in suggestions],
        "next_steps": [{"action": p["action_type"], "source": p["source"],
                        "consequence": p["consequence"], "command": p["command"],
                        "auto_safe": p["auto_safe"]} for p in plan],
    }


def summarize_completion(cmap: dict) -> str:
    """Human-readable completion map: gates to verify, decisions to make, steps to run."""
    L = [f"closure: {'CLOSED' if cmap['closed'] else 'OPEN'}  ·  {cmap['open_items']} open frontier item(s)"]
    if "publishable" in cmap:
        L.append(f"publishable (presentation complete): {'YES' if cmap['publishable'] else 'NO'}"
                 + (f"  ·  {len(cmap['not_publishable'])} study(ies) not materialized"
                    if cmap.get("not_publishable") else ""))
        for slug, gaps in (cmap.get("not_publishable") or {}).items():
            L.append(f"  ▢ {slug}: {', '.join(gaps[:3])}")
    if cmap["unaddressed_criteria"]:
        L.append("\nunmet acceptance criteria (block closure):")
        L += [f"  ✗ {s}" for s in cmap["unaddressed_criteria"]]
    if cmap["pending_gates"]:
        L.append("\nverification gates — met by verdict only, behavior unrun (run to verify):")
        L += [f"  ○ {g['target']}: {g['detail']}" for g in cmap["pending_gates"]]
    if cmap["gating_decisions"]:
        L.append("\ndecisions that gate the next studies (an AI decider can drive these):")
        L += [f"  ◆ {d['target']}" for d in cmap["gating_decisions"]]
    if cmap["suggested_studies"]:
        L.append("\nsuggested follow-up studies (each awaits the decision above):")
        L += [f"  → on {s['parent']}: {(s['decision'] or '')[:100]}" for s in cmap["suggested_studies"]]
    L.append("\nnext steps to run (ordered by value; consequence-gated):")
    for p in cmap["next_steps"][:8]:
        tag = "auto-safe" if p["auto_safe"] else p["consequence"]
        L.append(f"  [{tag:>16}]  {p['action']} {p['source']}")
        L.append(f"  {' ' * 20}{p['command']}")
    if not cmap["next_steps"]:
        L.append("  (frontier clear — nothing to run)")
    return "\n".join(L)


def summarize_suggestions(suggestions: list[dict]) -> str:
    ready = [s for s in suggestions if s["status"] == "ready"]
    awaiting = [s for s in suggestions if s["status"] == "awaiting_expert"]
    lines = [f"suggested studies: {len(suggestions)}  ({len(ready)} ready, "
             f"{len(awaiting)} awaiting expert input)"]
    if ready:
        lines.append("ready to seed:")
        for s in ready:
            lines.append(f"  {s['id']}  {s['title']}"
                         + (f"  (builds on {s['parent_study']})" if s.get("parent_study") else "")
                         + f"  [{s['source']}]")
    if awaiting:
        lines.append("awaiting expert input (resolve the decision, then seed):")
        for s in awaiting:
            lines.append(f"  {s['id']}  (builds on {s.get('parent_study')})")
            for q in s["awaiting_expert"]:
                lines.append(f"      needs: {q}")
    if not suggestions:
        lines.append("  none — no new studies proposed this cycle.")
    return "\n".join(lines)


def snapshot_validations(ws_root: Path | str, slug: str) -> dict:
    """Persist the current validation records' signatures as the investigation's
    accepted ``validation_snapshot`` (RFC-0007 Phase 4). Later cycles compare a fresh
    re-derivation against it; drift surfaces as a ``stale_validation`` frontier item
    (an accepted judgment an upstream model/run change moved past). Returns the
    snapshot. Like :func:`persist_cycle`, re-reads on-disk so authored edits are not
    clobbered and writes only this regenerable cache."""
    from .validation import snapshot_of
    inv_path, inv_spec, studies = load_cycle_inputs(ws_root, slug)
    snap = snapshot_of(run_cycle(inv_spec, studies)["validations"])
    path = Path(inv_path)
    on_disk = load_yaml_mapping(path) if path.exists() else dict(inv_spec)
    on_disk["validation_snapshot"] = snap
    save_yaml_atomic(path, on_disk)
    return snap


# ---------------------------------------------------------------------------
# load_cycle_inputs — read an investigation + its member studies from disk
# ---------------------------------------------------------------------------

def load_cycle_inputs(ws_root: Path | str, slug: str) -> tuple[Path, dict, dict[str, dict]]:
    """Load ``(inv_path, inv_spec, studies_by_slug)`` for an investigation.

    Member studies resolve from the flat ``studies/<slug>/study.yaml`` first, then
    the nested ``<investigations>/<inv>/studies/<slug>/study.yaml`` fallback; a
    member with no spec on disk is skipped (not fatal), matching the derive layer.
    """
    ws = Path(ws_root)
    inv_path = investigations_dir(ws) / slug / "investigation.yaml"
    inv_spec = load_yaml_mapping(inv_path)

    flat = studies_dir(ws)
    nested = investigations_dir(ws) / slug / "studies"
    # Resolve membership to THIS investigation only. Read the pre-migration `studies:` AND the
    # post-migration `members:` (dict/str item shapes), then ac/at_a_glance via investigation_members,
    # and only as a last resort scan disk SCOPED by each study's `investigation:` back-ref. The old
    # code read `studies:` alone and fell back to the whole flat studies dir, so a members-based
    # investigation pulled every workspace study into its cycle — and `apply` rewrote sibling
    # investigations' study.yaml files.
    member_slugs = (
        _declared_member_slugs(inv_spec)
        or investigation_members(inv_path)
        or _member_slugs_on_disk(slug, flat, nested)
    )

    studies_by_slug: dict[str, dict] = {}
    for s in member_slugs:
        for base in (flat, nested):
            sp = base / s / "study.yaml"
            if sp.exists():
                studies_by_slug[s] = load_yaml(sp)
                break
    return inv_path, inv_spec, studies_by_slug


def missing_members(ws_root: Path | str, slug: str) -> list[str]:
    """Member slugs listed in the investigation with no ``study.yaml`` on disk (flat or
    nested). Read-only. ``load_cycle_inputs`` skips these; the
    ``structural.investigation.members`` validator surfaces them on the frontier."""
    ws = Path(ws_root)
    inv_spec = load_yaml_mapping(investigations_dir(ws) / slug / "investigation.yaml")
    bases = (studies_dir(ws), investigations_dir(ws) / slug / "studies")
    return [s for s in (inv_spec.get("studies") or [])
            if not any((b / s / "study.yaml").exists() for b in bases)]


def _load_runs_by_study(ws: Path, inv_slug: str, studies: dict) -> dict[str, list]:
    """``{study_slug: [registered runs]}`` from each study's ``runs.db`` (flat path
    first, then the nested investigation path). Shared by the Phase A/B/F commands."""
    from . import run_registry as _rr
    runs_by_study: dict[str, list] = {}
    for slug in studies:
        runs: list = []
        for base in (studies_dir(ws), investigations_dir(ws) / inv_slug / "studies"):
            db = base / slug / "runs.db"
            if db.exists():
                runs = _rr.list_runs(db)
                break
        runs_by_study[slug] = runs
    return runs_by_study


def viz_inventory(ws_root, inv_slug: str, studies: dict) -> dict:
    """IMPURE (the one disk read the viz loop adds): per study, a row per declared
    ``visualizations[]`` entry ``{path, exists, meta, mtime, content_hash}``, plus
    ``_latest`` (latest run ``{run_id, completed_at}`` or None) and ``_orphans``
    (``viz/*.html`` on disk but not declared). Fed to ``run_cycle`` as data."""
    from .viz_freshness import _hash, normalize_entry, read_meta
    ws = Path(ws_root)
    runs_by = _load_runs_by_study(ws, inv_slug, studies)
    out: dict = {}
    for slug, spec in studies.items():
        sdir = studies_dir(ws) / slug
        nested = investigations_dir(ws) / inv_slug / "studies" / slug
        if not sdir.is_dir() and nested.is_dir():
            sdir = nested
        rows: dict = {}
        declared: set[str] = set()
        for entry in (spec or {}).get("visualizations") or []:
            ns = normalize_entry(entry)
            if not ns["name"] or not ns["output"]:
                continue
            declared.add(ns["output"])
            chart = sdir / ns["output"]
            if chart.is_file():
                rows[ns["name"]] = {"path": ns["output"], "exists": True,
                                    "meta": read_meta(chart),
                                    "mtime": chart.stat().st_mtime,
                                    "content_hash": _hash(chart)}
            else:
                rows[ns["name"]] = {"path": ns["output"], "exists": False,
                                    "meta": None, "mtime": None, "content_hash": None}
        runs = runs_by.get(slug) or []
        rows["_latest"] = ({"run_id": runs[0].get("run_id"),
                            "completed_at": runs[0].get("completed_at")} if runs else None)
        orph: list[str] = []
        for sub, pats in (("viz", ("*.html",)), ("charts", ("*.html", "*.svg", "*.png"))):
            d = sdir / sub
            if d.is_dir():
                for pat in pats:
                    orph += [f"{sub}/{f.name}" for f in d.glob(pat)
                             if f.is_file() and f"{sub}/{f.name}" not in declared]
        rows["_orphans"] = sorted(orph)
        out[slug] = rows
    return out


def _declared_member_slugs(inv_spec: dict) -> list[str]:
    """Member slugs an investigation spec DECLARES: the pre-migration ``studies:`` or the
    post-migration ``members:`` list, normalizing bare slugs and ``{study|slug|name: ...}`` dicts.
    ``studies:`` wins when both are present (transitional specs keep the old list authoritative)."""
    raw = inv_spec.get("studies") or inv_spec.get("members") or []
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for e in raw:
        if isinstance(e, str):
            if e not in out:
                out.append(e)
        elif isinstance(e, dict):
            slug = e.get("study") or e.get("slug") or e.get("name")
            if slug and str(slug) not in out:
                out.append(str(slug))
    return out


def _member_slugs_on_disk(inv_slug: str, flat: Path, nested: Path) -> list[str]:
    """On-disk member studies scoped to investigation ``inv_slug`` — the last-resort fallback when
    the spec declares no membership. Every study under the investigation's OWN nested ``studies/``
    dir counts (already dir-scoped); a FLAT ``studies/<slug>/`` counts only when its ``study.yaml``
    back-refs ``investigation: inv_slug``. It deliberately NEVER returns the whole flat studies dir:
    an undeclared membership must not pull sibling investigations' studies into this cycle, which is
    what let ``apply`` rewrite their files."""
    out: list[str] = []
    if nested.is_dir():
        for child in sorted(nested.iterdir()):
            if (child / "study.yaml").exists() and child.name not in out:
                out.append(child.name)
    if flat.is_dir():
        for child in sorted(flat.iterdir()):
            sp = child / "study.yaml"
            if child.name in out or not sp.exists():
                continue
            try:
                spec = load_yaml(sp)
            except Exception:  # noqa: BLE001 — a malformed sibling never breaks the scan
                continue
            if isinstance(spec, dict) and spec.get("investigation") == inv_slug:
                out.append(child.name)
    return out


# ---------------------------------------------------------------------------
# A compact summary for the CLI
# ---------------------------------------------------------------------------

def summarize(result: dict) -> str:
    verdicts = result["knowledge"]["verdicts"]
    tally: dict[str, int] = {}
    for rec in verdicts.values():
        tally[rec["verdict"]] = tally.get(rec["verdict"], 0) + 1
    verdict_line = ", ".join(f"{v}={n}" for v, n in sorted(tally.items())) or "none"
    gate_line = ", ".join(f"{g['id']}:{g['verdict']}" for g in result["gates"]) or "none"
    return (
        f"verdicts: {verdict_line}\n"
        f"claims: {len(result['argument']['claims'])}  edges: {len(result['argument']['edges'])}\n"
        f"narrative units: {len(result['narrative']['units'])}  "
        f"limitations: {len(result['narrative']['limitations'])}  "
        f"open_questions: {len(result['narrative']['open_questions'])}\n"
        f"audit gaps: {len(result['audit'])}  open_calls: {len(result['open_calls'])}\n"
        f"plan gates: {gate_line}"
    )


def _fmt_expected(expected) -> str:
    """Compact one-line rendering of a prediction's ``expected`` band."""
    if isinstance(expected, dict):
        if "op" in expected and "value" in expected:
            return f"{expected['op']} {expected['value']}"
        if "low" in expected or "high" in expected:
            return f"{expected.get('low', '-inf')}..{expected.get('high', 'inf')}"
    return str(expected)


def _summarize_hypotheses(inv_spec: dict, study_specs: list) -> str:
    """Render the competing-hypotheses contest: each hypothesis, its predicted
    observable, and the ▲supports/▼weakens/⊘excludes tally its studies' runs give
    it (empty until the studies run). Marks which studies are *discriminable* —
    carry competing hypotheses a single run would separate."""
    from . import hypotheses as _hyp
    rolled = _hyp.rollup_support(inv_spec or {}, study_specs or [])
    hyps = rolled.get("hypotheses") or []
    if not hyps:
        return "hypotheses: none authored (add a `hypotheses:` block to investigation.yaml)"
    disc = _hyp.discriminating_hypotheses(inv_spec or {})
    anoms = _hyp.anomalies(inv_spec or {}, study_specs or [])
    flags = _hyp.fidelity_flags(inv_spec or {}, study_specs or [])
    n_disc = sum(1 for d in disc.values() if d.get("discriminable"))
    by_study: dict[str, list] = {}
    for h in hyps:
        by_study.setdefault(h.get("study") or "(unassigned)", []).append(h)
    lines = [f"hypotheses: {len(hyps)} competing across {len(by_study)} study(ies) — "
             f"{n_disc} discriminable (a single run separates the alternatives)"
             + (f", {len(anoms)} anomaly(ies)" if anoms else "")
             + (f", {len(flags)} fidelity caveat(s)" if flags else "")]
    for slug in sorted(by_study):
        d = disc.get(slug) or {}
        mark = (f"  ⇄ discriminable on {d['observable']}" if d.get("discriminable")
                else "  (not yet discriminable — needs opposed predictions on one observable)")
        lines.append(f"\n{slug}{mark}")
        for h in by_study[slug]:
            s = _hyp.support_summary(h.get("support_log") or [])
            preds = h.get("predictions") or []
            obs = preds[0].get("observable") if preds else "?"
            exp = _fmt_expected(preds[0].get("expected")) if preds else "?"
            lines.append(f"  ▲{s['supports']} ▼{s['weakens']} ⊘{s['excludes']}  "
                         f"{h.get('id'):<28} {obs} {exp}   [{h.get('status', 'open')}]")
    if anoms:
        lines.append("\nanomalies — observed outside all competing predictions (seed a new hypothesis):")
        for a in anoms:
            lines.append(f"  ⚠ {a['study']}: {a['observable']} = {a['observed']}")
            lines.append(f"      → {a['proposed_question']}")
    if flags:
        lines.append("\nfidelity caveats — support must not rest on a coarse/wrong-mechanism model (stage 4/6):")
        for fl in flags:
            tag = {"superseded_evidence": "superseded", "gated_evidence": "gated",
                   "low_fidelity_support": "low-fidelity"}.get(fl["kind"], fl["kind"])
            hyp = (" [" + ", ".join(fl["hypotheses"]) + "]") if fl.get("hypotheses") else ""
            lines.append(f"  ⚑ {fl['study']} ({tag}): {fl['detail']}{hyp}")
    lines.append("\n(support tallies populate from each study's findings once it runs)")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
def _summarize_evidence_reconciliation(result: dict) -> str:
    """Render the Cycle N+1 Phase A evidence ledger — which findings can be trusted."""
    from .evidence_integrity import VERIFIED, PROVISIONAL, CONTRADICTED, UNSUPPORTED
    mark = {VERIFIED: "✓ verified", PROVISIONAL: "~ provisional",
            CONTRADICTED: "✗ contradicted", UNSUPPORTED: "· unsupported"}
    s = result.get("summary", {})
    lines = [f"evidence reconciliation: {s.get(VERIFIED,0)} verified · {s.get(PROVISIONAL,0)} provisional"
             f" · {s.get(CONTRADICTED,0)} contradicted · {s.get(UNSUPPORTED,0)} unsupported"]
    by = result.get("by_study", {})
    for slug in by:
        b = by[slug]
        st = b.get("status")
        lines.append(f"\n{slug}  [{mark.get(st, st or 'no numeric evidence')}]  "
                     f"({b.get('registered_runs',0)} registered run(s))")
        for row in result.get("ledger", []):
            if row.get("study") != slug:
                continue
            lines.append(f"  {mark.get(row['status'], row['status']):<16} "
                         f"{row.get('measure') or row.get('finding')}"
                         + (f" = {row['observed']}" if row.get('observed') is not None else "")
                         + f"  — {row['reason']}")
    if s.get(PROVISIONAL, 0) or s.get(UNSUPPORTED, 0):
        lines.append("\n→ repair action (R0): numeric findings without a resolvable registered run must be "
                     "re-run through the gated execution pipeline before they count as biological support.")
    return "\n".join(lines)


def _summarize_portfolio(portfolio: list) -> str:
    """Render the Cycle N+1 Phase B CandidateAction portfolio."""
    from .candidate_action import REPAIR, RESOLVE, EXTEND
    tag = {REPAIR: "REPAIR ", RESOLVE: "RESOLVE", EXTEND: "EXTEND "}
    by_kind = {k: sum(1 for a in portfolio if a["kind"] == k) for k in (REPAIR, RESOLVE, EXTEND)}
    lines = [f"portfolio: {len(portfolio)} candidate action(s) — "
             f"{by_kind[REPAIR]} repair · {by_kind[RESOLVE]} resolve · {by_kind[EXTEND]} extend "
             "(execution order; repairs are prerequisites)"]
    for i, a in enumerate(portfolio, 1):
        dep = f"  ⟸ needs {', '.join(a['depends_on'])}" if a.get("depends_on") else ""
        disc = f"  disc={a['discrimination']}" if a.get("discrimination") else ""
        lines.append(f"  {i:>2}. [{tag.get(a['kind'], a['kind'])}] {a.get('target')}"
                     f"  value={a.get('value')}{disc}{dep}")
        lines.append(f"       {a.get('rationale', '')}")
        if a.get("discriminates"):
            lines.append(f"       ⇄ separates: {', '.join(str(h) for h in a['discriminates'])}")
    return "\n".join(lines)


def _fmt_when(w):
    return "+".join(f"{k}={v}" for k, v in (w or {}).items()) or "baseline"


def _summarize_design(design: dict | None, study: str) -> str:
    """Render a Cycle N+1 Phase C discriminating-study design."""
    if not design:
        return f"design: {study} has no discriminable competing hypotheses — nothing to design."
    lines = [f"discriminating study design — {study}",
             f"  contrast: {' vs '.join(design['hypothesis_contrast'])}",
             f"  measures: {', '.join(design['measurements'])}"]
    lines.append("  intervention arms:")
    ctrl_ids = {(c['hypothesis'], tuple(sorted((c.get('when') or {}).items()))) for c in design['controls']}
    for arm in design["interventions"]:
        is_ctrl = (arm['hypothesis'], tuple(sorted((arm.get('when') or {}).items()))) in ctrl_ids
        lines.append(f"    - {arm['hypothesis']}  @ {_fmt_when(arm.get('when'))}"
                     + ("   [control]" if is_ctrl else ""))
    lines.append("  registered predictions (pre-run):")
    for p in design["predictions"]:
        lines.append(f"    - {p['hypothesis_id']}: {p['observable']} {p['expected']} when {_fmt_when(p['intervention'])}")
    lines.append(f"  decision rule (precommitted): {design['decision_criterion']['rule']}")
    issues = design.get("control_issues") or []
    lines.append("  controls: OK" if not issues else "  control issues: " + "; ".join(issues))
    return "\n".join(lines)


def _summarize_model_build(request: dict, catalog: list, routes: dict, mv: dict) -> str:
    """Render a Cycle N+1 Phase D model-acquisition decision."""
    tag = {"reuse": "REUSE", "wrap": "WRAP ", "build": "BUILD"}
    lines = [f"model build — question {request['question_id']}  "
             f"(catalog: {len(catalog)} component(s) from the campaign)",
             f"  required mechanisms: {', '.join(request['required_mechanisms'])}"]
    lines.append("  acquisition routes:")
    for mech, r in routes.items():
        comp = (r["component"].split(".")[-1] if r.get("component") else "—")
        lines.append(f"    [{tag.get(r['route'], r['route'])}] {mech:<22} {comp}")
        lines.append(f"            {r['rationale']}")
    issues = _mod_coupling_issues(routes, catalog)
    lines.append("  coupling validation: OK" if not issues else "  coupling checks:")
    for i in issues:
        lines.append(f"    ⚑ {i}")
    lines.append(f"  model version: {mv['id']}  (composite {mv['composite']})")
    return "\n".join(lines)


def _mod_coupling_issues(routes, catalog):
    from .model_architect import validate_coupling
    return validate_coupling(routes, catalog)


def _summarize_evaluation(cmp: dict, dmap: list) -> str:
    """Render a Cycle N+1 Phase F evaluation: per-study claim verdicts + disagreements."""
    lines = ["claim evaluation (support gated by evidence integrity)"]
    for slug in sorted(cmp):
        c = cmp[slug]
        head = f"  {slug}: {c['status']}"
        if c.get("favored"):
            head += f"  (favored: {c['favored']})"
        if c.get("evidence_status"):
            head += f"  [evidence: {c['evidence_status']}]"
        lines.append(head)
        if c.get("note"):
            lines.append(f"      ⚑ {c['note']}")
        for h in c["hypotheses"]:
            lines.append(f"      - {h['id']}: net {h['support']['net']:+d} → {h['verdict']}")
    disc = [d for d in dmap if d["disagreement"]]
    lines.append("  model disagreements:" if disc else "  model disagreements: none")
    for d in disc:
        arms = ", ".join(f"{k}={v}" for k, v in d["arms"].items())
        lines.append(f"    ⇄ {d['study']} / {d['observable']}: {arms}  (spread {d['spread']}) — do not average")
    return "\n".join(lines)


def _summarize_discovery_delta(delta: dict) -> str:
    """Render the per-cycle DiscoveryDelta."""
    lines = [f"DiscoveryDelta  (fingerprint {delta['fingerprint']})"]
    lines.append("  claims added / qualified this cycle:" if delta["added_or_qualified"]
                 else "  claims added / qualified this cycle: none")
    for a in delta["added_or_qualified"]:
        lines.append(f"    + {a['study']}: {a['claim']}  [{a['strength']}]")
    if delta["gated"]:
        lines.append("  gated (model-fidelity):")
        for g in delta["gated"]:
            lines.append(f"    · {g['study']}: {g.get('reason')}")
    if delta["model_disagreements"]:
        lines.append("  model disagreements to resolve:")
        for d in delta["model_disagreements"]:
            lines.append(f"    ⇄ {d['study']} / {d['observable']}  (spread {d['spread']})")
    if delta["anomalies"]:
        lines.append("  anomalies (outside every predicted band):")
        for a in delta["anomalies"]:
            lines.append(f"    ! {a['study']} / {a['observable']} = {a['observed']}")
    if delta["regressions"]:
        lines.append("  recorded regressions (progress, kept visible):")
        for r in delta["regressions"]:
            lines.append(f"    ↺ {r['study']}: {r['note']}")
    return "\n".join(lines)


def _coerce_scalar(v: str):
    """Coerce a ``--param key=value`` string value to int/float when it parses."""
    s = str(v).strip()
    for cast in (int, float):
        try:
            return cast(s)
        except ValueError:
            pass
    return s


def _summarize_refinement(slug: str, needs: list, changes: list, autonomy: str) -> str:
    lines = [f"refine study — {slug}  (autonomy: {autonomy})"]
    lines.append("  needs: " + ("; ".join(f"{n['kind']} ({n['reason']})" for n in needs) if needs
                                else "none — adequately rigorous"))
    if changes:
        lines.append("  refinements:")
        for c in changes:
            lines.append(f"    - [{c['kind']}] {c['detail']}")
    return "\n".join(lines)


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


def _summarize_seeded_study(study: dict, gaps: list, autonomy: str) -> str:
    base = next((a for a in (study.get("baseline") or []) if isinstance(a, dict)), {})
    tests = study.get("behavior_tests") or study.get("tests") or []
    lines = [f"seeded study — {study.get('name')}  (autonomy: {autonomy})",
             f"  title: {study.get('title')}",
             f"  seeded from: {study.get('seeded_from')}",
             f"  baseline: {str(base.get('composite','')).split('.')[-1]}  params={base.get('params')}",
             f"  observables: {study.get('observables')}",
             f"  behavior tests: {[t.get('name') for t in tests if isinstance(t, dict)]}"]
    if study.get("purpose"):
        lines.append(f"  purpose.question: {(study['purpose'] or {}).get('question')}")
    lines.append("  run-ready: YES" if not gaps else "  run-ready gaps: " + "; ".join(gaps))
    return "\n".join(lines)


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



def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="viva-discover",
        description="Drive one turn of the RFC-0005/0006 discovery cycle over an investigation.",
    )
    parser.add_argument("--workspace", default=".", help="workspace root (default: cwd)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_cycle = sub.add_parser("cycle", help="run + persist one discovery cycle")
    p_cycle.add_argument("investigation", help="investigation slug")
    p_cycle.add_argument("--no-write", action="store_true", help="project only, do not persist")

    p_status = sub.add_parser("status", help="project one cycle and print the summary (no write)")
    p_status.add_argument("investigation", help="investigation slug")

    p_snap = sub.add_parser("snapshot-validations",
                            help="accept the current validation state; later drift is flagged stale")
    p_snap.add_argument("investigation", help="investigation slug")

    p_diag = sub.add_parser("diagnose",
                            help="classify the crank's productivity bottlenecks (why it is/isn't progressing)")
    p_diag.add_argument("investigation", help="investigation slug")

    p_sugg = sub.add_parser("suggest",
                            help="suggest new studies to run next, each with the expert input it needs")
    p_sugg.add_argument("investigation", help="investigation slug")

    p_opp = sub.add_parser("opportunities",
                           help="rank the frontier by value — what is most valuable to investigate next")
    p_opp.add_argument("investigation", help="investigation slug")

    p_hyp = sub.add_parser("hypotheses",
                           help="the competing-hypotheses contest: each hypothesis, its predicted observable, and the support its studies' runs give it")
    p_hyp.add_argument("investigation", help="investigation slug")

    p_recon = sub.add_parser("reconcile-evidence",
                             help="Cycle N+1 Phase A: classify each finding's evidence as verified / provisional / contradicted / unsupported against the registered runs")
    p_recon.add_argument("investigation", help="investigation slug")

    p_plan = sub.add_parser("plan",
                            help="Cycle N+1 Phase B: the classified CandidateAction portfolio (repair / resolve / extend), ranked and dependency-ordered")
    p_plan.add_argument("investigation", help="investigation slug")

    p_design = sub.add_parser("design",
                              help="Cycle N+1 Phase C: design a discriminating study for a member study's competing hypotheses (contrast, controls, registered predictions, committed decision rule)")
    p_design.add_argument("investigation", help="investigation slug")
    p_design.add_argument("study", help="member study slug")
    p_design.add_argument("--require-control", action="append", default=[],
                          help="a control the design must include (repeatable), e.g. --require-control no-exchange")

    p_build = sub.add_parser("build-model",
                             help="Cycle N+1 Phase D: capability-driven model acquisition (reuse / wrap / build) for required mechanisms, against the campaign's composite catalog")
    p_build.add_argument("investigation", help="investigation slug")
    p_build.add_argument("--mechanism", action="append", default=[], required=True,
                         help="a required mechanism (repeatable), e.g. --mechanism exchange --mechanism adhesion")
    p_build.add_argument("--observable", action="append", default=[], help="a required observable (repeatable)")
    p_build.add_argument("--question", default="Q", help="question id for the ModelBuildRequest")

    p_eval = sub.add_parser("evaluate",
                            help="Cycle N+1 Phase F: promote or reject claims — per-study hypothesis comparison with claim verdicts gated by evidence integrity, plus the model-disagreement map")
    p_eval.add_argument("investigation", help="investigation slug")

    p_delta = sub.add_parser("delta",
                             help="Cycle N+1 Phase F: the DiscoveryDelta — what this cycle established / qualified / gated, with model disagreements, anomalies and recorded regressions")
    p_delta.add_argument("investigation", help="investigation slug")

    p_seed = sub.add_parser("seed-study",
                            help="author a complete, run-ready study.yaml for a proposed/follow-up study (closes propose→seed→run); --autonomy gates selection")
    p_seed.add_argument("investigation", help="investigation slug")
    p_seed.add_argument("parent", help="parent study slug")
    p_seed.add_argument("--slug", required=True, help="new study slug")
    p_seed.add_argument("--from-finding", default=None, help="parent finding id to seed purpose/assumptions from")
    p_seed.add_argument("--title", default=None)
    p_seed.add_argument("--param", action="append", default=[],
                        help="model_change param key=value (repeatable) — the variation under test")
    p_seed.add_argument("--autonomy", choices=["human_in_the_loop", "autonomous"], default="human_in_the_loop")
    p_seed.add_argument("--write", action="store_true",
                        help="write studies/<slug>/study.yaml + register it in the investigation (else just print)")

    p_refine = sub.add_parser("refine-study",
                              help="improve an existing study in place (replicate seeds / sweep variants / raise fidelity), from the engine's rigor + fidelity flags")
    p_refine.add_argument("investigation", help="investigation slug")
    p_refine.add_argument("study", help="study slug to refine")
    p_refine.add_argument("--composite", default=None, help="higher-fidelity composite to raise the baseline to")
    p_refine.add_argument("--autonomy", choices=["human_in_the_loop", "autonomous"], default="human_in_the_loop")
    p_refine.add_argument("--write", action="store_true", help="apply the refinement to the study.yaml (else just print)")

    p_comp = sub.add_parser("complete",
                            help="the completion map: gates to verify, decisions that gate the next studies, and the ordered next-step commands")
    p_comp.add_argument("investigation", help="investigation slug")

    p_adv = sub.add_parser("advance",
                           help="the cycle's ADVANCE phase: build-if-needed and run the open questions (run-ready `test` ops), re-cranking after each")
    p_adv.add_argument("investigation", help="investigation slug")
    p_adv.add_argument("--autonomous", action="store_true",
                       help="actually run the runnable open questions (else report what it would run)")

    p_exec = sub.add_parser("execute",
                            help="the recommendation→action bridge: show (or, with --execute, run) the next action")
    p_exec.add_argument("investigation", help="investigation slug")
    p_exec.add_argument("--execute", action="store_true",
                        help="actually run the top action when it is side-effect-free (else print its gated command)")
    p_exec.add_argument("--autonomous", action="store_true",
                        help="also run a CONSEQUENTIAL top action (seed a study / run a simulation) and record the outcome")
    p_exec.add_argument("--ai-decide", action="store_true",
                        help="let an AI decider drive an open expert decision (needs $VIVA_DECIDER_CMD; implies --autonomous)")

    p_sv = sub.add_parser("set-verdict", help="author a canonical verdict on a study")
    p_sv.add_argument("study", help="study slug")
    p_sv.add_argument("verdict", choices=VERDICT_VOCAB)
    p_sv.add_argument("--rationale", default=None)

    p_rel = sub.add_parser("relate", help="author an epistemic relation downstream <- upstream")
    p_rel.add_argument("downstream", help="the study whose prerequisite is being labelled")
    p_rel.add_argument("upstream", help="the study it relates to")
    p_rel.add_argument("--relation", choices=EPISTEMIC_RELATIONS, default="builds-on")

    p_conn = sub.add_parser("connect", help="enrich every bare prerequisite across an investigation")
    p_conn.add_argument("investigation", help="investigation slug")
    p_conn.add_argument("--relation", choices=EPISTEMIC_RELATIONS, default="builds-on")

    p_crank = sub.add_parser("crank", help="turn the discovery crank: frontier + next action + closure")
    p_crank.add_argument("investigation", help="investigation slug")

    p_res = sub.add_parser("resolve", help="record a resolution that discharges a frontier item")
    p_res.add_argument("investigation", help="investigation slug")
    p_res.add_argument("kind", choices=RESOLUTION_KINDS, help="the frontier-item kind to discharge")
    p_res.add_argument("target", help="the item's target, as the crank prints it (e.g. 'claim:og-x' or a study slug)")
    p_res.add_argument("--rationale", required=True, help="why the item is discharged (the adjudication / bridge)")
    p_res.add_argument("--by", default=None, help="the artifact that resolves it (e.g. study#finding)")

    p_rev = sub.add_parser("revise", help="approve, reject, or apply a model-revision proposal (rp:…)")
    p_rev.add_argument("investigation", help="investigation slug")
    p_rev.add_argument("proposal", help="the proposal id (rp:…)")
    g_rev = p_rev.add_mutually_exclusive_group(required=True)
    g_rev.add_argument("--approve", action="store_true")
    g_rev.add_argument("--reject", action="store_true")
    g_rev.add_argument("--apply", action="store_true", help="apply an approved proposal: child study + model version")
    p_rev.add_argument("--change", type=int, default=None, help="candidate change index (default: first derived)")
    p_rev.add_argument("--rationale", default=None, help="why (required for --approve/--reject)")

    p_gap = sub.add_parser("gap", help="review-tier capability gaps (cg:…): required mechanisms no catalog process provides")
    p_gap.add_argument("investigation", help="investigation slug")
    p_gap.add_argument("--propose", action="store_true", help="derive + persist capability gaps and list the open ones")
    p_gap.add_argument("--accept", metavar="CG", default=None, help="accept gap cg:… (needs --rationale)")
    p_gap.add_argument("--reject", metavar="CG", default=None, help="reject gap cg:… (terminal; needs --rationale)")
    p_gap.add_argument("--rationale", default=None, help="why (required)")
    p_viz = sub.add_parser("viz", help="review-tier edits of a declared figure (fig:<study>/<name>)")
    p_viz.add_argument("investigation", help="investigation slug")
    p_viz.add_argument("target", nargs="?", default=None,
                       help="the figure, fig:<study>/<name> (for --declare)")
    p_viz.add_argument("--declare", nargs="+", metavar="KEY=VALUE", default=None,
                       help="set address=… [render=…] [requires=a,b] on the entry")
    p_viz.add_argument("--propose", action="store_true",
                       help="derive + persist viz proposals and list the open ones")
    p_viz.add_argument("--accept", metavar="VP", default=None, help="accept proposal vp:… (needs --rationale)")
    p_viz.add_argument("--address", default=None, help="address for --accept (default: the suggested one)")
    p_viz.add_argument("--reject", metavar="VP", default=None, help="reject proposal vp:… (terminal; needs --rationale)")
    p_viz.add_argument("--rationale", default=None, help="why (required)")

    p_ingest = sub.add_parser("ingest-feedback",
                              help="compile a discovery_request.yaml: reopen + redirect an "
                                   "investigation from a scientist's reopening-feedback")
    p_ingest.add_argument("request", help="path to a discovery_request.yaml")
    p_ingest.add_argument("--write", action="store_true",
                          help="apply the mutations (default: dry-run, print the summary)")

    args = parser.parse_args(argv)
    ws = Path(args.workspace).resolve()

    if args.cmd in ("cycle", "status"):
        inv_path, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        result = _cycle_with_evidence(ws, args.investigation, inv_spec, studies)
        print(summarize(result))
        if args.cmd == "cycle" and not args.no_write:
            from . import evidence_integrity as _ei
            ev_state = _ei.reconcile_evidence_state(
                inv_spec, studies, _load_runs_by_study(ws, args.investigation, studies))
            persist_cycle(inv_path, inv_spec, result, studies=studies, evidence_state=ev_state,
                          viz_state=viz_inventory(ws, args.investigation, studies))
            print(f"persisted cycle_state -> {inv_path}")
        return 0

    if args.cmd == "ingest-feedback":
        import json as _json
        from . import feedback_compiler
        result = feedback_compiler.ingest_feedback(args.request, ws, write=args.write)
        print(_json.dumps(result["summary"], indent=2, default=str))
        if result["wrote"]:
            print(f"\nreopened {result['summary']['investigation']} + "
                  f"{len(result['new_studies'])} seeded studies")
        else:
            print("\n(dry-run; pass --write to apply)")
        return 0

    if args.cmd == "set-verdict":
        sp = studies_dir(ws) / args.study / "study.yaml"
        if not sp.exists():
            parser.error(f"no study.yaml at {sp}")
        rec = set_verdict(sp, args.verdict, args.rationale)
        print(f"{args.study}: verdict={rec['verdict']} source={rec['verdict_source']}")
        return 0

    if args.cmd == "relate":
        sp = studies_dir(ws) / args.downstream / "study.yaml"
        if not sp.exists():
            parser.error(f"no study.yaml at {sp}")
        _enrich_file(sp, [args.upstream], args.relation)
        print(f"{args.downstream} <-{args.relation}- {args.upstream}")
        return 0

    if args.cmd == "connect":
        report = connect_investigation(ws, args.investigation, args.relation)
        enriched, needs_relate = report["enriched"], report["needs_relate"]
        n = sum(len(v) for v in enriched.values())
        print(f"enriched {n} bare prerequisite(s) with '{args.relation}' across "
              f"{len(enriched)} studies:")
        for s, ups in enriched.items():
            print(f"  {s}: {', '.join(ups)}")
        if needs_relate:
            print("\nparent_studies-only dependencies — NOT auto-asserted (verify each is a "
                  "scientific edge, then author it explicitly):")
            for s, ups in needs_relate.items():
                for up in ups:
                    print(f"  discover --workspace {args.workspace} relate {s} {up} "
                          f"--relation {args.relation}")
        return 0

    if args.cmd == "resolve":
        inv_path = investigations_dir(ws) / args.investigation / "investigation.yaml"
        if not inv_path.exists():
            parser.error(f"no investigation.yaml at {inv_path}")
        rec = record_resolution(inv_path, args.kind, args.target, args.rationale, args.by)
        print(f"resolved [{rec['kind']}] {rec['target']}"
              + (f"  (by {rec['by']})" if rec.get("by") else "")
              + f"  — {rec['rationale']}")
        return 0

    if args.cmd == "gap":
        if args.accept and args.reject:
            parser.error("gap: pass only one of --accept / --reject")
        try:
            if args.reject:
                reject_capability_gap(ws, args.investigation, args.reject, rationale=args.rationale or "")
                print(f"rejected {args.reject} (terminal)")
            elif args.accept:
                accept_capability_gap(ws, args.investigation, args.accept, rationale=args.rationale or "")
                print(f"accepted {args.accept}; build it: discover execute {args.investigation} --autonomous")
            elif args.propose:
                inv_path, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
                result = _cycle_with_evidence(ws, args.investigation, inv_spec, studies)
                persist_cycle(inv_path, inv_spec, result, studies=studies,
                              viz_state=viz_inventory(ws, args.investigation, studies))
                for g in load_yaml_mapping(inv_path).get("capability_gaps") or []:
                    gst = g.get("status", "proposed") if isinstance(g, dict) else None
                    if gst in ("proposed", "needs_author", "failed"):
                        print(f"{g['id']}  {g.get('mechanism')}  "
                              f"[{'derivable' if g.get('derivable') else 'needs_author'}]"
                              + ("" if gst == "proposed" else f"  ({gst})"))
            else:
                parser.error("gap needs --propose, --accept CG, or --reject CG")
        except ValueError as e:
            parser.error(str(e))
        return 0

    if args.cmd == "viz" and (args.propose or args.accept or args.reject):
        try:
            if args.reject:
                reject_viz_proposal(ws, args.investigation, args.reject, rationale=args.rationale or "")
                print(f"rejected {args.reject} (terminal)")
            elif args.accept:
                prop = accept_viz_proposal(ws, args.investigation, args.accept,
                                           address=args.address, rationale=args.rationale or "")
                print(f"accepted {args.accept} -> {prop['history'][-1]['entry']}; "
                      f"render it: discover advance {args.investigation}")
            else:
                inv_path, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
                result = _cycle_with_evidence(ws, args.investigation, inv_spec, studies)
                persist_cycle(inv_path, inv_spec, result, studies=studies,
                              viz_state=viz_inventory(ws, args.investigation, studies))
                for p in load_yaml_mapping(inv_path).get("viz_proposals") or []:
                    if isinstance(p, dict) and p.get("status", "proposed") == "proposed":
                        sg = p.get("suggested") or {}
                        print(f"{p['id']}  {sg.get('form')}  {(p.get('source') or {}).get('observable')}  "
                              f"[{'needs_author' if sg.get('needs_author') else 'derivable'}]")
        except ValueError as e:
            parser.error(str(e))
            return 1
        return 0

    if args.cmd == "viz":
        if not args.declare or not args.target:
            parser.error("viz needs <target> --declare …, or --propose / --accept VP / --reject VP")
        decl_kv: dict = {}
        for kv in args.declare:
            k, sep, v = kv.partition("=")
            if not sep:
                parser.error(f"--declare expects KEY=VALUE, got {kv!r}")
            decl_kv[k.strip()] = [x.strip() for x in v.split(",") if x.strip()] if k.strip() == "requires" else v
        try:
            rec = declare_viz(ws, args.investigation, args.target, decl_kv, args.rationale or "")
        except ValueError as e:
            parser.error(str(e))
            return 1
        print(f"declared {args.target}: {', '.join(decl_kv)}  — {rec['rationale']}")
        return 0

    if args.cmd == "revise":
        inv_path = investigations_dir(ws) / args.investigation / "investigation.yaml"
        if not inv_path.exists():
            parser.error(f"no investigation.yaml at {inv_path}")
        if args.apply:
            try:
                res = apply_revision(ws, args.investigation, args.proposal, args.change)
            except ValueError as e:
                parser.error(str(e))
            if res["status"] != "applied":
                print(f"needs_build {args.proposal}  — {res['mechanism']}")
                return 1
            print(f"applied {args.proposal}  -> {res['study']} ({res['model_version']})")
            print(f"  run it: discover advance {args.investigation}")
            return 0
        if not (args.rationale or "").strip():
            parser.error("--rationale is required for --approve/--reject")
        try:
            prop = review_revision(inv_path, args.proposal,
                                   "approved" if args.approve else "rejected", args.rationale)
        except ValueError as e:
            parser.error(str(e))
        print(f"{prop['status']} {prop['id']}  — {args.rationale}")
        return 0

    if args.cmd == "crank":
        from . import discovery_engine
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        result = _cycle_with_evidence(ws, args.investigation, inv_spec, studies)
        print(discovery_engine.summarize(discovery_engine.crank(result, inv_spec)))
        return 0

    if args.cmd == "snapshot-validations":
        snap = snapshot_validations(ws, args.investigation)
        print(f"accepted validation snapshot: {len(snap)} record(s) "
              f"— later drift surfaces as stale_validation")
        return 0

    if args.cmd == "diagnose":
        from . import discovery_engine
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        result = _cycle_with_evidence(ws, args.investigation, inv_spec, studies)
        diag = discovery_engine.diagnose(result, inv_spec)
        print(discovery_engine.summarize_diagnosis(diag))
        return 0

    if args.cmd == "suggest":
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        result = _cycle_with_evidence(ws, args.investigation, inv_spec, studies)
        print(summarize_suggestions(result["suggestions"]))
        return 0

    if args.cmd == "opportunities":
        from . import discovery_engine
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        result = _cycle_with_evidence(ws, args.investigation, inv_spec, studies)
        opps = discovery_engine.opportunities(result, inv_spec)
        print(discovery_engine.summarize_opportunities(opps))
        return 0

    if args.cmd == "hypotheses":
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        specs = list(studies.values()) if isinstance(studies, dict) else list(studies or [])
        print(_summarize_hypotheses(inv_spec, specs))
        return 0

    if args.cmd == "reconcile-evidence":
        from . import evidence_integrity as _ei
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        runs_by_study = _load_runs_by_study(ws, args.investigation, studies)
        print(_summarize_evidence_reconciliation(
            _ei.reconcile_evidence_state(inv_spec, studies, runs_by_study)))
        return 0

    if args.cmd == "plan":
        from . import evidence_integrity as _ei, candidate_action as _ca
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        runs_by_study = _load_runs_by_study(ws, args.investigation, studies)
        ev_state = _ei.reconcile_evidence_state(inv_spec, studies, runs_by_study)
        from .model_architect import build_catalog
        result = run_cycle(inv_spec, studies, evidence_state=ev_state,
                           catalog=build_catalog(studies, ws))
        portfolio = _ca.select_portfolio(_ca.plan_candidate_actions(result, inv_spec, ev_state))
        print(_summarize_portfolio(portfolio))
        return 0

    if args.cmd == "evaluate":
        from . import evaluation as _ev, evidence_integrity as _ei
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        runs_by_study = _load_runs_by_study(ws, args.investigation, studies)
        ev_state = _ei.reconcile_evidence_state(inv_spec, studies, runs_by_study)
        cmp = _ev.compare_hypotheses(inv_spec, studies, ev_state)
        dmap = _ev.model_disagreement_map(inv_spec, studies)
        print(_summarize_evaluation(cmp, dmap))
        return 0

    if args.cmd == "delta":
        from . import evaluation as _ev, evidence_integrity as _ei
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        runs_by_study = _load_runs_by_study(ws, args.investigation, studies)
        ev_state = _ei.reconcile_evidence_state(inv_spec, studies, runs_by_study)
        print(_summarize_discovery_delta(_ev.discovery_delta(
            inv_spec, studies, ev_state,
            prev=(inv_spec.get("cycle_state") or {}).get("evaluation_snapshot"))))
        return 0

    if args.cmd == "design":
        from . import experiment_design as _ed
        _, inv_spec, _studies = load_cycle_inputs(ws, args.investigation)
        design = _ed.design_discriminating_study(inv_spec, args.study, args.require_control or None)
        print(_summarize_design(design, args.study))
        return 0

    if args.cmd == "build-model":
        from . import model_architect as _ma
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        catalog = _ma.build_catalog(studies)
        req = _ma.ModelBuildRequest(question_id=args.question, required_mechanisms=args.mechanism,
                                    required_observables=args.observable or None)
        routes = _ma.choose_acquisition_route(req, catalog)
        mv = _ma.model_version(_ma.routes_composite(routes), {})
        print(_summarize_model_build(req, catalog, routes, mv))
        return 0

    if args.cmd == "seed-study":
        from . import study_seeding as _ss
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        parent = studies.get(args.parent)
        if not isinstance(parent, dict):
            print(f"seed-study: parent study {args.parent!r} not found")
            return 1
        mc = {"params": {k: _coerce_scalar(v) for k, v in (kv.split("=", 1) for kv in args.param)}} if args.param else None
        study = _ss.build_seeded_study(parent, inv_spec, new_slug=args.slug,
                                       finding_id=args.from_finding, model_change=mc, title=args.title)
        gaps = _ss.is_run_ready(study)
        print(_summarize_seeded_study(study, gaps, args.autonomy))
        if args.write:
            if gaps:
                print("  NOT written — resolve run-readiness gaps first.")
                return 1
            path = _write_seeded_study(ws, args.investigation, args.slug, study)
            print(f"  wrote {path}")
            print(f"  run it: discover advance {args.investigation}  (or {run_command(args.slug, str(ws))})")
        return 0

    if args.cmd == "refine-study":
        from . import study_refinement as _sr
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        study = studies.get(args.study)
        if not isinstance(study, dict):
            print(f"refine-study: study {args.study!r} not found")
            return 1
        needs = _sr.refinement_needs(inv_spec, study)
        refined, changes = _sr.refine_study(study, needs=needs or None,
                                            higher_fidelity_composite=args.composite)
        print(_summarize_refinement(args.study, needs, changes, args.autonomy))
        if args.write:
            if not changes:
                print("  nothing to write — study already adequately rigorous.")
                return 0
            path = _apply_refinement(ws, args.investigation, args.study, refined)
            print(f"  applied to {path}")
            print(f"  re-run it: discover advance {args.investigation}")
        return 0

    if args.cmd == "complete":
        print(summarize_completion(build_completion_map(ws, args.investigation)))
        return 0

    if args.cmd == "advance":
        print(summarize_advance(advance_cycle(ws, args.investigation, autonomous=args.autonomous)))
        return 0

    if args.cmd == "execute":
        from . import discovery_engine
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        plan = discovery_engine.execute_plan(_cycle_with_evidence(ws, args.investigation, inv_spec, studies), inv_spec)
        print(discovery_engine.summarize_execute_plan(plan))
        autonomous = args.autonomous or args.ai_decide
        if (args.execute or autonomous) and plan:
            rec = execute_action(ws, args.investigation, autonomous=autonomous,
                                 decider=_env_decider if args.ai_decide else None)
            print(f"\n[{rec['status']}] {rec['action']} on {rec['source']}: "
                  f"{(rec.get('outcome') or {}).get('detail') or (rec.get('outcome') or {})}")
            if rec.get("status") == "executed" and "frontier_after" in rec:
                print(f"  frontier: {rec['frontier_before']} → {rec['frontier_after']} (re-cranked)")
        return 0

    return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
