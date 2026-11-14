"""Cycle orchestration — the write/wire layer that drives the RFC-0005/0006 ops.

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

Imports: depends on :mod:`discovery_engine.discover._common` and on the author
write-ops in :mod:`discovery_engine.discover.actions` (``execute_action`` and
``persist_cycle`` compose them). ``actions`` only depends back on this module
through a lazy in-function import, so the package stays acyclic.
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
from discovery_engine.phases.critique_report import critique_triggers
from discovery_engine.reporting.validation import ValidationContext, run_validators

from discovery_engine.discover._common import (
    VERDICT_VOCAB,
    _ruamel,
    _study_dir_on_disk,
    _viz_target,
)
from discovery_engine.discover.actions import (
    _cap_gap_load,
    _close_built_viz_proposals,
    _validate_extensions,
    accept_capability_gap,
    accept_viz_proposal,
    record_resolution,
    reject_capability_gap,
    reject_viz_proposal,
    review_revision,
)


def load_workspace_build_hook(ws_root: Path | str | None):
    """Import ``<pkg>.build_hook`` and call ``register_build_hook(registry)``; the hook is
    ``registry['build_capability']`` (``fn(gap, ws_root) -> {status, process, detail, gap_id}``).
    Same package discovery as ``register_evaluators``. None if absent; a broken hook is skipped."""
    import sys
    from discovery_engine.study.evaluator import _workspace_evaluator_packages
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
    from discovery_engine.phases import evaluation as _ev
    from discovery_engine.phases.hypotheses import anomalies as _anoms
    from discovery_engine.phases.model_architect import build_catalog
    from discovery_engine.phases.model_revision import propose_model_revisions
    mvs = project_model_versions(studies_by_slug, inv_spec.get("model_versions"))
    evaluation = _ev.compare_hypotheses(inv_spec, studies_by_slug, evidence_state)
    from discovery_engine.phases.model_revision import _contradicted_studies
    bad = _contradicted_studies(evidence_state, evaluation)  # evidence problem -> REPAIR, not revise
    bundle["signals"] = {
        "model_disagreements": [r for r in _ev.model_disagreement_map(inv_spec, studies_by_slug)
                                if r.get("study") not in bad],
        "anomalies": [a for a in _anoms(inv_spec or {}, _ev._keyed_specs(studies_by_slug))
                      if a.get("study") not in bad],
        # A verdict/confidence that outruns its evidence -> an advisory critique trigger. A study
        # already flagged `bad` (contradicted -> REPAIR) is excluded, mirroring the other signals.
        "verdict_overreach": [t for t in critique_triggers(list(studies_by_slug.values()))
                              if t.get("study") not in bad],
    }
    bundle["revision_proposals"] = propose_model_revisions(
        inv_spec, studies_by_slug, evaluation, evidence_state, mvs,
        catalog if catalog is not None else build_catalog(studies_by_slug))
    if viz_state:  # hermetic: no harvested viz state -> no viz proposals
        from discovery_engine.viz.proposals import propose_visualizations
        bundle["viz_proposals"] = propose_visualizations(
            inv_spec, studies_by_slug, bundle, viz_state, viz_catalog)
    # Always assessed. Headless (no catalog): scope_of is None for every address, so an unresolved
    # port is `not_assessed` (no false alarm) while an AUTHORED `scope:` is still honored.
    from discovery_engine.viz.appropriateness import investigation_viz_appropriateness
    bundle["viz_appropriateness"] = investigation_viz_appropriateness(
        studies_by_slug, _viz_scope_of(viz_catalog))
    from discovery_engine.feedback.capability_gaps import propose_capability_gaps
    # The catalog (carrying `capability` + `builds`) is the same one the acquisition route reads.
    # On the live ws-aware path (_cycle_with_evidence) an INJECTED catalog is built with ws_root
    # (`build_catalog(studies, ws_root)`) — it RESOLVES each composite, so a gap fires on a
    # mechanism whose composite genuinely does not import, not a lexical guess. The no-ws fallback
    # below is the pure projection's display catalog (headless / no filesystem) and is lexical by
    # design; it never drives an autonomous build (that path always supplies the resolved catalog).
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
    from discovery_engine.phases import evidence_integrity as _ei
    ev = _ei.reconcile_evidence_state(
        inv_spec, studies, _load_runs_by_study(Path(ws_root), inv_slug, studies))
    from discovery_engine.phases.model_architect import build_catalog
    viz = viz_inventory(ws_root, inv_slug, studies)  # the one impure viz harvest
    return run_cycle(inv_spec, studies, evidence_state=ev,
                     catalog=build_catalog(studies, ws_root),  # resolved: enables composite swaps
                     viz_state=viz, viz_catalog=None)  # workbench catalog supplied by a later task

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

def build_cycle_state(result: dict, inv_spec: dict, *, model_versions: list | None = None,
                      studies: dict | None = None, evidence_state: dict | None = None) -> dict:
    """Project a **slim, actionable** summary of the cycle — the few dozen lines a
    human, the dashboard, or the next cycle actually needs, instead of the full
    regenerable projection. Carries the cycle's *outputs* that previously never fed
    forward: the value-ranked opportunities, suggestions, diagnosis, validator census,
    and drift. The full `knowledge`/`argument`/`narrative` are recomputable on demand
    via :func:`run_cycle`, so they are not dumped here."""
    from discovery_engine.projection import engine as de
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
    from discovery_engine.reporting.validation import stale_records
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
        from discovery_engine.phases import evaluation as _ev
        from discovery_engine.phases.hypotheses import anomalies as _anoms
        cmp = _ev.compare_hypotheses(inv_spec, studies, evidence_state)
        n_anoms = len(_anoms(inv_spec or {}, _ev._keyed_specs(studies)))
        state["evaluation_snapshot"] = _ev.snapshot_evaluation(
            cmp, evidence_state, model_versions or [], n_anomalies=n_anoms)
    return state

def _tab_freshness(inv_spec: dict, studies: dict) -> dict:
    """Per-tab freshness for the summary. The computed verdict is what ``investigation_status``
    already wrote on the executive -- not recomputed here."""
    from discovery_engine.projection.narrative_freshness import investigation_freshness
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
    from discovery_engine.reporting.validation import _DEPRECATED_INV_FIELDS, _RETIRED_INV_CACHES
    path = Path(inv_path)
    on_disk = load_yaml_mapping(path) if path.exists() else dict(inv_spec)
    for dead in (*_RETIRED_INV_CACHES, *_DEPRECATED_INV_FIELDS):
        on_disk.pop(dead, None)  # retire bloat caches + deprecated fields (0 readers)
    # the prior evaluation snapshot, before build_cycle_state overwrites cycle_state —
    # detect_discoveries diffs it against the new one to record verdict flips (FR-08.02)
    _prev_snapshot = ((on_disk.get("cycle_state") or {}).get("evaluation_snapshot")
                      if studies is not None else None)
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
        from discovery_engine.feedback.capability_gaps import close_built_capability_gaps
        from discovery_engine.phases.model_architect import build_catalog
        close_built_capability_gaps(on_disk["capability_gaps"],
                                    catalog if catalog is not None else build_catalog(studies), studies)
    on_disk["cycle_state"] = build_cycle_state(result, inv_spec, model_versions=mvs, studies=studies,
                                               evidence_state=evidence_state)
    if studies is not None:
        _evaluate_applied_proposals(on_disk, studies,
                                    (on_disk["cycle_state"].get("evaluation_snapshot") or {}).get("fingerprint"))
    if studies is not None:  # per-tab freshness against the post-renew on-disk spec
        on_disk["cycle_state"]["freshness"] = _tab_freshness(on_disk, studies)
    if studies is not None:
        # FR-08.02: a verdict that flipped to a settled outcome on verified evidence this
        # cycle is recorded as a FindingCandidate — an evidence fact. It becomes a
        # Discovery only via a reviewed promotion (`discover review-findings`), which
        # attaches a novelty/significance assessment; nothing mints a Discovery directly
        # from a flip.
        from discovery_engine.phases import evaluation as _evm
        from discovery_engine.phases.discovery_record import detect_finding_candidates
        _new_snap = (on_disk["cycle_state"] or {}).get("evaluation_snapshot") or {}
        # A flip diffed against a snapshot computed under a different grader/resolver
        # *version* is a provenance artifact (method drift) — re-baseline, don't surface it.
        # A flip driven by new measured evidence under the SAME evaluator is a genuine
        # result and DOES surface (inputs_hash alone must not imply stale science). No-op
        # for a pre-versioning (0.4.x) prev snapshot, so existing behavior is unchanged.
        _nv = _new_snap.get("versions") or {}
        _stale = _evm.snapshot_is_stale(_prev_snapshot, grader=_nv.get("grader"),
                                        resolver=_nv.get("resolver"),
                                        inputs_hash=_new_snap.get("inputs_hash"))
        _cands = [] if _stale else detect_finding_candidates(_prev_snapshot, _new_snap, studies,
                                    fingerprint=_new_snap.get("fingerprint"))
        if _cands:
            _have = [c for c in on_disk.get("finding_candidates") or [] if isinstance(c, dict)]
            _ids = {c.get("id") for c in _have}
            for c in _cands:
                if c["id"] not in _ids:  # content-addressed: idempotent across re-persists
                    _have.append(c)
                    _ids.add(c["id"])
            on_disk["finding_candidates"] = _have
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
    from discovery_engine.phases import evaluation as _ev
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
    from discovery_engine.phases.model_architect import bind_model_versions, resolve_composite
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
    # Idempotency across ALL sources: a proposal already promoted to a child study (its id
    # recorded in some study's seeded_from.proposal_id) is not re-proposed — otherwise an
    # autonomous advance would re-seed it every crank. (The refuted-finding path also keys on
    # seeded_from.finding below, for children written via the finding path.)
    _seeded_proposal_ids = {
        sf.get("proposal_id")
        for sp in (studies or {}).values() if isinstance(sp, dict)
        for sf in [sp.get("seeded_from") or {}] if sf.get("proposal_id")
    }

    def add(sid, title, motivation, parent, source, awaiting):
        if not sid or sid in seen or sid in _seeded_proposal_ids:
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

    from discovery_engine.growth.seed_from_followup import build_child_seed_from_finding
    # Idempotency: a refuted finding already promoted to a child study (its id recorded in
    # some study's seeded_from.finding) is NOT re-proposed — otherwise advance would seed it
    # every crank. This is what lets the generative loop terminate instead of streaming
    # proposals forever.
    _seeded_findings = {
        sf.get("finding")
        for sp in (studies or {}).values() if isinstance(sp, dict)
        for sf in [sp.get("seeded_from") or {}] if sf.get("finding")
    }
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
            if fid in _seeded_findings:
                continue      # already promoted to a child study — don't re-propose (idempotent)
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
                out[-1]["finding_id"] = fid   # so the `seed` op can promote it (write_child_study)
                # FR-07.04: diagnose model-error vs hypothesis-error and steer WHICH seed
                # we emit (a model-revision vs a hypothesis-revision study).
                from discovery_engine.phases.model_error import (
                    diagnose_refutation, steer_refutation_proposal)
                steer_refutation_proposal(out[-1], diagnose_refutation(slug, f, inv_spec, studies))
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
                from discovery_engine.study.run_registry import list_runs
                rows = list_runs(db) or []
                if rows:
                    last = rows[-1]
                    return str(last.get("run_id") or last.get("name") or "") or None
            except Exception:  # noqa: BLE001
                return None
    return None

def execute_action(ws_root: Path | str, inv_slug: str, *, autonomous: bool = False,
                   runner=None, decider=None, target: str | None = None,
                   action: str | None = None, render_hook=None, build_hook=None,
                   worktree: Path | str | None = None, policy=None) -> dict:
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
    from discovery_engine.projection import engine as de
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

    # One autonomy gate for every consequential op: resolve the legacy `autonomous` bool
    # (or an explicit AutonomyPolicy) into the single policy object. `autonomous` below then
    # reads from it, so all existing consequence/decider checks consult one source; the
    # run/render/seed gates additionally honor `approved_ids` (per-item HITL approval) and
    # the allowlist, which the bare bool could not express.
    from discovery_engine.discover._policy import AutonomyPolicy, decision_record
    pol = AutonomyPolicy.resolve(policy, autonomous)
    autonomous = pol.is_autonomous

    if top["auto_safe"]:
        snap = snapshot_validations(ws_root, inv_slug)
        rec["status"] = "executed"
        rec["outcome"] = {"ok": True, "detail": f"snapshot-validations: {len(snap)} records accepted"}
    elif top["consequence"] in ("simulation", "simulation+study"):
        if not pol.allows(consequence=top["consequence"], target=top["source"]):
            rec["status"] = "gated"
            rec["outcome"] = {"ok": None, "detail": "consequential — re-run with --autonomous "
                              "(or approve this item in the policy) to execute"}
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
        if not pol.allows(consequence="render", target=top["source"]):
            rec["status"] = "gated"
            rec["outcome"] = {"ok": None, "detail": "consequential — re-run with --autonomous "
                              "(or approve this item in the policy) to execute"}
        else:
            vt = _viz_target(top["source"])
            entry = _viz_entry(studies, *vt) if vt else None
            if entry is None or not _viz_resolvable(entry):
                rec["status"] = "gated"
                rec["outcome"] = {"ok": None, "detail": "figure is not renderable (no resolvable "
                                  "address/render) — declare one: discover viz <inv> "
                                  f"{top['source']} --declare address=… --rationale '…'"}
            else:
                from discovery_engine.viz.freshness import normalize_entry
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
                    from discovery_engine.viz.freshness import read_meta
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
    elif top["consequence"] == "authoring":
        # Promote a ready, seed-bearing proposal into a real study.yaml — the write-back
        # that CLOSES the generative loop. Policy-gated exactly like a simulation: resolve
        # the proposal from the cycle's suggestions, then write the child via the atomic
        # write_child_study. On the next crank the new study raises its own `test` op and
        # advance runs it, so "a refuted finding proposes the next study" becomes "runs it".
        if not pol.allows(consequence="authoring", target=top["source"]):
            rec["status"] = "gated"
            rec["outcome"] = {"ok": None, "detail": "authoring — re-run with --autonomous "
                              "(or approve this item in the policy) to write the study"}
        else:
            prop = next((s for s in suggest_studies(cyc, inv_spec, studies)
                         if isinstance(s, dict) and s.get("id") == top["source"]), None)
            if prop is None or not prop.get("parent_study"):
                rec["status"] = "gated"
                rec["outcome"] = {"ok": None, "detail": "proposal not found / no parent — "
                                  "run `discover cycle` first"}
            else:
                from discovery_engine.growth.seed_from_followup import SeedSource, write_child_study
                try:
                    res = write_child_study(
                        ws_root, prop["parent_study"],
                        SeedSource(proposal={"id": prop["id"], "title": prop.get("title"),
                                             "motivation": prop.get("motivation")},
                                   finding_id=prop.get("finding_id"),
                                   family=prop.get("source") or "proposal"))
                    # Register the child as a member so the NEXT crank includes it — promotion
                    # must put the study IN the cycle, not just on disk, or the loop can't chain.
                    # Append to whichever membership field the investigation declares.
                    _inv_disk = load_yaml_mapping(inv_path)
                    _key = "members" if isinstance(_inv_disk.get("members"), list) else "studies"
                    _members = list(_inv_disk.get(_key) or [])
                    _existing = {m.get("study") if isinstance(m, dict) else m for m in _members}
                    if res["new_slug"] not in _existing:
                        _members.append(res["new_slug"])
                        _inv_disk[_key] = _members
                        save_yaml_atomic(inv_path, _inv_disk)
                    rec["status"] = "executed"
                    rec["command"] = f"seed-study {prop['parent_study']} -> {res['new_slug']}"
                    rec["outcome"] = {"ok": True, "new_slug": res["new_slug"],
                                      "detail": f"seeded study {res['new_slug']} from "
                                                f"{prop['parent_study']} (run it on the next crank)"}
                except FileExistsError:
                    rec["status"] = "gated"
                    rec["outcome"] = {"ok": None, "detail": "child study already exists — already seeded"}
                except Exception as e:  # noqa: BLE001 — a malformed seed never crashes the cycle
                    rec["status"] = "failed"
                    rec["outcome"] = {"ok": False, "detail": f"seed write failed: {type(e).__name__}: {e}"}
    elif top["consequence"] == "reopen":
        # Re-enter the loop on staged human feedback — the move that folds the old
        # `ingest-feedback` CLI into advance. Policy-gated like authoring: apply the staged
        # CompiledRequest via reconcile_and_reopen, write the reopened lifecycle fields (and
        # any new studies), and record the feedback id in applied_feedback so it is not
        # re-applied (idempotent).
        if not pol.allows(consequence="reopen", target=top["source"]):
            rec["status"] = "gated"
            rec["outcome"] = {"ok": None, "detail": "reopen — re-run with --autonomous "
                              "(or approve this item in the policy) to apply the feedback"}
        else:
            fb = next((f for f in inv_spec.get("pending_feedback") or []
                       if isinstance(f, dict) and f.get("id") == top["source"]), None)
            if fb is None:
                rec["status"] = "gated"
                rec["outcome"] = {"ok": None, "detail": "staged feedback not found — run `discover cycle` first"}
            else:
                from discovery_engine.feedback.compiler import reconcile_and_reopen
                try:
                    res = reconcile_and_reopen(fb, inv_spec, studies)
                    reopened = res["inv_spec"]
                    _inv_disk = load_yaml_mapping(inv_path)
                    _inv_disk["status"] = reopened.get("status")
                    if reopened.get("executive") is not None:
                        _inv_disk["executive"] = reopened["executive"]
                    if reopened.get("lifecycle_history") is not None:
                        _inv_disk["lifecycle_history"] = reopened["lifecycle_history"]
                    _applied = [a for a in _inv_disk.get("applied_feedback") or [] if isinstance(a, dict)]
                    _applied.append({"feedback": top["source"],
                                     "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")})
                    _inv_disk["applied_feedback"] = _applied
                    save_yaml_atomic(inv_path, _inv_disk)
                    # write any studies the reopen seeded (mirrors ingest_feedback --write)
                    for _slug, _spec in (res.get("new_studies") or {}).items():
                        _sdir = studies_dir(Path(ws_root)) / _slug
                        _sdir.mkdir(parents=True, exist_ok=True)
                        save_yaml_atomic(_sdir / "study.yaml", _spec)
                    rec["status"] = "executed"
                    rec["command"] = f"reconcile-and-reopen {top['source']}"
                    rec["outcome"] = {"ok": True, "detail": f"reopened the investigation on "
                                      f"feedback {top['source']} (re-enters the loop)"}
                except Exception as e:  # noqa: BLE001 — malformed feedback never crashes the cycle
                    rec["status"] = "failed"
                    rec["outcome"] = {"ok": False, "detail": f"reopen failed: {type(e).__name__}: {e}"}
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
        elif not pol.allows(consequence="build", target=top["source"]):
            # `build` is a consequential tier like run/render/authoring/reopen: honor the
            # unified AutonomyPolicy (allowlist + per-item approved_ids), not just the bare
            # `autonomous` bool, so "autonomous but never build" and HITL per-item approval work.
            rec["status"] = "gated-human"
            rec["outcome"] = {"ok": None, "detail": "review — needs a human accept/reject (or --autonomous)"}
        elif status == "accepted":
            hook = build_hook if build_hook is not None else load_workspace_build_hook(ws_root)
            if hook is None or not (disk.get("accept") or {}).get("rationale"):
                rec["status"] = "gated-human"
                rec["outcome"] = {"ok": None, "detail": "accepted but no build_hook (or no accept rationale) — "
                                  "register <pkg>.build_hook.register_build_hook"}
            else:
                from discovery_engine.feedback.capability_gaps import build_capability_gap
                from discovery_engine.phases.model_architect import build_catalog
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

    # Per-decision provenance: record WHY this op was (or was not) allowed to run, under
    # which policy version and actor — the audit the roadmap's autonomy log calls for.
    rec["decision"] = decision_record(
        pol,
        actor=("discovery-ai" if decider is not None and pol.is_autonomous
               else "autonomous" if pol.is_autonomous else "human-approved"),
        consequence=top["consequence"], target=top["source"],
        rationale=(rec.get("outcome") or {}).get("detail") or "", result=rec["status"])

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
    entry = {"action": record["action"], "source": record["source"],
             "command": record["command"], "status": record["status"],
             "outcome": (record.get("outcome") or {}).get("detail")
             or ("ok" if (record.get("outcome") or {}).get("ok") else "see runner output"),
             "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds")}
    if record.get("decision"):
        entry["decision"] = record["decision"]
    log.append(entry)
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
    # Parked ontology (engine cycle-correctness): a study flagged ``parked: true`` is a
    # deliberately-deferred future direction, so the cycle must never auto-run it — advance_cycle
    # only dispatches run-ready studies. Report it NOT ready with a parked gap (NOT "incomplete":
    # it is out of scope this cycle, distinct from a study whose run spec is missing fields).
    # Short-circuits before the expensive evaluator-registry load.
    if study_spec.get("parked"):
        return {"ready": False, "complete": False, "parked": True,
                "gaps": ["parked: deferred future direction (not run this cycle)"],
                "already_run": False, "composite_resolves": True, "measures_resolvable": True,
                "observables": {"required": [], "declared": [], "missing": []},
                "unevaluable_tests": [], "unresolved_measures": [],
                "has_emitter": False, "has_visualizations": False, "visualizations_rendered": False,
                "has_tests": False, "has_contracts": False}
    from discovery_engine.study.evaluator import load_workspace_derived_scalars, required_observables_for_measure
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
    # Ground truth from the recorded run(s): a test whose LATEST grade is in the `agent` (SKIP)
    # bucket *because its measure was unresolvable* never resolved its gate — e.g. its derived-scalar
    # computer was absent from the run env. This catches an unresolved measure even when THIS
    # readiness env's registry is empty (so the registry check below can't confirm it), without
    # false-flagging a test that later graded by code, and without mislabeling a SKIP that was for a
    # different reason (bad op / missing pass_if) as a missing computer.
    _latest_grade: dict = {}
    for _r in (study_spec.get("runs") or []):
        if isinstance(_r, dict):
            for _tn, _ent in (_r.get("computed_outcomes") or {}).items():
                if isinstance(_ent, dict) and _ent.get("evaluated_by"):
                    _latest_grade[_tn] = _ent            # later runs overwrite earlier → last wins
    _graded_skip = {tn for tn, ent in _latest_grade.items()
                    if ent.get("evaluated_by") == "agent"
                    and "resolv" in str(ent.get("reason") or "").lower()}
    for t in tests:
        if not isinstance(t, dict):
            continue
        m = t.get("measure")
        if not isinstance(m, dict):
            m = {}  # a bare-string measure (a composite-observable function) needs no declared
                    # readout — it is computed from the raw trajectory; treat as readiness-neutral.
        # The grader resolves a measure's identifier as `path` OR `field` (evaluator/config.py,
        # evaluator/__init__.py) — a `derived_scalar` conventionally declares `field`, not `path`.
        # Readiness must honor the same fallback, else a field-based derived measure is
        # mis-flagged "no measure.path" (sending the author to add a path) when the real gap is an
        # unregistered computer (the `unresolved_measures` branch below).
        kind = m.get("kind")
        path = m.get("path") or m.get("field")
        if kind and kind not in ("config_value", "cross_run", "run_delta") and not path:
            unevaluable.append(t.get("name") or "<unnamed>")
        if kind in ("derived_scalar", "derived") and path and _registry_trustworthy \
                and _registry.get(path) is None:
            unresolved_measures.append(t.get("name") or path)
        _tname = t.get("name")
        if kind in ("derived_scalar", "derived") and _tname and _tname in _graded_skip \
                and _tname not in unresolved_measures:
            unresolved_measures.append(_tname)
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
    from discovery_engine.phases.model_architect import _top_importable, resolve_composite
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

def _viz_entry(studies: dict, study: str, name: str) -> dict | None:
    for e in (studies.get(study) or {}).get("visualizations") or []:
        if isinstance(e, dict) and e.get("name") == name:
            return e
    return None

def _viz_resolvable(entry: dict) -> bool:
    """A declared entry the render executor can act on: a `class` or `command` kind. Never
    a broken_stub (no identity) or a static figure with no `render:` recipe."""
    from discovery_engine.viz.freshness import normalize_entry
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
        from discovery_engine.viz.freshness import normalize_entry, read_meta, stamp_meta
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
_ADVANCE_OPS = ("test", "branch", "build_viz", "refine_viz", "seed", "build_process", "reopen")

def _advance_study_for(source, studies: dict) -> str | None:
    """The on-disk study an advance op would run, or None (gated). The applied child of a
    revision runs as a plain ``test`` op on ``claim:<child>`` -- applied proposals are off the
    frontier, so there is deliberately no ``rp:`` resolution here: an unapplied proposal
    resolves to nothing and is never run."""
    src = str(source)
    slug = src.split(":", 1)[1] if src.startswith("claim:") else src
    return slug if slug in studies else None

def _arm_mvs(slug: str, spec: dict) -> dict:
    from discovery_engine.phases.model_architect import bind_model_versions
    try:
        return bind_model_versions({slug: spec}).get(slug) or {}
    except Exception:  # noqa: BLE001 -- a malformed arm must not break advance
        return {}

def _run_mv_id(run: dict, arms: dict) -> str | None:
    """A run's model-version id: the id stamped at run time, else (legacy run) derived from
    its recorded params + its arm's composite."""
    from discovery_engine.phases.model_architect import model_version
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

