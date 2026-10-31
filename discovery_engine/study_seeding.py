"""Autonomous study authoring + launch policy — Discovery Engine 2.0.

Closes the propose→seed→run→promote loop's one remaining host-driven step: turning a
proposal/parent-finding into a complete, **run-ready** ``study.yaml``. Previously
``seed-from-followup`` pre-populated child fragments but left the full spec to the
Claude host prose flow; ``build_seeded_study`` assembles the whole study as a pure
function, so the engine can author a new study itself.

Autonomy is a setting, not a default: ``select_for_launch`` gates which proposed
studies actually get seeded —

- ``human_in_the_loop`` (default): only proposals the human has approved (``approved_ids``)
  and that are not ``awaiting_expert`` launch; everything else is held for review.
- ``autonomous``: every ``ready`` (non-``awaiting_expert``) proposal launches without
  approval; ``awaiting_expert`` ones still need a decision (or an opt-in AI decider),
  per the DE2.0 rule that a general autonomous agent is gated on evidence integrity.

Derive-first: ``build_seeded_study`` + ``select_for_launch`` are pure; a thin CLI does
the file I/O (write the study.yaml, register it in the investigation).
"""
from __future__ import annotations

import re
from typing import Any

from .seed_from_followup import build_child_seed_from_finding

HUMAN_IN_THE_LOOP = "human_in_the_loop"
AUTONOMOUS = "autonomous"


def _slugify(text: str, fallback: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", str(text or "").lower()).strip("-")
    return s or fallback


def select_for_launch(suggestions: list[dict], *, autonomy: str = HUMAN_IN_THE_LOOP,
                      approved_ids: list[str] | None = None) -> dict:
    """Apply the autonomy policy to ``suggest_studies`` output. Returns
    ``{launch: [...], held: [{suggestion, reason}]}``. Never seeds or runs — it
    decides *which* proposals are eligible to seed under the configured policy."""
    approved = set(approved_ids or [])
    launch, held = [], []
    for s in suggestions or []:
        if not isinstance(s, dict):
            continue
        sid = s.get("id")
        if s.get("awaiting_expert") or s.get("status") == "awaiting_expert":
            held.append({"suggestion": s, "reason": "awaiting expert decision — resolve it first "
                                                     "(or wire an AI decider)"})
        elif autonomy == AUTONOMOUS:
            launch.append(s)
        elif sid in approved:
            launch.append(s)
        else:
            held.append({"suggestion": s, "reason": "human-in-the-loop — not yet approved for launch"})
    return {"launch": launch, "held": held, "autonomy": autonomy}


def _inherit_arm(parent_spec: dict, model_change: dict | None) -> list[dict]:
    """The seeded study's baseline: the parent's baseline composite, with the
    proposal's ``model_change`` ``params``/``composite`` overrides applied — run-ready by construction."""
    base = next((a for a in (parent_spec.get("baseline") or []) if isinstance(a, dict)), None)
    if not base or not base.get("composite"):
        return []
    params = dict(base.get("params") or {})
    for k, v in ((model_change or {}).get("params") or {}).items():
        params[k] = v
    composite = (model_change or {}).get("composite") or base["composite"]
    arm = {"name": "baseline", "composite": composite, "params": params}
    if (model_change or {}).get("note") or (model_change or {}).get("notes"):
        arm["note"] = model_change.get("note") or model_change.get("notes")
    return [arm]


def build_seeded_study(parent_spec: dict, inv_spec: dict, *, new_slug: str,
                       finding_id: str | None = None, proposal: dict | None = None,
                       model_change: dict | None = None, title: str | None = None) -> dict:
    """Author a complete, run-ready ``study.yaml`` dict for a new study seeded from a
    parent study (and optionally one of its findings).

    Run-ready + evaluable by construction: it inherits the parent's baseline composite,
    ``observables`` (emit paths) and ``behavior_tests`` (measure + pass_if), so the new
    study imports, emits what its gate needs, and is scored by the same evaluators —
    then applies the proposal's ``model_change`` param overrides as the variation under
    test. Pure — writes nothing. ``finding_id`` (when given) folds in the ChildSeed
    (purpose / key_assumptions / pipeline_gate / seeded_from / model_change)."""
    parent_slug = parent_spec.get("name") or parent_spec.get("slug") or "parent"
    proposal = proposal or {}
    proposal_id = proposal.get("id") or new_slug
    mc = model_change or proposal.get("model_change") or {}

    study: dict[str, Any] = {
        "schema_version": parent_spec.get("schema_version", 4),
        "name": new_slug,
        "investigation": inv_spec.get("name") or parent_spec.get("investigation"),
        "title": title or proposal.get("title") or f"Follow-up on {parent_slug}",
        "status": "open",
        "phase": "design",
        "topic": parent_spec.get("topic"),
        "tags": list(parent_spec.get("tags") or []),
        "question": proposal.get("motivation") or proposal.get("question"),
        "parent_studies": [parent_slug],
        "baseline": _inherit_arm(parent_spec, mc),
        "observables": list(parent_spec.get("observables") or []),
        "behavior_tests": [dict(t) for t in (parent_spec.get("behavior_tests") or parent_spec.get("tests") or [])
                           if isinstance(t, dict)],
        "seeded_from": {"study": parent_slug, "proposal_id": proposal_id},
        "findings": [],
    }
    if proposal.get("hypotheses"):
        study["hypotheses"] = proposal["hypotheses"]

    # Fold the finding-derived child seed (purpose/key_assumptions/pipeline_gate/
    # behavior_tests carryover/model_change/seeded_from.finding) when a finding drives it.
    if finding_id:
        seed = build_child_seed_from_finding(parent_spec, parent_slug, proposal_id, finding_id)
        study = seed.merge_into(study)

    # Drop empty optional keys so the authored file stays clean.
    return {k: v for k, v in study.items() if v not in (None, [], {})}


def is_run_ready(study: dict) -> list[str]:
    """Quick structural check that a seeded study can be run + evaluated: a baseline
    composite, at least one behavior test with a measure, and declared observables.
    Returns a list of gaps (empty = ready). Not a substitute for ``run_readiness``
    (which also imports the composite), but catches an un-authorable seed early."""
    gaps = []
    base = next((a for a in (study.get("baseline") or []) if isinstance(a, dict)), None)
    if not (base and base.get("composite")):
        gaps.append("no baseline composite")
    tests = study.get("behavior_tests") or study.get("tests") or []
    if not any(isinstance(t, dict) and (t.get("measure") or {}).get("path") for t in tests):
        gaps.append("no behavior test with a measure")
    if not study.get("observables"):
        gaps.append("no declared observables (run won't emit the gate's source)")
    return gaps
