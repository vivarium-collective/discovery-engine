"""One autonomy policy for every consequential cycle op.

PR 1 of the generative-cycle completion. Before this, autonomy was two disconnected
things:

- a bare ``autonomous: bool`` threaded through :func:`execute_action` /
  :func:`advance_cycle` — the *live* gate, but narrow (it cannot express "run only
  this one approved item"); and
- :func:`discovery_engine.growth.seeding.select_for_launch`'s
  ``human_in_the_loop`` / ``autonomous`` / ``approved_ids`` policy — richer, but with
  **no production caller** (referenced only in a comment).

This module unifies them into a single :class:`AutonomyPolicy` that every consequential
op (run / seed / build / render / reopen) passes through, plus the per-decision log
record the roadmap specifies (``{policy_version, actor, input_hashes, rationale,
consequence, result}``).

Back-compat: :meth:`AutonomyPolicy.resolve` turns the legacy ``autonomous`` bool into a
policy, so every existing caller (``autonomous=True`` / ``False``) keeps working unchanged.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Iterable

HUMAN_IN_THE_LOOP = "human_in_the_loop"
AUTONOMOUS = "autonomous"
POLICY_VERSION = "1"


@dataclass(frozen=True)
class AutonomyPolicy:
    """How much the engine may do without a human — one object, consulted by every
    consequential op.

    - ``mode`` — ``human_in_the_loop`` (default) or ``autonomous``.
    - ``approved_ids`` — frontier-item targets a human has pre-approved; they run even in
      HITL mode (this is what makes per-item approval expressible, which the bare bool
      could not).
    - ``allowlist`` — the consequence tiers permitted to run (``None`` = all). Lets a
      policy say e.g. "autonomous, but only renders" or "autonomous, but never authoring".
    - ``budget`` — max consequential actions this policy authorizes (``None`` = unbounded);
      :func:`advance_cycle` treats it as its run cap.
    - ``policy_version`` — stamped into every decision record for provenance.
    """

    mode: str = HUMAN_IN_THE_LOOP
    approved_ids: frozenset = field(default_factory=frozenset)
    allowlist: frozenset | None = None
    budget: int | None = None
    policy_version: str = POLICY_VERSION

    @property
    def is_autonomous(self) -> bool:
        return self.mode == AUTONOMOUS

    @classmethod
    def resolve(cls, policy: "AutonomyPolicy | None" = None, autonomous: bool = False,
                approved_ids: Iterable[str] | None = None) -> "AutonomyPolicy":
        """Coerce the legacy ``(policy | autonomous bool)`` pair into an AutonomyPolicy.

        An explicit :class:`AutonomyPolicy` wins (with any extra ``approved_ids`` merged in);
        otherwise the bool maps to ``autonomous`` / ``human_in_the_loop``.
        """
        if isinstance(policy, cls):
            if approved_ids:
                return replace(policy, approved_ids=policy.approved_ids | frozenset(approved_ids))
            return policy
        return cls(mode=AUTONOMOUS if autonomous else HUMAN_IN_THE_LOOP,
                   approved_ids=frozenset(approved_ids or ()))

    def allows(self, *, consequence: str, target: str | None = None) -> bool:
        """May a CONSEQUENTIAL action (run / seed / build / render) run now?

        ``auto_safe`` actions never consult the policy. Autonomous mode runs anything on the
        allowlist; human-in-the-loop mode runs only a pre-approved ``target``.
        """
        if self.allowlist is not None and consequence not in self.allowlist:
            return False
        if self.is_autonomous:
            return True
        return target is not None and target in self.approved_ids


def decision_record(policy: AutonomyPolicy, *, actor: str, consequence: str, target,
                    rationale: str, result, input_hashes: Iterable[str] | None = None) -> dict:
    """The per-decision provenance record the roadmap specifies:
    ``{policy_version, actor, input_hashes, target, consequence, rationale, result}``.

    Attached to each executed consequential action so the audit trail records *why* the
    engine was allowed to run it, not just that it ran.
    """
    return {"policy_version": policy.policy_version, "actor": actor,
            "input_hashes": list(input_hashes or []), "target": target,
            "consequence": consequence, "rationale": rationale, "result": result}
