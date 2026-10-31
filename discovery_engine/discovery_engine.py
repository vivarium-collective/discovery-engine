"""Formal discovery engine — turn the crank on an investigation.

RFC-0005 gives the operational algebra (branch / compose / test / synthesize /
narrate / audit) and the knowledge-clock transition I_{n+1} = T(a_n, I_n, delta_n).
This module makes the CRANK explicit and robust. One turn:

1. PROJECT the cycle (``discover.run_cycle``): verdicts, argument graph, narrative,
   audit gaps, synthesis spine, open calls.
2. FRONTIER: the set of unresolved items, each typed and PRIORITIZED by leverage on
   closure, each carrying the RECOMMENDED operation to resolve it.
3. NEXT: the single highest-leverage item — what to work on now, and how.
4. CLOSURE: is the investigation settled? (every acceptance criterion met, no open
   narrative gap, no unadjudicated challenge.)
5. INVARIANTS: the RFC-0005 validity invariants that every turn must preserve
   (provenance; epistemic separation; counterevidence retention).

A human, an agent, or software EXECUTES the recommended operation (builds the model,
runs it, researches) and records the result with the ``discover`` write ops
(set_verdict / connect / persist / a new study); the engine then re-projects and
advances. The engine is pure and deterministic — same evidence yields the same
frontier — so the crank is reproducible.

Public API
----------
frontier(cycle_result, inv_spec) -> list[dict]
next_action(cycle_result, inv_spec) -> dict | None
closure(cycle_result, inv_spec) -> dict
invariants(cycle_result) -> list[dict]
crank(cycle_result, inv_spec) -> dict          # one combined report
"""
from __future__ import annotations

from .run_command import render_command, run_command
from .validation import epistemic_separation_violations, stale_records


# Frontier priorities (higher = more urgent; a closure-blocker outranks a polish item).
_PRIORITY = {
    "unaddressed_criterion": 90,   # an acceptance criterion not yet met — blocks closure
    "open_challenge": 80,          # an alternative/refutation not yet adjudicated
    "narrative_gap": 70,           # an unsupported transition in the reading
    "open_decision": 60,           # an authored decision awaiting a steer
    "stale_validation": 55,        # a snapshotted validation the world moved past (RFC-0007)
    "divergence": 50,              # authored verdict disagrees with the coded roll-up
    "validation_finding": 45,      # a registered validator (RFC-0007) reported a failing check
    "pending_gate": 42,            # an acceptance gate met by verdict only — behavior unrun
    "open_question": 40,           # an unassessed / inconclusive claim
    # Closed-loop feedback (NON-blocking, never in _CLOSURE_BLOCKING): a model revision
    # the engine proposed, or the raw signal behind one.
    "revision_proposed": 58,       # a ModelRevisionProposal awaiting review
    "model_disagreement": 48,      # arms disagree on an observable (no proposal yet)
    "anomaly": 48,                 # an observation outside every live hypothesis band
    # Visualization loop (NON-blocking): a declared figure that is broken/missing/stale.
    "viz_broken": 47, "viz_missing": 46, "viz_proposed": 44, "viz_stale": 38,
}
# Recommended operation per item kind (the RFC-0005 op that resolves it).
_OP = {
    "unaddressed_criterion": "branch",   # open a study that addresses the criterion
    "open_challenge": "synthesize",      # adjudicate the alternative into a justification
    "narrative_gap": "branch",           # a follow-up study supplying the missing link
    "open_decision": "steer",            # resolve the decision and propagate
    "stale_validation": "revalidate",    # re-check and re-snapshot the drifted judgment
    "divergence": "reconcile",           # reconcile authored vs derived verdict
    "validation_finding": "revise",      # act on the validator's recommendation, then resolve
    "pending_gate": "test",              # run the gate's behavior — it is met by verdict only
    "open_question": "test",             # evaluate the claim
    "revision_proposed": "revise_model",  # review/apply the proposed model revision
    "model_disagreement": "revise_model",
    "anomaly": "revise_model",
    "viz_broken": "refine_viz", "viz_missing": "build_viz",
    "viz_proposed": "build_viz", "viz_stale": "refine_viz",
}
# open_calls kinds map straight onto frontier kinds.
_CALL_KIND = {"narrative_gap": "narrative_gap", "decision": "open_decision",
              "divergence": "divergence", "open_question": "open_question"}

_MET = ("supported", "qualified")


