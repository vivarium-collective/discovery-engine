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
from .investigation_import import investigations_dir, studies_dir
from .knowledge_store import canonical_verdict, derive_knowledge, merge_knowledge
from .narrative import audit, narrate
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
                    "validation_finding", "stale_validation", "pending_gate")


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


# ---------------------------------------------------------------------------
# run_cycle — one invocation = one turn of the discovery cycle
# ---------------------------------------------------------------------------

def run_cycle(inv_spec: dict, studies_by_slug: dict[str, dict]) -> dict:
    """Project one full discovery cycle over an investigation + its studies.

    Pure: no disk I/O. Each derived projection is merged over its authored block
    (``inv_spec['knowledge']`` / ``inv_spec['argument']``) with the
    authored-wins / empty-never-clobbers rule, then narrate/audit/synthesize read
    the merged argument graph.
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
                            argument=argument, narrative=narrative, audit=gaps)

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
    return bundle


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

def build_cycle_state(result: dict, inv_spec: dict) -> dict:
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
    return {
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


def persist_cycle(inv_path: Path | str, inv_spec: dict, result: dict) -> dict:
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
    on_disk["cycle_state"] = build_cycle_state(result, inv_spec)
    save_yaml_atomic(path, on_disk)
    return on_disk["cycle_state"]


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

    return out


def _default_runner(command: str, ws_root: Path | str) -> dict:
    """Run a consequential executor command as a subprocess in the workspace, capturing
    its outcome. This is the real sim/seed invocation the autonomous bridge makes.

    The RUNNING interpreter's venv bin is prepended to the subprocess PATH so a bare
    console script in the command (e.g. ``vivarium-workbench``) resolves to THIS venv's
    binary — not some other venv earlier on PATH, which would run against a different
    workspace package and fail to find the composite (observed: a bare ``vivarium-workbench``
    resolved to a sibling venv and reported the study's composite 'not found')."""
    import os
    import subprocess
    import sys
    env = dict(os.environ)
    env["PATH"] = os.path.dirname(sys.executable) + os.pathsep + env.get("PATH", "")
    proc = subprocess.run(command, shell=True, cwd=str(ws_root), env=env,
                          capture_output=True, text=True, timeout=3600)
    return {"returncode": proc.returncode,
            "ok": proc.returncode == 0,
            "tail": (proc.stdout or proc.stderr or "")[-400:]}


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
                   runner=None, decider=None, target: str | None = None) -> dict:
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
    cyc = run_cycle(inv_spec, studies)
    before = de.closure(cyc, inv_spec)["open_items"]
    plan = de.execute_plan(cyc, inv_spec)
    if not plan:
        return {"status": "nothing-to-do", "frontier": before}
    # Default: the single highest-value move (plan[0]). With `target`, drive that specific
    # frontier item instead — e.g. an AI decider driving a decision that a higher-value
    # verification run would otherwise rank ahead of.
    top = plan[0] if target is None else next((p for p in plan if p["source"] == target), None)
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
                    rec["outcome"] = (runner or _default_runner)(command, ws_root)
                    rec["status"] = "executed" if rec["outcome"].get("ok") else "failed"
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
    else:  # review / human-only with no decider
        rec["status"] = "gated-human"
        rec["outcome"] = {"ok": None, "detail": f"{top['consequence']} — needs a human decision"}

    if rec["status"] == "executed":
        _append_executed(inv_path, rec)
        _, inv2, studies2 = load_cycle_inputs(ws_root, inv_slug)
        rec["frontier_before"] = before
        rec["frontier_after"] = de.closure(run_cycle(inv2, studies2), inv2)["open_items"]
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
    # after a run (observed: island/colony/eukaryogenesis tracked runs graded SKIP because the run
    # env-worker imported a stale venv without the merged `register_derived_scalars` evaluators).
    unresolved_measures: list[str] = []
    for t in tests:
        if not isinstance(t, dict):
            continue
        m = t.get("measure") or {}
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
    # every already-run study as un-runnable (observed on genesis: all 21 run studies).
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
    # declared-but-unrendered viz shows "No figures yet" (a silent gap; observed on eukaryogenesis,
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
    # on eukaryogenesis: all 5 baseline composites were ModuleNotFoundError while readiness said
    # ready). Resolve WITHOUT executing composite code (find_spec), and only when the workspace
    # package is importable at all, so a missing package isn't misread as a missing model.
    import importlib.util as _ilu
    composite_missing: str | None = None
    _baseline = study_spec.get("baseline") or []
    comp = (_baseline[0].get("composite")
            if _baseline and isinstance(_baseline[0], dict) else None) or study_spec.get("composite")
    if comp and isinstance(comp, str) and "." in comp:
        top, module = comp.split(".")[0], comp.rsplit(".", 1)[0]
        try:
            top_ok = _ilu.find_spec(top) is not None
        except Exception:  # noqa: BLE001 — a broken intermediate package isn't our assessment
            top_ok = False
        if top_ok:
            try:
                if _ilu.find_spec(module) is None:
                    composite_missing = comp
            except Exception:  # noqa: BLE001 — module (or intermediate) not importable → not built
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


