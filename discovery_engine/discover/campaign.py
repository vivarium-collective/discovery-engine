"""R1 — the unified cycle controller.

`run_discovery_campaign` is the engine-owned bounded loop that coordinates the EXISTING
primitives into one call: project the cycle (with evidence) -> apply the authorized frontier
actions (what ``revise --approve``/``--apply`` do by hand) -> advance/execute and grade the
ready studies (``advance_cycle`` + ``compute_outcomes``) -> persist the cycle -> repeat under
a budget. So a newly-applied study is run, graded, and its computed evidence drives the next
step WITHOUT any manual per-phase command.

It does **not** merge the pure :func:`run_cycle` projection (reads stay pure; only the
existing apply/advance writers touch disk), and it keeps the human-only acts human-gated:
steer/synthesize decisions, the adjudication of a disagreement, and reviewed Discovery
promotion are never performed here.

Every consequential action yields a durable :class:`ActionRecord` (id, input hash,
dependencies, policy decision, artifact ids, execution status, cycle index) — the ledger
that makes a campaign idempotent and resume-after-crash safe: a re-run skips a proposal that
is no longer ``proposed`` and advances nothing that is already graded. [0.7.2 plan §1, P0]
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
from pathlib import Path


@dataclasses.dataclass
class ActionRecord:
    """A durable record of one consequential campaign action (0.7.2 plan §1)."""
    id: str
    kind: str                     # revision_apply | run | design | ...
    target: str | None
    cycle_index: int
    policy_decision: str          # autonomous | gated
    execution_status: str         # applied | executed | blocked | failed | skipped
    input_hash: str
    artifacts: list               # child studies, run ids, model versions
    depends_on: list
    detail: str | None = None     # why a run failed/was gated (e.g. the stderr tail) — self-diagnosing

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _hash(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


# The §1 execution-state taxonomy. Execution is separated from verdict ("Progress ⊥ verdict":
# a failed execution is not a refuted hypothesis): a run that executed and whose prediction was
# wrong is `refuted` (the science spoke), distinct from `failed_execution` (the run itself did
# not produce a confirmed result). `completed` is never claimed without a runs.db-confirmed grade.
EXECUTION_STATES = ("completed", "refuted", "inconclusive", "failed_execution",
                    "blocked", "applied", "skipped", "gated", "executed")


def _run_execution_state(status, ws_root, inv_slug, target) -> str:
    """Map an advance run rec (+ its just-graded outcomes) onto EXECUTION_STATES."""
    if status == "needs_build":
        return "blocked"
    if status in ("failed", "error"):
        return "failed_execution"
    if status != "executed":
        return status or "skipped"
    from discovery_engine.discover.cycle import _study_dir_on_disk
    from discovery_engine.study.io import load_yaml
    slug = (target or "").split(":")[-1]
    sdir = _study_dir_on_disk(ws_root, inv_slug, slug) if slug else None
    if sdir is None:
        return "inconclusive"
    runs = (load_yaml(Path(sdir) / "study.yaml") or {}).get("runs") or []
    outcomes = (runs[-1] or {}).get("computed_outcomes") if runs else None
    if not outcomes:
        return "inconclusive"          # ran but not measured — never 'completed'
    # per-test verdicts only (computed_outcomes also carries non-test metadata entries)
    results = [o["result"] for o in outcomes.values()
               if isinstance(o, dict) and "result" in o]
    if any(r == "FAIL" for r in results):
        return "refuted"               # prediction wrong — scientifically refuted, not a failure
    if results and all(r == "PASS" for r in results):
        return "completed"             # runs.db-confirmed pass
    return "inconclusive"              # SKIP / ungraded / mixed-non-fail


def _ledger_path(inv_path: Path | str) -> Path:
    """The durable, append-only ActionRecord ledger for one investigation. Per-investigation
    (next to investigation.yaml) so parallel campaigns on sibling investigations never share
    one."""
    return Path(inv_path).parent / "campaign_ledger.jsonl"


def _load_ledger(path: Path) -> dict:
    """Read the ledger as {action_id -> record}. A crash mid-write leaves at worst a partial
    final line, which is skipped — the ledger is only ever appended to."""
    recs: dict = {}
    if not path.exists():
        return recs
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            recs[r["id"]] = r
        except (ValueError, KeyError):
            continue  # torn last line from a crash mid-append — ignore
    return recs


def run_discovery_campaign(ws_root: Path | str, inv_slug: str, *, autonomous: bool = False,
                           runner=None, budget=None, max_iterations: int = 8, policy=None,
                           gates=None, fill=None, apply_cli_verb=None, render_hook=None,
                           build_hook=None, worktree=None, resources=None, transcript=None) -> dict:
    """Drive one investigation through a bounded sequence of
    project -> apply -> advance/grade -> persist cycles, under ``budget`` and the autonomy
    ``policy`` (or the legacy ``autonomous`` bool). Returns
    ``{iterations, stop_reason, applied, applied_generations, applied_total, runs, actions,
    inv_path}`` where ``applied`` is model-revision applies, ``applied_generations`` the generated
    artifacts applied, ``applied_total`` their sum, and ``actions`` is the
    list of :class:`ActionRecord` dicts. A ``stop_reason`` of ``closed`` /
    ``budget_exhausted`` / ``no_informative_experiment`` / ``max_iterations`` is a legitimate
    halt, not a failure."""
    from discovery_engine.discover._policy import AutonomyPolicy
    from discovery_engine.discover.actions import apply_revision, review_revision
    from discovery_engine.discover.cycle import (
        _cycle_with_evidence, _load_runs_by_study, advance_cycle, load_cycle_inputs,
        persist_cycle, run_cycle)
    from discovery_engine.phases import evidence_integrity as ei
    from discovery_engine.phases.generation_apply import run_requests
    from discovery_engine.projection.closure_states import closure_states_from_cycle
    from discovery_engine.phases.generation_emit import generation_requests
    from discovery_engine.projection import engine as de
    from discovery_engine.discover.fill_isolation import isolate_fill

    # Isolation tool (shared with `discover investigate`): ground every generation request in the
    # author-declared resources and record each (request, result) to the transcript. Identity when
    # both are empty, so a campaign without them is byte-identical.
    fill = isolate_fill(fill, resources=resources, transcript=transcript)

    pol = AutonomyPolicy.resolve(policy, autonomous)
    sim_budget = getattr(budget, "simulation_runs", None)

    actions: list[ActionRecord] = []
    applied_total = 0
    # Generation requests already APPLIED this run, by request id. A generation act (e.g.
    # interpret_finding) writes prose but does not RESOLVE the frontier item it answers (an open
    # challenge / divergence stays open for real adjudication — interpretation is never evidence),
    # so the same opportunity re-emits every iteration. Re-applying it would re-interpret the same
    # target endlessly; instead the controller applies each generation target once and converges.
    applied_gen_ids: set[str] = set()
    # Run targets that already FAILED this run. Like applied_gen_ids for generation, this converges
    # the execute half: a persistently-failing run (e.g. a figure whose render env is broken) is
    # tried once, not re-attempted every iteration while other work progresses. A transient failure
    # is retried by re-running the campaign (a fresh run starts with an empty set).
    failed_run_targets: set[str] = set()
    stop_reason = None
    i = 0

    # Resolve the investigation + load the durable ActionRecord ledger up front, so a resume
    # (a fresh process after a crash) reconstructs what already happened: the simulation-budget
    # charge is replayed from prior executed runs, and each action is appended at most once.
    inv_path, _inv0, _st0 = load_cycle_inputs(ws_root, inv_slug)
    ledger_file = _ledger_path(inv_path)
    ledger = _load_ledger(ledger_file)
    recorded_ids = set(ledger)
    runs_done = sum(1 for r in ledger.values()
                    if r.get("kind") == "run"
                    and r.get("execution_status") in ("completed", "refuted", "inconclusive"))

    def _record(rec: ActionRecord) -> ActionRecord:
        actions.append(rec)
        if rec.id not in recorded_ids:          # durable, append-only, de-duplicated by id
            recorded_ids.add(rec.id)
            ledger_file.parent.mkdir(parents=True, exist_ok=True)
            with ledger_file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(rec.to_dict()) + "\n")
        return rec

    def _persist(ip, spec, sts):
        evs = ei.reconcile_evidence_state(
            spec, sts, _load_runs_by_study(Path(ws_root), inv_slug, sts))
        persist_cycle(ip, spec, run_cycle(spec, sts, evidence_state=evs),
                      studies=sts, evidence_state=evs)

    for i in range(1, max_iterations + 1):
        # Persist FIRST: materializes the frontier's model-revision proposals into
        # ``revision_proposals`` (so they can be approved/applied by id) and writes the slim
        # cycle_state. The derived proposal exists from the finding; persisting records it.
        inv_path, inv_spec, studies = load_cycle_inputs(ws_root, inv_slug)
        _persist(inv_path, inv_spec, studies)
        inv_path, inv_spec, studies = load_cycle_inputs(ws_root, inv_slug)
        cyc = _cycle_with_evidence(ws_root, inv_slug, inv_spec, studies)
        progressed = False

        # 1) Authorized apply of model-revision proposals — the autonomous equivalent of
        #    `revise --approve` then `revise --apply`. Adjudication of the resulting evidence
        #    stays human-gated; the controller only lands the child + model version.
        for item in de.frontier(cyc, inv_spec):
            if item.get("kind") != "revision_proposed":
                continue
            pid = item.get("target")
            prop = next((p for p in inv_spec.get("revision_proposals") or []
                         if isinstance(p, dict) and p.get("id") == pid), None)
            if prop is None or prop.get("status", "proposed") != "proposed":
                continue  # idempotent: already approved/applied/evaluated — never re-apply
            if not (pol.is_autonomous and pol.allows(consequence="authoring", target=pid)):
                _record(ActionRecord(
                    id=_hash("revision_apply", pid), kind="revision_apply", target=pid,
                    cycle_index=i, policy_decision="gated", execution_status="skipped",
                    input_hash=_hash(pid), artifacts=[], depends_on=[]))
                continue
            review_revision(inv_path, pid, "approved", by="engine",
                            rationale="engine-autonomous: apply to discriminate the model disagreement")
            rep = apply_revision(ws_root, inv_slug, pid)
            hist = (rep.get("history") or [{}])[-1]
            child, mv = hist.get("child_study"), hist.get("model_version")
            status = "applied" if rep.get("status") == "applied" else "blocked"
            _record(ActionRecord(
                id=_hash("revision_apply", pid), kind="revision_apply", target=pid,
                cycle_index=i, policy_decision="autonomous", execution_status=status,
                input_hash=_hash(pid), artifacts=[a for a in (child, mv) if a], depends_on=[]))
            if status == "applied":
                applied_total += 1
                progressed = True

        # 1b) Generation: emit the auto-gated GenerationRequests, fill them through the
        #     injected filler seam, and apply — the generative half of the loop. With no
        #     `fill` the campaign is a safe projection that applies no generated content;
        #     the engine itself never runs an LLM (generation lives only behind `fill`).
        if fill is not None:
            reqs = [r for r in generation_requests(
                        de.opportunities(cyc, inv_spec), gates=gates, inv_spec=inv_spec,
                        discrepancies=cyc.get("discrepancies"), studies=studies)
                    if r.gate == "auto" and r.id not in applied_gen_ids]   # don't re-apply what landed
            for o in run_requests(reqs, fill=fill, ws_root=ws_root, inv_slug=inv_slug,
                                  budget=budget, apply_cli_verb=apply_cli_verb) if reqs else []:
                oc = o.get("outcome")
                es = {"applied": "applied", "stop": "blocked",
                      "queued": "skipped"}.get(oc, oc or "skipped")
                _record(ActionRecord(
                    id=_hash("generate", o.get("request_id")),
                    kind=f"generate:{o.get('kind')}", target=o.get("request_id"),
                    cycle_index=i, policy_decision="autonomous", execution_status=es,
                    input_hash=_hash(o.get("request_id")),
                    artifacts=o.get("applied_to") or [], depends_on=[]))
                if es == "applied":
                    progressed = True
                    applied_gen_ids.add(o.get("request_id"))   # converge: apply each target once

        # 2) Advance: build-if-needed, run the ready studies, and grade them (compute_outcomes).
        #    Never counts a run as measured before runs.db confirms it — that is advance's job.
        #    A simulation budget of 0 is *projection-only*, not an exhausted budget: skip advance
        #    without halting (generation/revision still run, the stop reason stays honest). Only an
        #    actually-spent budget (runs were used) is the `budget_exhausted` stop.
        remaining = None if sim_budget is None else max(0, sim_budget - runs_done)
        if remaining == 0 and runs_done > 0:
            stop_reason = "budget_exhausted"
            break
        adv = ({} if remaining == 0 else
               advance_cycle(ws_root, inv_slug, policy=pol, runner=runner, render_hook=render_hook,
                             build_hook=build_hook, worktree=worktree, skip_targets=failed_run_targets,
                             **({"max_runs": remaining} if remaining is not None else {})))
        for r in adv.get("runs", []):
            st = r.get("status")
            # advance also dispatches the `reopen` op (staged feedback); record it as a typed
            # reopen, not a run, and do not charge it against the simulation budget. The
            # summarized rec carries no command, so key off advance's reopen outcome marker.
            is_reopen = "reopened the investigation" in (r.get("outcome") or "")
            if is_reopen:
                kind = "reopen"
                es = "executed" if st == "executed" else (st or "skipped")
            else:
                kind = "run"
                es = _run_execution_state(st, ws_root, inv_slug, r.get("target"))
            # Surface WHY a run failed/was gated (the runner's stderr tail or the gate detail) so a
            # failed_execution record is self-diagnosing instead of a dead end for the operator.
            detail = r.get("outcome") if es in ("failed_execution", "blocked", "gated") else None
            _record(ActionRecord(
                id=_hash(kind, r.get("target") or r.get("command")), kind=kind,
                target=r.get("target"), cycle_index=i, policy_decision="autonomous",
                execution_status=es, input_hash=_hash(r.get("target") or r.get("command")),
                artifacts=r.get("run_ids") or ([r["run_id"]] if r.get("run_id") else []),
                depends_on=[], detail=detail))
            ran = es in ("completed", "refuted", "inconclusive")   # the run actually executed
            if es == "failed_execution" and r.get("target"):
                failed_run_targets.add(r["target"])   # don't re-attempt it in a later iteration
            if is_reopen and st == "executed":
                progressed = True
            elif ran:
                progressed = True
                runs_done += 1
        for nb in adv.get("needs_build", []):
            _record(ActionRecord(
                id=_hash("needs_build", nb.get("study")), kind="run",
                target=nb.get("study"), cycle_index=i, policy_decision="autonomous",
                execution_status="blocked", input_hash=_hash(nb.get("study")),
                artifacts=[], depends_on=[]))

        # 3) Stop conditions (the next iteration's top persist, or the final persist below,
        #    captures this iteration's advance into cycle_state).
        if sim_budget is not None and runs_done >= sim_budget and runs_done > 0:
            stop_reason = "budget_exhausted"
            break
        if not progressed:
            # Honour the five closure states, not just the boolean. engine.closure() can read
            # "closed" (no unmet criterion/gap/challenge) while a claim still rests on
            # provisional-only evidence — reporting that as `closed` would hide a live frontier.
            # Only a genuinely resolved investigation stops `closed`; otherwise the controller made
            # no informative move this iteration, which is an honest, non-closing halt.
            evs = ei.reconcile_evidence_state(
                inv_spec, studies, _load_runs_by_study(Path(ws_root), inv_slug, studies))
            cs = closure_states_from_cycle(de.closure(cyc, inv_spec), inv_spec,
                                           studies=studies, evidence_state=evs)["states"]
            truly_closed = (cs["workflow_complete"] and cs["evidence_sufficient"]
                            and cs["model_adequate"] and cs["scientifically_resolved"])
            stop_reason = "closed" if truly_closed else "no_informative_experiment"
            break

    if stop_reason is None:
        stop_reason = "max_iterations"
    # Final persist: capture the last iteration's applied revisions + graded runs in cycle_state.
    if inv_path is not None:
        _ip, _spec, _sts = load_cycle_inputs(ws_root, inv_slug)
        _persist(_ip, _spec, _sts)
    # Honest apply counts: `applied` historically counted only model-revision applies (step 1a),
    # so a run that applied 19 generated artifacts but no revisions reported `applied: 0` — reading
    # as "nothing happened". Surface the generation applies (step 1b, which live in actions[]) and a
    # combined total; `applied` stays revision-only for back-compat.
    applied_generations = sum(1 for a in actions
                              if a.kind.startswith("generate:") and a.execution_status == "applied")
    return {"iterations": i, "stop_reason": stop_reason,
            "applied": applied_total,                              # model-revision applies (back-compat)
            "applied_generations": applied_generations,           # generated artifacts applied
            "applied_total": applied_total + applied_generations,  # everything that landed this run
            "runs": runs_done, "actions": [a.to_dict() for a in actions],
            "inv_path": str(inv_path) if inv_path else None,
            "ledger_path": str(ledger_file),
            "transcript": str(transcript) if transcript else None,
            "resources": len(resources) if resources else 0}