def _resolution_keys(inv_spec: dict) -> set:
    """``(kind, target)`` pairs the investigation has recorded as resolved.

    A resolution is an authored record ``{kind, target, rationale, by?}`` written by
    the op that discharged the item (e.g. an adjudicated challenge, a bridged narrative
    gap, a met criterion) — the artifact's back-reference to the frontier item it closes.
    ``frontier`` consults this one set for every item kind."""
    out: set = set()
    for r in inv_spec.get("resolutions") or []:
        if isinstance(r, dict) and r.get("kind") and r.get("target"):
            out.add((r["kind"], r["target"]))
    return out


def frontier(cycle_result: dict, inv_spec: dict) -> list[dict]:
    """The prioritized set of unresolved items. Each item is
    ``{kind, target, priority, op, rationale}``; the list is sorted by priority
    (descending), ties broken by a stable key so the crank is deterministic."""
    verdicts = (cycle_result.get("knowledge") or {}).get("verdicts") or {}
    edges = (cycle_result.get("argument") or {}).get("edges") or []
    resolved = _resolution_keys(inv_spec)
    items: list[dict] = []
    seen: set[tuple] = set()

    def add(kind, target, rationale):
        key = (kind, target)
        # Uniform resolution: one check discharges a frontier item of ANY kind. An op
        # that produced its artifact records a resolution {kind, target, …}; until then
        # the item is raised. This closes the knowledge-clock loop — the action aₙ a
        # cycle performs feeds the next frontier — for every kind, not just acceptance.
        if key in seen or key in resolved:
            return
        seen.add(key)
        items.append({"kind": kind, "target": target, "priority": _PRIORITY.get(kind, 30),
                      "op": _OP.get(kind, "test"), "rationale": rationale})

    behaviors = (cycle_result.get("knowledge") or {}).get("behaviors") or {}
    # 1. acceptance criteria. A criterion {study, behavior} is MET when its NAMED
    #    behavior's test PASSED — independent of the study's roll-up verdict. This keeps
    #    the two axes (progress ⊥ verdict) orthogonal: an adversarial null control, whose
    #    designed success is a `refuted` verdict AND whose acceptance behavior passed, is a
    #    met criterion, not an open frontier item. With no behavior outcome recorded, it
    #    falls back to the verdict (a non-supported/qualified verdict is unmet).
    for c in inv_spec.get("acceptance_criteria") or []:
        study = c.get("study") if isinstance(c, dict) else (c if isinstance(c, str) else None)
        if not study:
            continue
        behavior = c.get("behavior") if isinstance(c, dict) else None
        beh_result = (behaviors.get(study) or {}).get(behavior) if behavior else None
        if beh_result == "PASS":
            continue  # the named acceptance behavior passed → criterion met
        vrec = verdicts.get(study) or {}
        v = vrec.get("verdict")
        if v not in _MET:
            if vrec.get("calibrated"):
                # The study addressing this criterion ALREADY EXISTS and is seeded/
                # calibrated (recorded findings present) but has never been run as a
                # tracked study. The resolving action is to RUN the existing study (an
                # `open_question` -> `test` op that `advance` can execute), NOT to branch
                # a brand-new one. `run_readiness` is re-checked at execution, so a
                # calibrated-but-unrunnable study simply is not dispatched — but a
                # run-ready one gets run, its outcomes write back, and the criterion is
                # re-evaluated next cycle. (Without this, a seeded/calibrated arc stalls
                # on 'planned/pending' forever: branch is a human-gated placeholder the
                # autonomous crank never executes.)
                add("open_question", study,
                    (f"{study} is calibrated/seeded but not run as a tracked study — run it "
                     f"to assess acceptance behavior '{behavior}' on tracked evidence"
                     if behavior else
                     f"{study} is calibrated/seeded but not run as a tracked study — run it"))
            else:
                # No real study behind the criterion yet (never seeded / no recorded
                # evidence): propose a new one.
                add("unaddressed_criterion", study,
                    (f"acceptance criterion '{behavior}' on {study} not met "
                     f"(behavior: {beh_result or 'no-run'}, verdict: {v or 'none'})"
                     if behavior else
                     f"acceptance criterion on {study} not met (verdict: {v or 'none'})"))
        elif behavior:
            # Met via the study's verdict, but the NAMED gate behavior never actually
            # passed (no run / not PASS). The criterion is satisfied on paper but the
            # specific gate is unverified — surface it as verification debt (it does not
            # block closure; closure counts the criterion as met).
            add("pending_gate", study,
                f"gate '{behavior}' on {study} is met by verdict ({v}) only — "
                f"behavior {beh_result or 'not run'}; run it to verify the gate on evidence")

    # 2. unadjudicated challenge edges (an alternative/refutation left open)
    for e in edges:
        if e.get("type") == "challenge":
            add("open_challenge", e.get("to"),
                f"challenge {e.get('from')} -> {e.get('to')} ({e.get('relation')}) unadjudicated")

    # 3. the derived open calls (narrative gaps, decisions, divergences, open questions)
    for call in cycle_result.get("open_calls") or []:
        kind = _CALL_KIND.get(call.get("kind"))
        if not kind:
            continue
        refs = call.get("refs") or []
        target = refs[0] if refs else (call.get("title") or kind)
        add(kind, target, call.get("title") or "")

    # 4. validator findings (RFC-0007): a registered validator reported a failing
    #    check. Records already surfaced by another path (narrative coherence is
    #    raised as a narrative_gap via open_calls) are skipped so they are not
    #    double-counted; the rest enter the frontier, resolvable like any item.
    for rec in cycle_result.get("validations") or []:
        if not isinstance(rec, dict):
            continue
        # Only frontier-surfacing validators reach the actionable crank; "standing"
        # validators (rigor, band provenance) are feedback, shown elsewhere, and never
        # flood the frontier. (A record with no surface defaults to frontier.)
        if rec.get("surfaces", "frontier") != "frontier":
            continue
        # Only failed/errored records are actionable; an `inconclusive` record is
        # standing feedback (e.g. a rigor WARN) and stays off the frontier.
        if (rec.get("result") or {}).get("execution_status") not in ("failed", "error"):
            continue
        if rec.get("type") == "narrative":
            continue
        # visual records are projected per figure as typed viz_* items in step 7
        # (build/refine ops), not as one generic validation_finding with op `revise`.
        # A crashed visual validator (status `error`) is NOT skipped: it falls through
        # to a validation_finding so a harvest bug is visible, not a silent empty frontier.
        if rec.get("type") == "visual" and (rec.get("result") or {}).get("execution_status") != "error":
            continue
        findings = rec.get("findings") or []
        add("validation_finding", rec.get("target"),
            f"[{rec.get('type')}/{rec.get('scope')}] "
            + ("; ".join(str(f) for f in findings) if findings else "validation failed"))

    # 5. stale validations (RFC-0007 Phase 4): a snapshotted validation record that a
    #    fresh re-derivation no longer matches — an accepted judgment the world moved
    #    past (an upstream model/run changed). Re-validate and re-snapshot.
    for s in stale_records(cycle_result.get("validations"), inv_spec.get("validation_snapshot")):
        add("stale_validation", s["target"],
            f"validation '{s['validator_id']}' on {s['target']} is {s['change']} since the "
            f"accepted snapshot — re-validate")

    # 6. closed-loop feedback (non-blocking). One revision_proposed per OPEN proposal; the
    #    on-disk status (a human approved/rejected it) wins over the freshly-derived copy.
    #    A raw anomaly / model_disagreement is emitted only when no proposal (of ANY status,
    #    so a rejected one is not re-raised forever) already covers its study+observable.
    disk = {p.get("id"): p for p in inv_spec.get("revision_proposals") or [] if isinstance(p, dict)}
    proposals = [p for p in cycle_result.get("revision_proposals") or [] if isinstance(p, dict)]
    covered: set = set()
    for p in list(disk.values()) + proposals:
        src = p.get("source") or {}
        covered.add((src.get("study"), src.get("observable")))
    for p in proposals:
        status = (disk.get(p.get("id")) or p).get("status", "proposed")
        if status not in ("proposed", "approved"):
            continue
        src = p.get("source") or {}
        add("revision_proposed", p.get("id"),
            f"revise {p.get('affected_model_version') or 'model'}: {p.get('inadequacy') or src.get('kind')} "
            f"({src.get('study')}/{src.get('observable')})")
    sig = cycle_result.get("signals") or {}
    for r in sig.get("model_disagreements") or []:
        if not r.get("disagreement") or (r.get("study"), r.get("observable")) in covered:
            continue
        add("model_disagreement", f"{r.get('study')}:{r.get('observable')}",
            f"arms disagree on {r.get('observable')} in {r.get('study')} (spread {r.get('spread')})")
    for a in sig.get("anomalies") or []:
        if (a.get("study"), a.get("observable")) in covered:
            continue
        add("anomaly", f"{a.get('study')}:{a.get('observable')}",
            a.get("proposed_question") or f"{a.get('observable')} outside every live hypothesis band")

    # 7. visualization loop (non-blocking): one typed item per non-fresh declared figure.
    #    fresh emits nothing (derivation discharge).
    _viz_kind = {"broken_stub": "viz_broken", "unrendered": "viz_missing", "stale": "viz_stale"}
    for rec in cycle_result.get("validations") or []:
        if not isinstance(rec, dict) or rec.get("type") != "visual" or rec.get("scope") != "figure":
            continue
        crit = (rec.get("criteria") or [None])[0]
        kind = _viz_kind.get(crit)
        if kind is None:
            continue
        text = "; ".join(str(f) for f in rec.get("findings") or [])
        fix = "; ".join(str(f) for f in rec.get("recommendations") or [] if f)
        add(kind, rec.get("target"), text + (f" — {fix}" if fix else ""))

    items.sort(key=lambda x: (-x["priority"], str(x["kind"]), str(x["target"])))
    return items