def _run_names(sdir) -> set:
    if sdir is None:
        return set()
    return {r.get("name") for r in load_yaml_mapping(sdir / "study.yaml").get("runs") or []
            if isinstance(r, dict)}

def _dispatch_classify(p: dict, studies: dict, ws_root, inv_slug: str,
                       build_hook=None) -> tuple[str, str | None, list]:
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
    if op == "reopen":
        # Staged human feedback with a reopen action is dispatchable: advance applies it via
        # reconcile_and_reopen under the policy. The frontier only raises it while it is
        # unapplied (applied_feedback drops it), so if it is on the plan it is promotable. It
        # mutates the investigation lifecycle, claiming no study slug.
        return ("run", None, [])
    if op == "seed":
        # A proposed_study (the generative loop's ready, seed-bearing child) is dispatchable:
        # advance promotes it via write_child_study under the policy. The frontier only raises
        # one when it carries a parent + seed (and idempotency drops already-written ones), so
        # it is genuinely promotable here. It claims NO existing study slug — it writes a NEW
        # study — so `slug` is None (the per-run seen_slugs guard does not apply to it).
        return ("run", None, [])
    if op == "build_process":
        # A capability-gap build is dispatchable only when the gap is ACCEPTED (a human put a
        # rationale on record) AND a build_hook is available — the engine never builds itself,
        # it drives the workspace's injected hook, then verifies coverage. It writes a composite,
        # not a run, so it claims no study slug.
        try:
            _, _, gap = _cap_gap_load(ws_root, inv_slug, p["source"])
        except Exception:  # noqa: BLE001 — a malformed/absent gap is simply not dispatchable
            return ("skip", None, [])
        if (not isinstance(gap, dict) or gap.get("status") != "accepted"
                or not (gap.get("accept") or {}).get("rationale")):
            return ("skip", None, [])
        if (build_hook or load_workspace_build_hook(ws_root)) is None:
            return ("skip", None, [])
        return ("run", None, [])
    if op in ("build_viz", "refine_viz"):
        vt = _viz_target(p["source"])
        entry = _viz_entry(studies, *vt) if vt else None
        if p["consequence"] != "render" or entry is None or not _viz_resolvable(entry):
            return ("skip", None, [])          # review-tier / unresolvable render stays gated
        from discovery_engine.viz.freshness import normalize_entry, read_meta
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
    from discovery_engine.projection import engine as de
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
            from discovery_engine.study import outcomes as study_outcomes
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
        from discovery_engine.study import finding_observations
        finding_observations.populate_finding_observations(sdir)
    except Exception:  # noqa: BLE001 -- sync is best-effort, never fails an advance
        pass