def advance_cycle(ws_root: Path | str, inv_slug: str, *, autonomous: bool = False,
                  runner=None, max_runs: int = 12) -> dict:
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
    _, inv0, st0 = load_cycle_inputs(ws_root, inv_slug)
    before = de.closure(run_cycle(inv0, st0), inv0)["open_items"]
    for _ in range(max_runs):
        _, inv, studies = load_cycle_inputs(ws_root, inv_slug)
        plan = de.execute_plan(run_cycle(inv, studies), inv)
        target = None
        for p in plan:
            if p["action_type"] != "test" or p["source"] in seen:
                continue
            slug = p["source"].split(":", 1)[1] if str(p["source"]).startswith("claim:") else p["source"]
            spec = studies.get(slug)
            if not isinstance(spec, dict):
                continue
            if spec.get("runs"):
                # already ran: a lingering open_question / pending_gate here means the gate
                # SKIP'd or the verdict is only-by-roll-up — re-running won't resolve it (the
                # behavior test / evaluation needs attention, not another sim). Don't loop on it.
                seen.add(p["source"])
                continue
            rr = run_readiness(spec, ws_root)
            if rr["ready"]:
                target = p["source"]
                break
            needs_build.append({"study": slug, "gaps": rr["gaps"]})
            seen.add(p["source"])
        if target is None:
            break                                   # no runnable test op left
        seen.add(target)
        if not autonomous:
            ran.append({"target": target, "status": "would-run (pass --autonomous to execute)"})
            break
        rec = execute_action(ws_root, inv_slug, autonomous=True, runner=runner, target=target)
        ran.append({"target": target, "status": rec["status"],
                    "outcome": (rec.get("outcome") or {}).get("detail")})
        if rec["status"] != "executed":
            break
    _, inv2, st2 = load_cycle_inputs(ws_root, inv_slug)
    after = de.closure(run_cycle(inv2, st2), inv2)["open_items"]
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
    cyc = run_cycle(inv_spec, studies)
    clo = de.closure(cyc, inv_spec)
    front = de.frontier(cyc, inv_spec)
    plan = de.execute_plan(cyc, inv_spec)
    suggestions = [s for s in (cyc.get("suggestions") or []) if s.get("status") == "awaiting_expert"]
    # Presentation readiness ⊥ scientific closure (P1): an investigation can be CLOSED (every
    # acceptance criterion met) while still not PUBLISHABLE — a member study's figures, evaluators,
    # or contracts haven't materialized (observed on eukaryogenesis: closed-ish arc, empty viz
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
    member_slugs = inv_spec.get("studies") or _slugs_on_disk(flat, nested)

    studies_by_slug: dict[str, dict] = {}
    for s in member_slugs:
        for base in (flat, nested):
            sp = base / s / "study.yaml"
            if sp.exists():
                studies_by_slug[s] = load_yaml(sp)
                break
    return inv_path, inv_spec, studies_by_slug


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