def next_action(cycle_result: dict, inv_spec: dict) -> dict | None:
    """The single highest-leverage unresolved item (or ``None`` when closed)."""
    f = frontier(cycle_result, inv_spec)
    return f[0] if f else None


def closure(cycle_result: dict, inv_spec: dict) -> dict:
    """Is the investigation settled? Closed iff no acceptance criterion is unmet, no
    narrative gap is open, and no challenge is unadjudicated — the machine-checkable
    form of the RFC-0005 closure condition."""
    f = frontier(cycle_result, inv_spec)
    unaddressed = [i["target"] for i in f if i["kind"] == "unaddressed_criterion"]
    gaps = [i for i in f if i["kind"] == "narrative_gap"]
    challenges = [i for i in f if i["kind"] == "open_challenge"]
    return {
        "closed": not unaddressed and not gaps and not challenges,
        "unaddressed_criteria": unaddressed,
        "open_gaps": len(gaps),
        "open_challenges": len(challenges),
        "open_items": len(f),
    }


def invariants(cycle_result: dict) -> list[dict]:
    """Validity-invariant violations to flag each turn (RFC-0005 §"validity invariants").

    - provenance: an assessed claim (supported/qualified/refuted) with no evidence_refs.
    - epistemic_separation (RFC-0007): a validation record whose type establishes
      execution correctness only (structural/computational/visual) yet asserts a
      scientific judgment — a passing check standing in for biological truth.
    """
    out: list[dict] = []
    claims = (cycle_result.get("argument") or {}).get("claims") or {}
    for cid, c in claims.items():
        if not isinstance(c, dict):
            continue
        v = c.get("verdict")
        if v in ("supported", "qualified", "refuted") and not (c.get("evidence_refs") or []):
            out.append({"invariant": "provenance", "target": cid,
                        "detail": f"claim is {v} but traces to no evidence"})
    out.extend(epistemic_separation_violations(cycle_result.get("validations") or []))
    return out


