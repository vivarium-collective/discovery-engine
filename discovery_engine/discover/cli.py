"""Command-line interface for the discover cycle.

``main()`` + the argparse wiring for the ~30 subcommands, plus the
``summarize*`` / ``_summarize_*`` terminal-rendering helpers it uses. This is the
console-script entry point (``discover = discovery_engine.discover:main``); it
imports the logic from the sibling submodules and renders their results.
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
    VERDICT_VOCAB,
)
from discovery_engine.discover.actions import (
    _apply_refinement,
    _enrich_file,
    _write_seeded_study,
    accept_capability_gap,
    accept_viz_proposal,
    apply_revision,
    connect_investigation,
    declare_viz,
    materialize_design_study,
    record_critique_report,
    record_hypothesis_proposal,
    record_resolution,
    review_findings,
    set_critique_report_status,
    set_hypothesis_proposal_status,
    reject_capability_gap,
    reject_viz_proposal,
    review_revision,
)
from discovery_engine.discover.cycle import (
    _cycle_with_evidence,
    _load_runs_by_study,
    advance_cycle,
    build_completion_map,
    execute_action,
    load_cycle_inputs,
    persist_cycle,
    run_cycle,
    set_verdict,
    snapshot_validations,
    viz_inventory,
)


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

def summarize_advance(result: dict) -> str:
    """Human-readable advance log: what ran, what needs building, the frontier delta."""
    L = [f"frontier {result['frontier_before']} → {result['frontier_after']} "
         f"({len(result['runs'])} run attempt(s))"]
    for d in result.get("designed") or []:
        tail = (f" — {d['child']}" if d.get("child") else (f" — {d['detail']}" if d.get("detail") else ""))
        L.append(f"  [design:{d['status']}] {d['study']}{tail}")
    for r in result["runs"]:
        L.append(f"  [{r['status']}] {r['target']}" + (f" — {r['outcome']}" if r.get("outcome") else ""))
    for b in result["needs_build"]:
        L.append(f"  [needs build] {b['study']}: {'; '.join(b['gaps'][:2])}")
    if not result["runs"] and not result["needs_build"] and not (result.get("designed")):
        L.append("  (no runnable open questions — nothing to advance)")
    return "\n".join(L)

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
    from discovery_engine.phases import hypotheses as _hyp
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
    from discovery_engine.phases.evidence_integrity import VERIFIED, PROVISIONAL, CONTRADICTED, UNSUPPORTED
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
    from discovery_engine.phases.candidate_action import REPAIR, RESOLVE, EXTEND
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
    from discovery_engine.phases.model_architect import validate_coupling
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

    p_bm = sub.add_parser("benchmark",
                          help="run the DE-13 ablation benchmark in a temp dir (read-only)")
    p_bm.add_argument("--json", action="store_true", help="emit config -> metrics as JSON")
    p_bm.add_argument("--recovery", action="store_true",
                      help="run the recovery track (mechanism recovery + held-out error) instead")
    p_bm.add_argument("--real-llm", action="store_true",
                      help="fill proposals with $VIVA_FILLER_CMD (a real model) instead of the "
                           "deterministic oracle; result reflects that model and is not a fixed pass")
    p_bm.add_argument("--filler-cmd", default=None,
                      help="explicit fill command (implies --real-llm; overrides $VIVA_FILLER_CMD)")

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

    p_requests = sub.add_parser("requests",
                                help="AI-drivable cycle Phase 1: the typed GenerationRequest list the frontier emits (propose_study / acquire_model / author_tests / interpret_finding / resolve_decision / explain / visualize) for a filler to fill")
    p_requests.add_argument("investigation", help="investigation slug")
    p_requests.add_argument("--json", action="store_true", help="emit the requests as JSON")
    p_requests.add_argument("--fill", action="store_true",
                            help="dispatch auto-gated requests to $VIVA_FILLER_CMD and apply the results")
    p_requests.add_argument("--free-run", action="store_true",
                            help="with --fill: treat every request as auto (the free_run gate preset)")

    p_prop = sub.add_parser("propose",
                            help="DiscoveryAgent coordinator: route opportunities to scientific skills (dry-run by default; --fill runs the filler, records captures, applies auto-gated proposals under policy). Unlike 'requests --fill', --fill DOES run the filler on review-gated requests (to capture them for audit) but never auto-applies them")
    p_prop.add_argument("investigation", help="investigation slug")
    p_prop.add_argument("--json", action="store_true", help="machine output")
    p_prop.add_argument("--fill", action="store_true",
                        help="run $VIVA_FILLER_CMD, record captures, apply auto-gated proposals")
    p_prop.add_argument("--free-run", action="store_true", help="treat every request as auto gate")
    p_prop.add_argument("--autonomous", action="store_true",
                        help="with --fill: AutonomyPolicy(autonomous) authorizes authoring")

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
    p_design.add_argument("--materialize", action="store_true",
                          help="turn the design into a run-ready child study (writes study.yaml, registers "
                               "it, and records the ExperimentDesign on the investigation)")

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

    p_report = sub.add_parser("report",
                              help="render the workspace report (reports/index.html); Pass B lints first, "
                                   "and blocking errors refuse to render unless --force")
    p_report.add_argument("--force", action="store_true",
                          help="log blocking lint errors to the override file and render anyway")
    p_report.add_argument("--no-lint", action="store_true",
                          help="skip the pre-render lint (internal/testing; not recommended)")
    p_report.add_argument("--today", default=None, metavar="ISO_DATE",
                          help="pin the report date (YYYY-MM-DD) for byte-stable CI renders")

    p_lint = sub.add_parser("lint",
                            help="run the Pass B report linter over the workspace and print findings "
                                 "(no render); exits 1 if blocking errors remain")
    p_lint.add_argument("--json", action="store_true", help="emit findings as JSON")
    p_lint.add_argument("--force", action="store_true",
                        help="log blocking errors to the override file and exit 0")
    p_lint.add_argument("--strict", action="store_true",
                        help="promote opt-in warning checks to errors")
    p_lint.add_argument("--ruleset", action="append", default=None,
                        help="enable an opt-in ruleset (repeatable), e.g. --ruleset campaign")

    p_pop = sub.add_parser("populate-observations",
                           help="fill finding evidence/expected/provenance slots from computed_outcomes "
                                "(spine stage #5) — writes study.yaml")
    g_pop = p_pop.add_mutually_exclusive_group(required=True)
    g_pop.add_argument("--study", help="study slug")
    g_pop.add_argument("--all", action="store_true", help="every study in the workspace")

    p_romig = sub.add_parser("readout-migrate",
                             help="migrate a study's legacy readouts to canonical form (dry-run by default)")
    p_romig.add_argument("study", help="study slug")
    p_romig.add_argument("--write", action="store_true",
                         help="rewrite study.yaml's readouts block (default: dry-run)")

    p_band = sub.add_parser("band-provenance",
                            help="list a study's acceptance bands that have no cites "
                                 "(no machine-linked source in references/papers.bib)")
    p_band.add_argument("study", help="study slug")
    p_band.add_argument("--json", action="store_true", help="emit the missing-provenance bands as JSON")

    p_linkage = sub.add_parser("linkage",
                               help="the workspace linkage graph (nodes + edges): investigations, studies, "
                                    "sources, observables and the edges between them — the engine-pure core "
                                    "of the /api/linkage-index payload")
    p_linkage.add_argument("--json", action="store_true", help="emit the full {nodes, edges} index as JSON")

    p_hz = sub.add_parser("hypothesize",
                          help="surface where a competing hypothesis is NEEDED (measured surprises the "
                               "live hypotheses did not predict), and validate+record an agent-authored "
                               "HypothesisProposal; the engine validates, the agent authors the science")
    p_hz.add_argument("investigation", help="investigation slug")
    p_hz.add_argument("--from", dest="from_file", default=None, metavar="FILE",
                      help="validate a HypothesisProposal (YAML/JSON) and record it as 'proposed'")
    p_hz.add_argument("--accept", default=None, metavar="HP_ID",
                      help="accept a recorded proposal and promote it into authored hypotheses[]")
    p_hz.add_argument("--decline", default=None, metavar="HP_ID", help="decline a recorded proposal")
    p_hz.add_argument("--rationale", default=None, help="rationale for --accept/--decline")
    p_hz.add_argument("--list-proposals", action="store_true", help="list recorded proposals + status")
    p_hz.add_argument("--json", action="store_true", help="emit as JSON")

    p_cq = sub.add_parser("critique",
                          help="surface where a result should be challenged (a verdict/confidence that "
                               "outruns its evidence), and validate+record an agent-authored CritiqueReport; "
                               "advisory — it never changes a verdict")
    p_cq.add_argument("investigation", help="investigation slug")
    p_cq.add_argument("--from", dest="from_file", default=None, metavar="FILE",
                      help="validate a CritiqueReport (YAML/JSON) and record it as 'open'")
    p_cq.add_argument("--address", default=None, metavar="CR_ID", help="mark a recorded critique addressed")
    p_cq.add_argument("--dismiss", default=None, metavar="CR_ID", help="dismiss a recorded critique")
    p_cq.add_argument("--rationale", default=None, help="rationale for --address/--dismiss")
    p_cq.add_argument("--list-reports", action="store_true", help="list recorded critiques + status")
    p_cq.add_argument("--json", action="store_true", help="emit as JSON")

    p_rf = sub.add_parser("review-findings",
                          help="list pending FindingCandidates (verified verdict flips) and promote "
                               "reviewed ones to Discoveries; promotion is the only path that writes a "
                               "Discovery, and it attaches a novelty/validation assessment")
    p_rf.add_argument("investigation", help="investigation slug")
    p_rf.add_argument("--promote", action="append", default=None, metavar="FC_ID",
                      help="promote this candidate to a Discovery (repeatable); naming it is the review")
    p_rf.add_argument("--hold", action="append", default=None, metavar="FC_ID",
                      help="park this candidate without promoting it (repeatable)")
    p_rf.add_argument("--all", action="store_true",
                      help="promote every pending candidate the autonomy policy authorizes")
    p_rf.add_argument("--autonomous", action="store_true",
                      help="authorize promotion under an autonomous AutonomyPolicy (for --all)")
    p_rf.add_argument("--json", action="store_true", help="emit as JSON")

    args = parser.parse_args(argv)
    ws = Path(args.workspace).resolve()

    if args.cmd in ("cycle", "status"):
        inv_path, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        result = _cycle_with_evidence(ws, args.investigation, inv_spec, studies)
        print(summarize(result))
        if args.cmd == "cycle" and not args.no_write:
            from discovery_engine.phases import evidence_integrity as _ei
            ev_state = _ei.reconcile_evidence_state(
                inv_spec, studies, _load_runs_by_study(ws, args.investigation, studies))
            persist_cycle(inv_path, inv_spec, result, studies=studies, evidence_state=ev_state,
                          viz_state=viz_inventory(ws, args.investigation, studies))
            print(f"persisted cycle_state -> {inv_path}")
        return 0

    if args.cmd == "benchmark":
        import json as _json
        import tempfile
        from dataclasses import asdict as _asdict
        from pathlib import Path as _P
        from discovery_engine.benchmark.report import (
            render_matrix, render_recovery_matrix, run_matrix, run_recovery_matrix)

        class _Factory:
            def __init__(self, base):
                self.base, self.n = _P(base), 0

            def mktemp(self, name):
                self.n += 1
                d = self.base / f"{name}-{self.n}"
                d.mkdir(parents=True)
                return d

        fill = None
        if getattr(args, "real_llm", False) or getattr(args, "filler_cmd", None):
            import sys as _sys
            if args.recovery:
                print("--real-llm applies to the proposal track, not --recovery "
                      "(the recovery track builds a model via a build hook, not a fill)",
                      file=_sys.stderr)
                return 2
            import os as _os

            from discovery_engine.phases.generation_apply import subprocess_filler
            cmd = args.filler_cmd or _os.environ.get("VIVA_FILLER_CMD")
            if not cmd:
                print("--real-llm needs a fill command: set $VIVA_FILLER_CMD or pass --filler-cmd",
                      file=_sys.stderr)
                return 2
            fill = subprocess_filler(cmd)
        with tempfile.TemporaryDirectory(prefix="de-benchmark-") as td:
            if args.recovery:
                matrix = run_recovery_matrix(tmp_path_factory=_Factory(td))
            else:
                matrix = run_matrix(tmp_path_factory=_Factory(td), fill=fill)
        render = render_recovery_matrix if args.recovery else render_matrix
        if args.json:
            print(_json.dumps({k: _asdict(v) for k, v in matrix.items()}, indent=2))
        else:
            print(render(matrix))
        return 0

    if args.cmd == "ingest-feedback":
        import json as _json
        from discovery_engine.feedback import compiler as feedback_compiler
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

    if args.cmd == "hypothesize":
        import json as _json
        from discovery_engine.phases.hypothesis_proposal import hypothesis_triggers
        inv_path = investigations_dir(ws) / args.investigation / "investigation.yaml"
        if not inv_path.exists():
            parser.error(f"no investigation.yaml at {inv_path}")
        if args.accept or args.decline:
            status = "accepted" if args.accept else "declined"
            rec = set_hypothesis_proposal_status(inv_path, args.accept or args.decline, status,
                                                 rationale=args.rationale)
            print(f"{rec['id']}: {rec['status']}"
                  + ("  -> promoted into hypotheses[]" if rec["promoted"] else ""))
            return 0
        if args.from_file:
            proposal = load_yaml(Path(args.from_file))
            try:
                rec = record_hypothesis_proposal(inv_path, proposal)
            except ValueError as e:
                parser.error(f"invalid HypothesisProposal: {e}")
            print(f"recorded {rec['id']} (status={rec['status']}); "
                  f"accept it: discover --workspace {args.workspace} hypothesize "
                  f"{args.investigation} --accept {rec['id']}")
            return 0
        spec = load_yaml_mapping(inv_path)
        if args.list_proposals:
            props = [p for p in spec.get("hypothesis_proposals") or [] if isinstance(p, dict)]
            if args.json:
                print(_json.dumps(props, indent=2, default=str))
                return 0
            if not props:
                print("no recorded hypothesis proposals")
                return 0
            for p in props:
                print(f"{p['id']}  [{p.get('status')}]  {p.get('statement')}")
            return 0
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        triggers = hypothesis_triggers(inv_spec, list((studies or {}).values()))
        if args.json:
            print(_json.dumps(triggers, indent=2, default=str))
            return 0
        if not triggers:
            print("no unexplained anomalies — the live hypotheses account for the measured evidence")
            return 0
        print("a competing hypothesis is needed for:")
        for t in triggers:
            print(f"  {t['id']}  {t['study']}#{t['observable']} = {t['observed']}  ({t['note']})")
        print(f"\nauthor one per skills/hypothesize/SKILL.md, then: discover --workspace "
              f"{args.workspace} hypothesize {args.investigation} --from <proposal.yaml>")
        return 0

    if args.cmd == "critique":
        import json as _json
        from discovery_engine.phases.critique_report import critique_triggers
        inv_path = investigations_dir(ws) / args.investigation / "investigation.yaml"
        if not inv_path.exists():
            parser.error(f"no investigation.yaml at {inv_path}")
        if args.address or args.dismiss:
            status = "addressed" if args.address else "dismissed"
            rec = set_critique_report_status(inv_path, args.address or args.dismiss, status,
                                             rationale=args.rationale)
            print(f"{rec['id']}: {rec['status']}")
            return 0
        if args.from_file:
            report = load_yaml(Path(args.from_file))
            try:
                rec = record_critique_report(inv_path, report)
            except ValueError as e:
                parser.error(f"invalid CritiqueReport: {e}")
            print(f"recorded {rec['id']} (status={rec['status']}) against {rec['target']}")
            return 0
        spec = load_yaml_mapping(inv_path)
        if args.list_reports:
            reps = [r for r in spec.get("critique_reports") or [] if isinstance(r, dict)]
            if args.json:
                print(_json.dumps(reps, indent=2, default=str))
                return 0
            if not reps:
                print("no recorded critique reports")
                return 0
            for r in reps:
                print(f"{r['id']}  [{r.get('status')}]  {r.get('target')}")
            return 0
        _, _, studies = load_cycle_inputs(ws, args.investigation)
        triggers = critique_triggers(list((studies or {}).values()))
        if args.json:
            print(_json.dumps(triggers, indent=2, default=str))
            return 0
        if not triggers:
            print("no overclaimed results — authored verdicts/confidence match the evidence")
            return 0
        print("a result should be challenged for:")
        for t in triggers:
            print(f"  {t['id']}  {t['study']}  [{t['kind']}] authored={t['authored']!r} "
                  f"derived={t['derived']!r}  ({t['note']})")
        print(f"\nauthor a critique per skills/critique/SKILL.md, then: discover --workspace "
              f"{args.workspace} critique {args.investigation} --from <critique.yaml>")
        return 0

    if args.cmd == "review-findings":
        import json as _json
        inv_path = investigations_dir(ws) / args.investigation / "investigation.yaml"
        if not inv_path.exists():
            parser.error(f"no investigation.yaml at {inv_path}")
        from discovery_engine.phases.discovery_record import assess_finding_candidate, CANDIDATE
        from discovery_engine.discover._policy import AutonomyPolicy, AUTONOMOUS
        if not (args.promote or args.hold or args.all):          # list mode (read-only)
            spec = load_yaml_mapping(inv_path)
            discs = spec.get("discoveries") or []
            pending = [c for c in spec.get("finding_candidates") or []
                       if isinstance(c, dict) and c.get("status") == CANDIDATE]
            rows = [{"id": c["id"], "study": c.get("study"), "to_verdict": c.get("to_verdict"),
                     "assessment": assess_finding_candidate(c, discs)} for c in pending]
            if args.json:
                print(_json.dumps(rows, indent=2, default=str))
                return 0
            if not rows:
                print("no pending finding candidates")
                return 0
            for r in rows:
                a = r["assessment"]
                print(f"{r['id']}  {r['study']}: -> {r['to_verdict']}  "
                      f"[{a['novelty']} / would {a['review_decision']}]")
            print(f"\npromote: discover --workspace {args.workspace} review-findings "
                  f"{args.investigation} --promote <fc-id>")
            return 0
        pol = AutonomyPolicy(mode=AUTONOMOUS) if args.autonomous else AutonomyPolicy()
        reviewer = "engine" if args.autonomous else "human"
        rep = review_findings(inv_path, promote=args.promote, hold=args.hold,
                              promote_all=args.all, policy=pol, reviewer=reviewer)
        if args.json:
            print(_json.dumps(rep, indent=2, default=str))
            return 0
        print(f"promoted {len(rep['promoted'])} -> Discoveries"
              + (f": {', '.join(rep['promoted'])}" if rep['promoted'] else "")
              + f"; held {len(rep['held'])}; skipped {len(rep['skipped'])}; "
              f"pending {len(rep['pending'])}")
        for s in rep["skipped"]:
            print(f"  skipped {s['id']}: {s['reason']}")
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
        from discovery_engine.projection import engine as discovery_engine
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
        from discovery_engine.projection import engine as discovery_engine
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
        from discovery_engine.projection import engine as discovery_engine
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        result = _cycle_with_evidence(ws, args.investigation, inv_spec, studies)
        opps = discovery_engine.opportunities(result, inv_spec)
        print(discovery_engine.summarize_opportunities(opps))
        return 0

    if args.cmd == "requests":
        import json as _json

        from discovery_engine.projection import engine as discovery_engine
        from discovery_engine.phases.generation import GateConfig
        from discovery_engine.phases.generation_emit import generation_requests, summarize_requests
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        result = _cycle_with_evidence(ws, args.investigation, inv_spec, studies)
        opps = discovery_engine.opportunities(result, inv_spec)
        if getattr(args, "free_run", False):
            gates = GateConfig.preset("free_run")
        else:
            gc = (inv_spec.get("discovery") or {}).get("gates") if isinstance(inv_spec, dict) else None
            gates = (GateConfig(default=gc.get("default"), by_kind=gc.get("by_kind") or {})
                     if isinstance(gc, dict) else GateConfig())
        reqs = generation_requests(opps, gates=gates, inv_spec=inv_spec)
        if getattr(args, "fill", False):
            from discovery_engine.phases.generation_apply import run_requests, subprocess_filler
            outcomes = run_requests(reqs, fill=subprocess_filler(),
                                    ws_root=ws, inv_slug=args.investigation)
            print(_json.dumps(outcomes, indent=2, default=str))
            return 0
        if getattr(args, "json", False):
            print(_json.dumps([r.to_dict() for r in reqs], indent=2, default=str))
        else:
            print(summarize_requests(reqs))
        return 0

    if args.cmd == "propose":
        import json as _json

        from discovery_engine.agents.discovery_agent import DiscoveryAgent
        from discovery_engine.agents.skill_registry import default_registry
        from discovery_engine.discover._policy import AutonomyPolicy, AUTONOMOUS
        from discovery_engine.discover.actions import record_captures
        from discovery_engine.projection import engine as discovery_engine
        from discovery_engine.phases.generation import GateConfig, GenerationResult
        from discovery_engine.phases.generation_emit import generation_requests
        inv_path, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        result = _cycle_with_evidence(ws, args.investigation, inv_spec, studies)
        opps = discovery_engine.opportunities(result, inv_spec)
        if getattr(args, "free_run", False):
            gates = GateConfig.preset("free_run")
        else:
            gc = (inv_spec.get("discovery") or {}).get("gates") if isinstance(inv_spec, dict) else None
            gates = (GateConfig(default=gc.get("default"), by_kind=gc.get("by_kind") or {})
                     if isinstance(gc, dict) else GateConfig())
        reqs = generation_requests(opps, gates=gates, inv_spec=inv_spec)
        registry = default_registry()
        if not getattr(args, "fill", False):                       # dry-run: pure preview
            rows = []
            for r in reqs:
                sk = registry.get(r.kind)
                rows.append({"request_id": r.id, "kind": r.kind, "target": r.target,
                             "skill": sk.name if sk else None, "gate": r.gate})
            if args.json:
                print(_json.dumps(rows, indent=2, default=str))
            elif not rows:
                print("no requests to route")
            else:
                for x in rows:
                    print(f"{x['kind']}  {x['target']}  skill={x['skill'] or '(none)'}  gate={x['gate']}")
                print(f"\n{len(rows)} would be routed (dry-run; pass --fill to run the filler)")
            return 0
        from discovery_engine.phases.generation_apply import apply_result, subprocess_filler
        portfolio = DiscoveryAgent().propose(opps, fill=subprocess_filler(), gates=gates)
        record_captures(inv_path, portfolio.captures)
        pol = AutonomyPolicy(mode=AUTONOMOUS) if args.autonomous else AutonomyPolicy()
        by_id = {r.id: r for r in reqs}
        applied, queued, errors = [], [], []
        for cap in portfolio.captures:
            if cap.outcome != "proposed":
                continue
            req = by_id.get(cap.request_id)
            if req is not None and req.gate == "auto" and pol.allows(
                    consequence="authoring", target=req.target):
                try:
                    res = apply_result(req, GenerationResult(
                        request_id=req.id, artifact=cap.artifact, filler=cap.filler),
                        ws_root=ws, inv_slug=args.investigation)
                    applied.append({"request_id": req.id, "applied_to": res.get("applied_to", [])})
                except ValueError as e:
                    errors.append({"request_id": req.id, "reason": str(e)})
            else:
                queued.append(cap.request_id)
        summary = {"captured": len(portfolio.captures), "applied": len(applied),
                   "queued": len(queued), "skipped": len(portfolio.skipped),
                   "errors": len(errors), "applied_detail": applied,
                   "queued_ids": queued, "skipped_detail": portfolio.skipped}
        if args.json:
            print(_json.dumps(summary, indent=2, default=str))
        else:
            print(f"captured {summary['captured']}, applied {summary['applied']}, "
                  f"queued {summary['queued']}, skipped {summary['skipped']}, "
                  f"errors {summary['errors']}")
        return 0

    if args.cmd == "hypotheses":
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        specs = list(studies.values()) if isinstance(studies, dict) else list(studies or [])
        print(_summarize_hypotheses(inv_spec, specs))
        return 0

    if args.cmd == "report":
        import sys as _sys

        from discovery_engine.reporting.report import ReportLintBlocked, render_workspace_report
        try:
            out = render_workspace_report(ws, lint=not args.no_lint, force=args.force,
                                          today=args.today)
        except ReportLintBlocked as blocked:
            print(f"BLOCKING: report lint refused to render — "
                  f"{len(blocked.findings)} error-level finding(s):", file=_sys.stderr)
            for f in blocked.findings:
                print(f"  [error] {f.study_slug}:{f.field_path} — {f.message}", file=_sys.stderr)
            print("Re-run `discover report --force` to log overrides and render anyway, "
                  "or fix the findings (see `discover lint`).", file=_sys.stderr)
            return 1
        print(f"rendered workspace report -> {out}")
        return 0

    if args.cmd == "lint":
        import json as _json
        import sys as _sys

        from discovery_engine.reporting.linter import (
            apply_overrides,
            format_findings,
            lint_workspace_report,
            load_overrides,
            override_path,
            write_override,
        )
        findings = lint_workspace_report(ws, strict=args.strict, rulesets=args.ruleset)
        overrides = load_overrides(ws)
        visible = apply_overrides(findings, overrides)
        if args.json:
            print(_json.dumps([f.to_dict() for f in visible], indent=2))
        else:
            print(format_findings(visible))
        blocking = [f for f in findings if f.level == "error" and f.override_key not in overrides]
        if not blocking:
            return 0
        if args.force:
            for f in blocking:
                write_override(ws, f)
            print(f"--force: logged {len(blocking)} blocking finding(s) to "
                  f"{override_path(ws).relative_to(ws)}", file=_sys.stderr)
            return 0
        print(f"BLOCKING: {len(blocking)} error-level finding(s) — refusing publication. "
              "Re-run with --force to log overrides and proceed.", file=_sys.stderr)
        return 1

    if args.cmd == "populate-observations":
        from discovery_engine.study.finding_observations import main as _fo_main
        fo_argv = ["--workspace", str(ws)]
        fo_argv += ["--all"] if args.all else ["--study", args.study]
        return _fo_main(fo_argv)

    if args.cmd == "readout-migrate":
        from discovery_engine.study.readout_migration import main as _rm_main
        rm_argv = ["--workspace", str(ws), "--study", args.study]
        if args.write:
            rm_argv.append("--write")
        return _rm_main(rm_argv)

    if args.cmd == "band-provenance":
        import json as _json

        from discovery_engine.study.band_provenance import bands_missing_provenance
        sp = studies_dir(ws) / args.study / "study.yaml"
        if not sp.exists():
            parser.error(f"no study.yaml at {sp}")
        missing = bands_missing_provenance(load_yaml_mapping(sp))
        if args.json:
            print(_json.dumps(missing, indent=2, default=str))
        elif not missing:
            print(f"{args.study}: all acceptance bands have cites (no provenance gaps)")
        else:
            print(f"{args.study}: {len(missing)} band(s) missing provenance (no cites):")
            for m in missing:
                print(f"  [{m['kind']}] {m['name']}  {m['field_path']}  band={m['band']}")
        return 0

    if args.cmd == "linkage":
        import json as _json
        from collections import Counter

        from discovery_engine.investigation.linkage import build_index
        index = build_index(ws)
        if args.json:
            print(_json.dumps(index, indent=2, default=str))
        else:
            nodes = index.get("nodes") or []
            edges = index.get("edges") or []
            nt = Counter(n.get("type") for n in nodes)
            et = Counter(e.get("type") for e in edges)
            print(f"linkage graph: {len(nodes)} nodes, {len(edges)} edges")
            if nt:
                print("  nodes: " + ", ".join(f"{k}={v}" for k, v in sorted(nt.items())))
            if et:
                print("  edges: " + ", ".join(f"{k}={v}" for k, v in sorted(et.items())))
        return 0

    if args.cmd == "reconcile-evidence":
        from discovery_engine.phases import evidence_integrity as _ei
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        runs_by_study = _load_runs_by_study(ws, args.investigation, studies)
        print(_summarize_evidence_reconciliation(
            _ei.reconcile_evidence_state(inv_spec, studies, runs_by_study)))
        return 0

    if args.cmd == "plan":
        from discovery_engine.phases import evidence_integrity as _ei, candidate_action as _ca
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        runs_by_study = _load_runs_by_study(ws, args.investigation, studies)
        ev_state = _ei.reconcile_evidence_state(inv_spec, studies, runs_by_study)
        from discovery_engine.phases.model_architect import build_catalog
        result = run_cycle(inv_spec, studies, evidence_state=ev_state,
                           catalog=build_catalog(studies, ws))
        portfolio = _ca.select_portfolio(_ca.plan_candidate_actions(result, inv_spec, ev_state))
        print(_summarize_portfolio(portfolio))
        return 0

    if args.cmd == "evaluate":
        from discovery_engine.phases import evaluation as _ev, evidence_integrity as _ei
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        runs_by_study = _load_runs_by_study(ws, args.investigation, studies)
        ev_state = _ei.reconcile_evidence_state(inv_spec, studies, runs_by_study)
        cmp = _ev.compare_hypotheses(inv_spec, studies, ev_state)
        dmap = _ev.model_disagreement_map(inv_spec, studies)
        print(_summarize_evaluation(cmp, dmap))
        return 0

    if args.cmd == "delta":
        from discovery_engine.phases import evaluation as _ev, evidence_integrity as _ei
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        runs_by_study = _load_runs_by_study(ws, args.investigation, studies)
        ev_state = _ei.reconcile_evidence_state(inv_spec, studies, runs_by_study)
        print(_summarize_discovery_delta(_ev.discovery_delta(
            inv_spec, studies, ev_state,
            prev=(inv_spec.get("cycle_state") or {}).get("evaluation_snapshot"))))
        return 0

    if args.cmd == "design":
        if args.materialize:
            try:
                rep = materialize_design_study(ws, args.investigation, args.study,
                                               required_controls=args.require_control or None)
            except ValueError as e:
                parser.error(str(e))
            print(f"materialized design {rep['design_id']} -> study '{rep['child_slug']}' "
                  f"at {rep['path']}")
            if rep["control_issues"]:
                print("  control issues: " + "; ".join(rep["control_issues"]))
            print(f"  run it: discover --workspace {args.workspace} advance {args.investigation}")
            return 0
        from discovery_engine.phases import experiment_design as _ed
        _, inv_spec, _studies = load_cycle_inputs(ws, args.investigation)
        design = _ed.design_discriminating_study(inv_spec, args.study, args.require_control or None)
        print(_summarize_design(design, args.study))
        return 0

    if args.cmd == "build-model":
        from discovery_engine.phases import model_architect as _ma
        _, inv_spec, studies = load_cycle_inputs(ws, args.investigation)
        catalog = _ma.build_catalog(studies)
        req = _ma.ModelBuildRequest(question_id=args.question, required_mechanisms=args.mechanism,
                                    required_observables=args.observable or None)
        routes = _ma.choose_acquisition_route(req, catalog)
        mv = _ma.model_version(_ma.routes_composite(routes), {})
        print(_summarize_model_build(req, catalog, routes, mv))
        return 0

    if args.cmd == "seed-study":
        from discovery_engine.growth import seeding as _ss
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
        from discovery_engine.growth import refinement as _sr
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
        from discovery_engine.projection import engine as discovery_engine
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