def _slugs_on_disk(*dirs: Path) -> list[str]:
    seen: list[str] = []
    for d in dirs:
        if d.is_dir():
            for child in sorted(d.iterdir()):
                if (child / "study.yaml").exists() and child.name not in seen:
                    seen.append(child.name)
    return seen


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
    lines.append(f"  model version: {mv['id']}  (executable_digest {mv['executable_digest']}, "
                 f"validation {mv['validation']['status']} — {', '.join(mv['validation']['required'])})")
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


_OUTCOME_GLYPH = {"stable-endosymbiosis": "██ stable", "collapse": "░░ collapse",
                  "transient-association": "▒▒ transient", "no-transition": "·· none"}


def _coerce_scalar(v: str):
    """Coerce a ``--param key=value`` string value to int/float when it parses."""
    s = str(v).strip()
    for cast in (int, float):
        try:
            return cast(s)
        except ValueError:
            pass
    return s


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


def _summarize_transition(report: dict) -> str:
    """Render the Cycle N+2 composed model + the conditional transition phase diagram."""
    mv = report["model_version"]
    lines = [f"Proto-Endosymbiosis Composite — {mv['id']}",
             f"  built on Cycle N+1 versions: {mv['parent_ids'] or '(none pinned)'}",
             f"  mechanisms: {', '.join(mv['components'])}"]
    if mv.get("coupling_issues"):
        for i in mv["coupling_issues"]:
            lines.append(f"    ⚑ {i}")
    lines.append("")
    lines.append("conditional transition phase diagram (proximity × pump × heredity):")
    for cell in report["surface"]:
        p = cell["point"]
        coords = f"proximity={p['proximity']:<9} pump={p['pump']:<9} heredity={p['heredity']:<4}"
        lines.append(f"    {coords} → {_OUTCOME_GLYPH.get(cell['outcome'], cell['outcome'])}")
    lines.append("  tally: " + ", ".join(f"{k}×{v}" for k, v in sorted(report["tally"].items())))
    if report["caveats"]:
        lines.append("  conditional on (unresolved upstream):")
        for c in report["caveats"]:
            lines.append(f"    ⚑ {c}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------

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

    p_trans = sub.add_parser("transition",
                             help="Cycle N+2: compose the Proto-Endosymbiosis Composite and estimate the conditional transition phase diagram over proximity × pump × heredity")
    p_trans.add_argument("investigation", help="investigation slug")

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

    args = parser.parse_args(argv)
    ws = Path(args.workspace).resolve()

    if args.cmd in ("cycle", "status"):
        inv_path, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        result = run_cycle(inv_spec, studies)
        print(summarize(result))
        if args.cmd == "cycle" and not args.no_write:
            persist_cycle(inv_path, inv_spec, result)
            print(f"persisted knowledge_cycle -> {inv_path}")
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

    if args.cmd == "crank":
        from . import discovery_engine
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        result = run_cycle(inv_spec, studies)
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
        result = run_cycle(inv_spec, studies)
        diag = discovery_engine.diagnose(result, inv_spec)
        print(discovery_engine.summarize_diagnosis(diag))
        return 0

    if args.cmd == "suggest":
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        result = run_cycle(inv_spec, studies)
        print(summarize_suggestions(result["suggestions"]))
        return 0

    if args.cmd == "opportunities":
        from . import discovery_engine
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        result = run_cycle(inv_spec, studies)
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
        result = run_cycle(inv_spec, studies)
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
        print(_summarize_discovery_delta(_ev.discovery_delta(inv_spec, studies, ev_state)))
        return 0

    if args.cmd == "transition":
        from . import transition_model as _tm
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        print(_summarize_transition(_tm.transition_phase_report(inv_spec, studies)))
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
        mv = _ma.model_version(req, routes)
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
            print(f"  run it: discover advance {args.investigation}  (or vivarium-workbench run study {args.slug})")
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
        plan = discovery_engine.execute_plan(run_cycle(inv_spec, studies), inv_spec)
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