def crank(cycle_result: dict, inv_spec: dict) -> dict:
    """One combined crank report: the frontier, the next action, closure, invariants."""
    return {
        "closure": closure(cycle_result, inv_spec),
        "next_action": next_action(cycle_result, inv_spec),
        "frontier": frontier(cycle_result, inv_spec),
        "invariant_violations": invariants(cycle_result),
        "suggestions": cycle_result.get("suggestions") or [],
    }


def summarize(report: dict) -> str:
    c = report["closure"]
    lines = [
        f"closure: {'CLOSED' if c['closed'] else 'OPEN'}  "
        f"(unaddressed criteria {len(c['unaddressed_criteria'])}, "
        f"open gaps {c['open_gaps']}, open challenges {c['open_challenges']}, "
        f"frontier {c['open_items']})",
    ]
    na = report["next_action"]
    if na:
        lines.append(f"next: [{na['op']}] {na['kind']} on {na['target']}  — {na['rationale']}")
    else:
        lines.append("next: — nothing open; the investigation is settled.")
    inv = report["invariant_violations"]
    if inv:
        lines.append(f"invariant violations: {len(inv)} "
                     f"({', '.join(sorted({v['invariant'] for v in inv}))})")
    sugg = report.get("suggestions") or []
    if sugg:
        ready = sum(1 for s in sugg if s.get("status") == "ready")
        lines.append(f"suggested studies: {len(sugg)} ({ready} ready, "
                     f"{len(sugg) - ready} awaiting expert input) — run `discover suggest`")
    lines.append("top frontier:")
    for i in report["frontier"][:8]:
        lines.append(f"  {i['priority']:>3}  [{i['op']:>10}] {i['kind']:<22} {i['target']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# diagnose — why is the cycle (not) making progress?
# ---------------------------------------------------------------------------
# A crank that reaches CLOSED, or stalls, is *productive* (items discharged) but that
# says nothing about decision value. Following the Perfect-Discovery-Engine productivity
# diagnostics (Table 2 / §11.1), classify the residual state into bottleneck categories
# from data run_cycle already produces — each with the operational signal, the evidence,
# and the next intervention. The flags are not exclusive: a cycle can be several at once.

#: frontier kinds that are expert hand-backs, not engine-actionable work.
_STEER_KINDS = frozenset({"open_decision"})


def _flag(name: str, evidence: str, intervention: str) -> dict:
    return {"flag": name, "evidence": evidence, "intervention": intervention}


def diagnose(cycle_result: dict, inv_spec: dict) -> dict:
    """Diagnose the crank's productivity bottlenecks (pure; no oracle required).

    Returns ``{closed, frontier_size, validator_pass_rate, census, flags}`` where each
    flag is ``{flag, evidence, intervention}``. Flags: ``task_saturated`` (nothing worth
    doing left), ``input_limited`` (waiting on expert priors/data — steer items dominate),
    ``planner_limited`` (a large same-priority cluster the crank can't rank by value),
    ``validation_limited`` (low validator pass-rate / ungrounded claims), and
    ``capability_limited`` (a validator could not run)."""
    f = frontier(cycle_result, inv_spec)
    clo = closure(cycle_result, inv_spec)
    records = cycle_result.get("validations") or []

    # The validator pass-rate measures ACTIONABLE validators (surfaces=="frontier"), not
    # `standing` feedback (rigor GAP/WARN, band provenance). A study routinely carries rigor
    # gaps; counting that feedback as "failed" washed the rate out (island: 1/27 = 3.7% where
    # only 9 records are actionable). Standing feedback is reported separately so the signal
    # isn't conflated with it.
    def _front(r):
        return r.get("surfaces", "frontier") == "frontier"
    gradeable = [r for r in records if _front(r)
                 and (r.get("result") or {}).get("execution_status") in ("passed", "failed", "error")]
    passed = sum(1 for r in gradeable
                 if (r.get("result") or {}).get("execution_status") == "passed")
    pass_rate = (passed / len(gradeable)) if gradeable else None
    standing = sum(1 for r in records if r.get("surfaces") == "standing")
    errored = [r for r in records if _front(r) and (r.get("result") or {}).get("execution_status") == "error"]
    unsupported = [r for r in records if _front(r) and r.get("type") in ("logical", "empirical")
                   and (r.get("result") or {}).get("execution_status") == "failed"]

    steer = [i for i in f if i["kind"] in _STEER_KINDS]
    actionable = [i for i in f if i["kind"] not in _STEER_KINDS]
    tiers: dict[int, int] = {}
    for i in actionable:
        tiers[i["priority"]] = tiers.get(i["priority"], 0) + 1
    top_tier_pri, top_tier_n = (max(tiers.items(), key=lambda kv: kv[1]) if tiers else (None, 0))

    flags: list[dict] = []
    if clo["closed"] and not f:
        flags.append(_flag("task_saturated",
                            "closed with an empty frontier — no admissible action remains",
                            "stop, change the objective, or expand the action space"))
    if len(steer) >= 2:
        flags.append(_flag("input_limited",
                            f"{len(steer)} expert decision(s) pending — the residual frontier is "
                            f"dominated by steer items (the next action is 'the expert decides')",
                            "supply the requested priors / datasets / scope choices (new inputs), "
                            "not more planning"))
    if top_tier_n >= 3:
        flags.append(_flag("planner_limited",
                            f"{top_tier_n} actionable items at priority {top_tier_pri} the crank "
                            f"orders only by id — no value signal to rank within the tier",
                            "add a value/EVSI estimate or lookahead to next_action (not fixed "
                            "priority alone)"))
    if pass_rate is not None and pass_rate < 0.5:
        flags.append(_flag("validation_limited",
                            f"actionable validator pass-rate {passed}/{len(gradeable)} "
                            f"({standing} standing feedback record(s) excluded); "
                            f"{len(unsupported)} ungrounded logical/empirical claim(s)",
                            "strengthen provenance/holdouts (cite bands, back assessed claims, "
                            "fill or waive rigor dimensions)"))
    if errored:
        ids = sorted({(r.get('provenance') or {}).get('validator_id') for r in errored})
        flags.append(_flag("capability_limited",
                            f"{len(errored)} validator(s) could not run: {', '.join(str(i) for i in ids)}",
                            "fix or add the simulator / interface / validator"))
    # The argument graph's edges are the UNION of two authoring channels — investigation-level
    # `scientific_argument` and study-level `parent_studies` — so an edgeless graph means
    # NEITHER was authored. Flag that (not "author parent_studies", which names only one
    # channel): with no edges, every claim stands alone, downstream impact is 0, and the
    # value-ranking can't differentiate (observed on a small graph; a 94-edge graph is fine).
    arg = cycle_result.get("argument") or {}
    if (arg.get("claims")) and not (arg.get("edges") or []):
        flags.append(_flag("argument_disconnected",
                            "the argument graph has no epistemic edges — every claim stands "
                            "alone, so downstream impact is 0 and the value-ranking cannot "
                            "differentiate opportunities",
                            "author epistemic relations in EITHER channel: investigation "
                            "`scientific_argument` (claim→claim) or study `parent_studies` "
                            "(builds-on / refutes / qualifies)"))

    # Census split so the actionable-vs-standing weight is legible (observed on a real workspace:
    # 64 records = 3 actionable + 61 standing — the pass-rate uses only the frontier bucket).
    census: dict[str, int] = {}
    census_frontier: dict[str, int] = {}
    census_standing: dict[str, int] = {}
    for r in records:
        st = (r.get("result") or {}).get("execution_status")
        census[st] = census.get(st, 0) + 1
        bucket = census_standing if r.get("surfaces") == "standing" else census_frontier
        bucket[st] = bucket.get(st, 0) + 1

    return {"closed": clo["closed"], "frontier_size": len(f),
            "validator_pass_rate": pass_rate, "census": census,
            "census_frontier": census_frontier, "census_standing": census_standing,
            "standing_feedback": standing, "flags": flags}


# ---------------------------------------------------------------------------
# opportunities — value-aware ranking of the frontier (value-aware discovery)
# ---------------------------------------------------------------------------
# The frontier is a list of unresolved items in FIXED priority order; this layer
# re-casts each as a typed OPPORTUNITY with a transparent value estimate, so the
# question shifts from "what is unresolved?" to "what is most valuable to do next?".
# The score is a weighted heuristic (Score = w_info·Info + w_impact·Impact +
# w_enable·Enable − w_cost·Cost), NOT a calibrated Bayesian value — it is explainable
# and tunable, and it reports unknowns rather than inventing them.

_OPP_WEIGHTS = {"info": 0.30, "impact": 0.40, "enable": 0.20, "cost": 0.30, "closure": 0.35,
                # discriminate: a test/branch whose study carries competing hypotheses
                # one run would separate is worth more — the stage-3 information signal.
                "discriminate": 0.50}
# Frontier kinds that block closure (the closure() condition). They sit on the
# investigation's critical path, so they carry leverage even when the argument graph has
# no edges to compute downstream `impact` from — otherwise, on an edgeless graph, cost
# dominates and an expensive-but-closure-blocking criterion ranks below a cheap test.
_CLOSURE_BLOCKING = ("unaddressed_criterion", "narrative_gap", "open_challenge")
# Information potential by action: evidence-generating acts (run a test, open a study)
# can teach something new; reconciling/adjudicating resolves bookkeeping.
_OPP_INFO = {"test": 1.0, "branch": 1.0, "steer": 0.5, "synthesize": 0.3,
             "reconcile": 0.3, "revise": 0.3, "revalidate": 0.3, "revise_model": 0.6,
             "build_viz": 0.4, "refine_viz": 0.2}
# Cost by action: a new study + simulation is dear; a reconcile is cheap.
_OPP_COST = {"branch": 1.0, "test": 0.6, "steer": 0.3, "synthesize": 0.2,
             "reconcile": 0.2, "revise": 0.2, "revalidate": 0.2, "revise_model": 0.4,
             "build_viz": 0.3, "refine_viz": 0.2}


def _opp_impact(target: str, edges: list) -> float:
    """Downstream scientific impact: how much of the argument builds on this target
    (0..1, capped at 5 dependents). A foundational claim scores high, a leaf low."""
    if not target:
        return 0.0
    cid = target if str(target).startswith("claim:") else f"claim:{target}"
    dependents = sum(1 for e in (edges or []) if e.get("from") == cid)
    return min(dependents / 5.0, 1.0)


def _opp_enable(item: dict, suggestions: list) -> float:
    """How many future investigations this unblocks: an expert decision that gates
    proposed studies scores by how many it would release (0..1, capped at 3)."""
    if item.get("kind") != "open_decision":
        return 0.0
    tgt = item.get("target")
    n = sum(1 for s in (suggestions or []) if tgt in (s.get("awaiting_expert") or []))
    return min(n / 3.0, 1.0)


def opportunities(cycle_result: dict, inv_spec: dict) -> list[dict]:
    """The frontier re-cast as value-ranked opportunities (value-aware discovery).

    Each is a typed object — ``{id, source, action_type, question,
    competing_predictions, proposed_validator, value{info,impact,enable,cost,score},
    confidence, state, unknowns}`` — scored by the transparent heuristic above and
    sorted by score (then fixed priority, then id, so the ranking is deterministic and
    not an artifact of list order). Fields the engine cannot derive are left explicit
    (``None`` / listed in ``unknowns``), never silently defaulted."""
    from . import hypotheses as _hyp
    edges = (cycle_result.get("argument") or {}).get("edges") or []
    suggestions = cycle_result.get("suggestions") or []
    disc = _hyp.discriminating_hypotheses(inv_spec)
    w = _OPP_WEIGHTS
    opps: list[dict] = []
    for it in frontier(cycle_result, inv_spec):
        op, target = it["op"], it["target"]
        info, cost = _OPP_INFO.get(op, 0.3), _OPP_COST.get(op, 0.3)
        impact, enable = _opp_impact(target, edges), _opp_enable(it, suggestions)
        closure_leverage = 1.0 if it["kind"] in _CLOSURE_BLOCKING else 0.0
        # Information value: an evidence-generating op (test/branch) whose study
        # carries competing hypotheses a single run would separate is worth more.
        slug = target[len("claim:"):] if str(target).startswith("claim:") else target
        dinfo = disc.get(slug) if op in ("test", "branch") else None
        discrimination = 1.0 if (dinfo and dinfo["discriminable"]) else 0.0
        competing_predictions = dinfo["competing"] if (dinfo and dinfo["discriminable"]) else None
        score = (w["info"] * info + w["impact"] * impact + w["enable"] * enable
                 + w["closure"] * closure_leverage + w["discriminate"] * discrimination
                 - w["cost"] * cost)
        is_claim = str(target).startswith("claim:")
        unknowns = ["proposed_validator"]
        if competing_predictions is None:
            unknowns.insert(0, "competing_predictions")
        if not is_claim:
            unknowns.append("impact (no argument-graph signal for this target)")
        opps.append({
            "id": f"{it['kind']}:{target}",
            "source": target,
            "action_type": op,
            "kind": it["kind"],
            "priority": it["priority"],
            "question": it.get("rationale") or "",
            "competing_predictions": competing_predictions,
            "proposed_validator": None,
            "value": {"info": round(info, 3), "impact": round(impact, 3),
                      "enable": round(enable, 3), "cost": round(cost, 3),
                      "closure": round(closure_leverage, 3),
                      "discriminate": round(discrimination, 3), "score": round(score, 3)},
            "confidence": 0.7 if is_claim else 0.4,
            "state": "open",
            "unknowns": unknowns,
        })
    opps.sort(key=lambda o: (-o["value"]["score"], -o["priority"], o["id"]))
    return opps


# ---------------------------------------------------------------------------
# execute_plan — the bridge from a recommendation to a concrete, gated action
# ---------------------------------------------------------------------------
# Each value-ranked opportunity resolves to the executor that would carry it out, the
# exact command, and a consequence class. Only a truly side-effect-free action
# (re-snapshot) is auto_safe; running a simulation, seeding a study, or overwriting a
# verdict is consequential and stays gated behind an explicit human/--autonomous step.

def _executor_for(op: str, target: str, kind: str | None = None) -> dict:
    if op in ("build_viz", "refine_viz"):
        t = str(target)
        if t.startswith("fig:") and "/" in t and kind != "viz_broken":
            fstudy, _, fname = t[4:].partition("/")
            return {"executor": "render-viz", "command": render_command(fstudy, fname),
                    "consequence": "render", "auto_safe": False}
        if t.startswith("fig:"):       # broken stub: declare an address/render, never auto
            return {"executor": "discover viz",
                    "command": f"discover viz <inv> {t} --declare address=… --rationale '…'",
                    "consequence": "review", "auto_safe": False}
        return {"executor": "discover viz",
                "command": f"discover viz <inv> {t} --accept|--reject",
                "consequence": "review", "auto_safe": False}
    study = str(target).split(":", 1)[1] if str(target).startswith("claim:") else str(target)
    if op == "revalidate":
        return {"executor": "snapshot-validations", "command": "discover snapshot-validations <inv>",
                "consequence": "safe", "auto_safe": True}
    if op == "test":
        return {"executor": "run-study",
                "command": run_command(study),
                "consequence": "simulation", "auto_safe": False}
    if op == "branch":
        return {"executor": "seed-from-followup + run-study",
                "command": f"viva-study seed-from-followup <parent> <proposal>; "
                           f"{run_command(study)}",
                "consequence": "simulation+study", "auto_safe": False}
    if op == "reconcile":
        return {"executor": "set-verdict", "command": f"discover set-verdict {study} <derived> --rationale '…'",
                "consequence": "review", "auto_safe": False}
    if op == "revise":
        return {"executor": "act-on-validator + resolve",
                "command": f"<address the validator's recommendation>; "
                           f"discover resolve <inv> validation_finding {target} --rationale '…'",
                "consequence": "review", "auto_safe": False}
    if op == "revise_model":
        return {"executor": "discover revise", "command": f"discover revise <inv> {target} --apply",
                "consequence": "review", "auto_safe": False}
    if op == "steer":
        return {"executor": "expert", "command": None, "consequence": "human-only", "auto_safe": False}
    if op == "synthesize":
        return {"executor": "author finding + resolve",
                "command": f"viva-study findings {study} …; discover resolve <inv> open_challenge {target} --rationale …",
                "consequence": "human-only", "auto_safe": False}
    return {"executor": op, "command": None, "consequence": "unknown", "auto_safe": False}


def execute_plan(cycle_result: dict, inv_spec: dict) -> list[dict]:
    """The value-ranked frontier, each item resolved to *how to execute it*:
    ``{source, action_type, score, executor, command, consequence, auto_safe, state}``.
    This is the recommendation → execution bridge; it proposes the concrete action and
    its consequence class, it does not run anything (gated)."""
    out: list[dict] = []
    for o in opportunities(cycle_result, inv_spec):
        ex = _executor_for(o["action_type"], o["source"], o.get("kind"))
        out.append({"id": o["id"], "kind": o.get("kind"), "source": o["source"], "action_type": o["action_type"],
                    "score": o["value"]["score"], "state": "proposed", **ex})
    return out


def summarize_execute_plan(plan: list[dict], top: int = 10) -> str:
    lines = ["execution plan (value-ranked; nothing runs without --execute / --autonomous):"]
    for p in plan[:top]:
        tag = "AUTO-SAFE" if p["auto_safe"] else p["consequence"].upper()
        tgt = str(p["source"]).replace("claim:", "")
        lines.append(f"  {p['score']:+.2f}  [{tag:>14}]  {p['action_type']} {tgt}")
        if p["command"]:
            lines.append(f"       → {p['command']}")
    if not plan:
        lines.append("  none — the frontier is empty.")
    return "\n".join(lines)


def summarize_opportunities(opps: list[dict], top: int = 10) -> str:
    lines = [f"opportunities: {len(opps)} ranked by value "
             f"(score = {_OPP_WEIGHTS['info']}·info + {_OPP_WEIGHTS['impact']}·impact + "
             f"{_OPP_WEIGHTS['enable']}·enable + {_OPP_WEIGHTS['discriminate']}·discriminate "
             f"− {_OPP_WEIGHTS['cost']}·cost)"]
    for o in opps[:top]:
        v = o["value"]
        lines.append(f"  {v['score']:+.2f}  [{o['action_type']:>10}] {o['source']}")
        lines.append(f"       info {v['info']}  impact {v['impact']}  enable {v['enable']}  "
                     f"discriminate {v.get('discriminate', 0)}  cost {v['cost']}  conf {o['confidence']}")
        cp = o.get("competing_predictions")
        if cp:
            obs = cp[0].get("observable")
            ids = ", ".join(str(c.get("hypothesis")) for c in cp)
            lines.append(f"       ⇄ separates on {obs}: {ids}")
    if not opps:
        lines.append("  none — the frontier is empty.")
    return "\n".join(lines)


def summarize_diagnosis(diag: dict) -> str:
    pr = diag["validator_pass_rate"]
    lines = [
        f"diagnosis: {'CLOSED' if diag['closed'] else 'OPEN'}  frontier {diag['frontier_size']}  "
        f"validator pass-rate {f'{pr:.0%}' if pr is not None else 'n/a'}  "
        f"records {diag['census'] or '{}'}",
    ]
    if not diag["flags"]:
        lines.append("  no bottleneck flags — the cycle is unobstructed.")
    for fl in diag["flags"]:
        lines.append(f"  [{fl['flag']}] {fl['evidence']}")
        lines.append(f"      → {fl['intervention']}")
    return "\n".join(lines)