def _pending_design_studies(inv_spec: dict, studies: dict) -> list[str]:
    """Member studies that carry a *discriminable* competing-hypothesis fork but have no
    discriminating child study yet. Pure — the DE-10 trigger for autonomous design.

    Skips, so advance never loops or over-designs:
    - a study whose ``<slug>-discriminating`` child already exists (idempotent); and
    - a study that is itself already a discriminating experiment — its competing
      hypotheses were produced BY a ``design``/``revision`` (so running *it* discriminates;
      designing a grandchild would be wrong)."""
    from discovery_engine.phases.hypotheses import _as_list, discriminating_hypotheses
    have = set(studies or {})
    _designed_origin = {"design", "revision"}
    origin_by_study: dict[str, set] = {}
    for h in _as_list((inv_spec or {}).get("hypotheses")):
        if isinstance(h, dict) and h.get("study"):
            origin_by_study.setdefault(h["study"], set()).add(
                str((h.get("origin") or {}).get("kind") or ""))
    out: list[str] = []
    for slug, info in sorted(discriminating_hypotheses(inv_spec or {}).items()):
        if not (isinstance(info, dict) and info.get("discriminable")):
            continue
        if slug not in have or f"{slug}-discriminating" in have:
            continue
        if origin_by_study.get(slug, set()) & _designed_origin:
            continue   # already a discriminating/revision study — running it discriminates
        out.append(slug)
    return out

