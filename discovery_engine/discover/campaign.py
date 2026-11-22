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

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _hash(*parts) -> str:
    return hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:16]


def run_discovery_campaign(ws_root: Path | str, inv_slug: str, *, autonomous: bool = False,
                           runner=None, budget=None, max_iterations: int = 8, policy=None,
                           gates=None, render_hook=None, build_hook=None, worktree=None) -> dict:
    """Drive one investigation through a bounded sequence of
    project -> apply -> advance/grade -> persist cycles, under ``budget`` and the autonomy
    ``policy`` (or the legacy ``autonomous`` bool). Returns
    ``{iterations, stop_reason, applied, runs, actions, inv_path}`` where ``actions`` is the
    list of :class:`ActionRecord` dicts. A ``stop_reason`` of ``closed`` /
    ``budget_exhausted`` / ``no_informative_experiment`` / ``max_iterations`` is a legitimate
    halt, not a failure."""
    from discovery_engine.discover._policy import AutonomyPolicy
    from discovery_engine.discover.actions import apply_revision, review_revision
    from discovery_engine.discover.cycle import (
        _cycle_with_evidence, _load_runs_by_study, advance_cycle, load_cycle_inputs,
        persist_cycle, run_cycle)
    from discovery_engine.phases import evidence_integrity as ei
    from discovery_engine.projection import engine as de

    pol = AutonomyPolicy.resolve(policy, autonomous)
    sim_budget = getattr(budget, "simulation_runs", None)

    actions: list[ActionRecord] = []
    applied_total = 0
    runs_done = 0
    stop_reason = None
    inv_path = None
    i = 0

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
                actions.append(ActionRecord(
                    id=_hash("revision_apply", pid, i), kind="revision_apply", target=pid,
                    cycle_index=i, policy_decision="gated", execution_status="skipped",
                    input_hash=_hash(pid), artifacts=[], depends_on=[]))
                continue
            review_revision(inv_path, pid, "approved", by="engine",
                            rationale="engine-autonomous: apply to discriminate the model disagreement")
            rep = apply_revision(ws_root, inv_slug, pid)
            hist = (rep.get("history") or [{}])[-1]
            child, mv = hist.get("child_study"), hist.get("model_version")
            status = "applied" if rep.get("status") == "applied" else "blocked"
            actions.append(ActionRecord(
                id=_hash("revision_apply", pid, i), kind="revision_apply", target=pid,
                cycle_index=i, policy_decision="autonomous", execution_status=status,
                input_hash=_hash(pid), artifacts=[a for a in (child, mv) if a], depends_on=[]))
            if status == "applied":
                applied_total += 1
                progressed = True

        # 2) Advance: build-if-needed, run the ready studies, and grade them (compute_outcomes).
        #    Never counts a run as measured before runs.db confirms it — that is advance's job.
        remaining = None if sim_budget is None else max(0, sim_budget - runs_done)
        if remaining == 0:
            stop_reason = "budget_exhausted"
            break
        adv = advance_cycle(ws_root, inv_slug, policy=pol, runner=runner, render_hook=render_hook,
                            build_hook=build_hook, worktree=worktree,
                            **({"max_runs": remaining} if remaining is not None else {}))
        for r in adv.get("runs", []):
            st = r.get("status")
            es = {"executed": "executed", "needs_build": "blocked"}.get(st, st or "skipped")
            # advance also dispatches the `reopen` op (staged feedback); record it as a typed
            # reopen, not a run, and do not charge it against the simulation budget. The
            # summarized rec carries no command, so key off advance's reopen outcome marker.
            is_reopen = "reopened the investigation" in (r.get("outcome") or "")
            kind = "reopen" if is_reopen else "run"
            actions.append(ActionRecord(
                id=_hash(kind, r.get("target") or r.get("command"), i), kind=kind,
                target=r.get("target"), cycle_index=i, policy_decision="autonomous",
                execution_status=es, input_hash=_hash(r.get("target") or r.get("command")),
                artifacts=r.get("run_ids") or ([r["run_id"]] if r.get("run_id") else []),
                depends_on=[]))
            if es == "executed":
                progressed = True
                if not is_reopen:
                    runs_done += 1
        for nb in adv.get("needs_build", []):
            actions.append(ActionRecord(
                id=_hash("needs_build", nb.get("study"), i), kind="run",
                target=nb.get("study"), cycle_index=i, policy_decision="autonomous",
                execution_status="blocked", input_hash=_hash(nb.get("study")),
                artifacts=[], depends_on=[]))

        # 3) Stop conditions (the next iteration's top persist, or the final persist below,
        #    captures this iteration's advance into cycle_state).
        if sim_budget is not None and runs_done >= sim_budget:
            stop_reason = "budget_exhausted"
            break
        if not progressed:
            stop_reason = "closed" if de.closure(cyc, inv_spec).get("closed") \
                else "no_informative_experiment"
            break

    if stop_reason is None:
        stop_reason = "max_iterations"
    # Final persist: capture the last iteration's applied revisions + graded runs in cycle_state.
    if inv_path is not None:
        _ip, _spec, _sts = load_cycle_inputs(ws_root, inv_slug)
        _persist(_ip, _spec, _sts)
    return {"iterations": i, "stop_reason": stop_reason, "applied": applied_total,
            "runs": runs_done, "actions": [a.to_dict() for a in actions],
            "inv_path": str(inv_path) if inv_path else None}