def advance_cycle(ws_root: Path | str, inv_slug: str, *, autonomous: bool = False,
                  runner=None, max_runs: int = 12, render_hook=None,
                  worktree: Path | str | None = None, policy=None, build_hook=None) -> dict:
    """The cycle's ADVANCE phase — build-if-needed and RUN the open questions, so turning
    the crank moves the science forward instead of only describing it.

    Repeatedly: crank, take the top **run-ready** ``test`` op (an ``open_question`` /
    ``pending_gate`` on an existing study), run it via :func:`execute_action`, re-crank —
    until no runnable open work remains, a study needs BUILDING (not run-ready: its run
    spec is incomplete, per :func:`run_readiness`), or only human-gated items are left. A
    ``test`` op is tried once (``seen``), so a run that only SKIPs its gates doesn't loop.

    This makes "run the open questions" a defined phase of a cycle, not an afterthought.
    Without ``autonomous`` it reports what it WOULD run (dry). Returns
    ``{runs, needs_build, frontier_before, frontier_after}``.

    Autonomy is one object: pass an :class:`AutonomyPolicy` as ``policy`` (its ``mode``
    decides dry-vs-execute and its ``budget`` caps the run count), or the legacy
    ``autonomous`` bool, which is resolved into the same policy."""
    from discovery_engine.discover._policy import AutonomyPolicy
    from discovery_engine.projection import engine as de
    pol = AutonomyPolicy.resolve(policy, autonomous)
    if pol.budget is not None:
        max_runs = min(max_runs, pol.budget)
    ran: list[dict] = []
    needs_build: list[dict] = []
    seen: set = set()
    seen_slugs: set = set()
    gated_ops: set = set()      # (op, source) already gated: not retried, but they do not claim the study
    _, inv0, st0 = load_cycle_inputs(ws_root, inv_slug)
    before = de.closure(_cycle_with_evidence(ws_root, inv_slug, inv0, st0), inv0)["open_items"]
    # DE-10: before running, autonomously AUTHOR a discriminating study for any member that
    # carries a discriminable competing-hypothesis fork and has no discriminating child yet —
    # gated by the autonomy policy, exactly like a seed. The materialized child is registered,
    # so the run loop below raises and runs its own `test` op on the next crank. Idempotent:
    # _pending_design_studies skips a study once its child exists, so advance never loops on it.
    designed: list[dict] = []
    for _dslug in _pending_design_studies(inv0, st0):
        _dtgt = f"design:{_dslug}"
        if not pol.is_autonomous:
            designed.append({"study": _dslug, "status": "would-design (pass --autonomous to author)"})
            continue
        if not pol.allows(consequence="authoring", target=_dtgt):
            designed.append({"study": _dslug, "status": "gated"})
            continue
        try:
            from discovery_engine.discover.actions import materialize_design_study
            _rep = materialize_design_study(ws_root, inv_slug, _dslug)
            designed.append({"study": _dslug, "status": "designed",
                             "child": _rep["child_slug"], "design_id": _rep["design_id"]})
        except ValueError as _e:   # no discriminable fork after all — never crash an advance
            designed.append({"study": _dslug, "status": "skipped", "detail": str(_e)})
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
            state, slug, gaps = _dispatch_classify(p, studies, ws_root, inv_slug, build_hook=build_hook)
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
        if not pol.is_autonomous:
            seen.add(target)
            seen_slugs.add(target_slug)
            ran.append({"target": target, "status": "would-run (pass --autonomous to execute)"})
            break
        sdir = _study_dir_on_disk(ws_root, inv_slug, target_slug) if target_slug else None
        before_runs = _run_names(sdir) if sdir else []
        rec = execute_action(ws_root, inv_slug, policy=pol, runner=runner, target=target,
                             action=target_op, render_hook=render_hook, worktree=worktree,
                             build_hook=build_hook)
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
        if target_op in ("seed", "build_process", "reopen"):
            # seed wrote a NEW study; build_process ran the workspace build_hook and wrote a
            # composite — neither is a study run. Mark seen and re-crank: a study that needed the
            # just-built mechanism becomes run-ready, or the new study's `test` op is dispatched.
            # A failed/gated one is not retried this advance.
            if rec["status"] == "executed":
                seen.add(target)
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
    return {"runs": ran, "needs_build": needs_build, "designed": designed,
            "frontier_before": before, "frontier_after": after}

def build_completion_map(ws_root: Path | str, inv_slug: str) -> dict:
    """The single 'what's left to complete this cycle' view: the gate state, the
    verification debt (gates met by verdict only), the expert decisions that gate the
    next studies, and the ordered next-step commands. Pure read — assembles the frontier,
    closure, suggestions, and the execution plan into one map."""
    from discovery_engine.projection import engine as de
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

def snapshot_validations(ws_root: Path | str, slug: str) -> dict:
    """Persist the current validation records' signatures as the investigation's
    accepted ``validation_snapshot`` (RFC-0007 Phase 4). Later cycles compare a fresh
    re-derivation against it; drift surfaces as a ``stale_validation`` frontier item
    (an accepted judgment an upstream model/run change moved past). Returns the
    snapshot. Like :func:`persist_cycle`, re-reads on-disk so authored edits are not
    clobbered and writes only this regenerable cache."""
    from discovery_engine.reporting.validation import snapshot_of
    inv_path, inv_spec, studies = load_cycle_inputs(ws_root, slug)
    snap = snapshot_of(run_cycle(inv_spec, studies)["validations"])
    path = Path(inv_path)
    on_disk = load_yaml_mapping(path) if path.exists() else dict(inv_spec)
    on_disk["validation_snapshot"] = snap
    save_yaml_atomic(path, on_disk)
    return snap

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
    from discovery_engine.study import run_registry as _rr
    runs_by_study: dict[str, list] = {}
    for slug in studies:
        runs: list = []
        for base in (studies_dir(ws), investigations_dir(ws) / inv_slug / "studies"):
            db = base / slug / "runs.db"
            if db.exists():
                runs = _rr.list_runs(db)
                break
        runs_by_study[slug] = runs
    # Merge in runs recorded only in the workbench's workspace-wide run log
    # (.pbg/runs.jsonl, vivarium-workbench #612) so a study whose runs live only
    # there is still resolvable for evidence reconciliation. The per-study runs.db
    # is authoritative and wins on a run_id conflict.
    for slug, extra in _rr.runs_by_study_from_runlog(
            ws / ".pbg" / "runs.jsonl", set(studies)).items():
        existing = runs_by_study.setdefault(slug, [])
        have = {r.get("run_id") for r in existing if isinstance(r, dict)}
        for run in extra:
            rid = run.get("run_id")
            if rid not in have:
                existing.append(run)
                have.add(rid)
    return runs_by_study

def viz_inventory(ws_root, inv_slug: str, studies: dict) -> dict:
    """IMPURE (the one disk read the viz loop adds): per study, a row per declared
    ``visualizations[]`` entry ``{path, exists, meta, mtime, content_hash}``, plus
    ``_latest`` (latest run ``{run_id, completed_at}`` or None) and ``_orphans``
    (``viz/*.html`` on disk but not declared). Fed to ``run_cycle`` as data."""
    from discovery_engine.viz.freshness import _hash, normalize_entry, read_meta
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
