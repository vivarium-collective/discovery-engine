"""Roll member-study verdicts → investigation acceptance status.

Spine stage #2 (coded gate/verdict/acceptance roll-up), Task 3.

Public API
----------
roll_up_acceptance(inv_spec, studies_by_name) -> dict
    Pure function: per acceptance_criteria entry → member study's canonical
    outcome for that behavior → aggregated {verdict_status, criteria, unmet}.

write_investigation_acceptance(inv_dir, workspace) -> bool
    Ruamel round-trip write of executive.computed_verdict_status +
    computed_acceptance (parallel coded slot). NEVER overwrites authored
    executive.verdict / verdict_status. Returns True when changed.
"""
from __future__ import annotations

from pathlib import Path

from .study_outcomes import canonical_outcomes


# ---------------------------------------------------------------------------
# Result vocabulary (per-criterion and aggregate)
# ---------------------------------------------------------------------------
_CRIT_PASS = "passing"
_CRIT_FAIL = "failing"
_CRIT_CAVEATS = "passing-with-caveats"
_CRIT_PROGRESS = "in-progress"

# Per-outcome result string → criterion result mapping
# Sources: _TEST_PASS / _TEST_FAIL / _TEST_SKIP from study_status
_OUTCOME_PASS = {"pass", "passed", "ok"}
_OUTCOME_FAIL = {"fail", "failed", "error"}
_OUTCOME_PARTIAL = {"skip", "skipped", "inconclusive", "partial"}


def _criterion_result(behavior_result: str | None) -> str:
    """Map a single behavior outcome result → criterion result."""
    r = str(behavior_result or "").strip().lower()
    if r in _OUTCOME_PASS:
        return _CRIT_PASS
    if r in _OUTCOME_FAIL:
        return _CRIT_FAIL
    if r in _OUTCOME_PARTIAL:
        return _CRIT_CAVEATS
    # None / missing / pending
    return _CRIT_PROGRESS


def _aggregate(criterion_results: list[str]) -> str:
    """Aggregate per-criterion results into an investigation verdict_status.

    Priority: any FAIL → failing; any in-progress → in-progress;
    any caveats (no fail, no in-progress) → passing-with-caveats; else → passing.
    """
    if _CRIT_FAIL in criterion_results:
        return "failing"
    if _CRIT_PROGRESS in criterion_results:
        return "in-progress"
    if _CRIT_CAVEATS in criterion_results:
        return "passing-with-caveats"
    return "passing"


# ---------------------------------------------------------------------------
# Task 3a: roll_up_acceptance (pure)
# ---------------------------------------------------------------------------

def roll_up_acceptance(inv_spec: dict, studies_by_name: dict) -> dict:
    """Compute the investigation acceptance from member study outcomes.

    Parameters
    ----------
    inv_spec : dict
        The investigation spec. Must carry ``acceptance_criteria: [{study, behavior}]``.
    studies_by_name : dict
        Mapping from study slug → study spec (e.g. ``{slug: load_yaml(study.yaml)}``).

    Returns
    -------
    dict with keys:
        verdict_status : str — aggregate result string
        criteria       : list[dict] — per-criterion {study, behavior, result}
        unmet          : list[dict] — criteria that are not "passing"
    """
    criteria_spec = inv_spec.get("acceptance_criteria") or []
    per_criterion: list[dict] = []

    for entry in criteria_spec:
        if not isinstance(entry, dict):
            continue
        study_slug = entry.get("study", "")
        behavior = entry.get("behavior", "")

        study_spec = studies_by_name.get(study_slug)
        if study_spec is None:
            # Study not found in workspace → pending/in-progress
            result = _CRIT_PROGRESS
        else:
            outcomes = canonical_outcomes(study_spec)
            out = outcomes.get(behavior)
            raw = out.get("result") if isinstance(out, dict) else out
            result = _criterion_result(raw)

        per_criterion.append({
            "study": study_slug,
            "behavior": behavior,
            "result": result,
        })

    results = [c["result"] for c in per_criterion]
    verdict = _aggregate(results) if results else "in-progress"
    unmet = [c for c in per_criterion if c["result"] != _CRIT_PASS]

    return {
        "verdict_status": verdict,
        "criteria": per_criterion,
        "unmet": unmet,
    }


# ---------------------------------------------------------------------------
# Task 3b: write_investigation_acceptance (parallel coded slot, never clobber)
# ---------------------------------------------------------------------------

def write_investigation_acceptance(inv_dir, workspace=None) -> bool:
    """Write computed acceptance into investigation.yaml (parallel coded slot).

    Writes ONLY:
        executive.computed_verdict_status
        computed_acceptance: {criteria, unmet, diverges_from_authored}

    The authored ``executive.verdict`` / ``executive.verdict_status`` are NEVER
    modified. Uses ruamel round-trip to preserve YAML comments.

    Parameters
    ----------
    inv_dir : Path-like
        Directory containing ``investigation.yaml``.
    workspace : Path-like or None
        Workspace root for loading member studies (falls back to inv_dir parent's parent).
        May be a path containing ``workspace.yaml``.

    Returns True when investigation.yaml was rewritten, False on no-op.
    """
    from io import StringIO

    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap, CommentedSeq

    from . import study_io
    from .workspace_paths import WorkspacePaths

    inv_dir = Path(inv_dir)
    inv_yaml = inv_dir / "investigation.yaml"
    inv_spec = study_io.load_yaml_mapping(inv_yaml)

    # Resolve workspace paths for loading member studies
    if workspace is None:
        # Heuristic: investigations/<slug>/ → workspace root is two levels up
        workspace = inv_dir.parent.parent
    wp = WorkspacePaths.load(workspace)

    # Load member study specs
    studies_by_name: dict = {}
    for study_dir in wp.iter_study_dirs():
        slug = study_dir.name
        yaml_path = study_dir / "study.yaml"
        if yaml_path.exists():
            try:
                studies_by_name[slug] = study_io.load_yaml_mapping(yaml_path)
            except Exception:  # noqa: BLE001
                pass

    acceptance = roll_up_acceptance(inv_spec, studies_by_name)

    # diverges_from_authored: authored verdict_status != computed
    authored_vs = str(
        (inv_spec.get("executive") or {}).get("verdict_status") or ""
    ).strip().lower()
    diverges = (authored_vs != "") and (authored_vs != acceptance["verdict_status"])

    new_acceptance: dict = {
        "criteria": acceptance["criteria"],
        "unmet": acceptance["unmet"],
        "diverges_from_authored": diverges,
    }
    new_computed_vs = acceptance["verdict_status"]

    # Round-trip load
    ryaml = YAML()
    ryaml.preserve_quotes = True
    ryaml.width = 4096

    rt_spec = ryaml.load(inv_yaml.read_text(encoding="utf-8"))
    if rt_spec is None:
        rt_spec = {}

    # Locate or create executive mapping
    if not isinstance(rt_spec.get("executive"), dict):
        rt_spec["executive"] = CommentedMap()
    exec_map = rt_spec["executive"]

    # Check for no-op
    existing_vs = exec_map.get("computed_verdict_status")
    existing_acc = exec_map.get("computed_acceptance")
    existing_plain_acc: dict | None = None
    if isinstance(existing_acc, dict):
        existing_plain_acc = {
            "criteria": [dict(c) for c in (existing_acc.get("criteria") or [])],
            "unmet": [dict(u) for u in (existing_acc.get("unmet") or [])],
            "diverges_from_authored": existing_acc.get("diverges_from_authored"),
        }
    if (
        existing_vs == new_computed_vs
        and existing_plain_acc is not None
        and existing_plain_acc["criteria"] == new_acceptance["criteria"]
        and existing_plain_acc["unmet"] == new_acceptance["unmet"]
        and existing_plain_acc["diverges_from_authored"] == new_acceptance["diverges_from_authored"]
    ):
        return False  # idempotent no-op

    # Write ONLY the computed slots — never touch authored verdict/verdict_status
    exec_map["computed_verdict_status"] = new_computed_vs

    ca = CommentedMap()
    crit_seq = CommentedSeq()
    for c in new_acceptance["criteria"]:
        cm = CommentedMap()
        cm["study"] = c["study"]
        cm["behavior"] = c["behavior"]
        cm["result"] = c["result"]
        crit_seq.append(cm)
    ca["criteria"] = crit_seq

    unmet_seq = CommentedSeq()
    for u in new_acceptance["unmet"]:
        um = CommentedMap()
        um["study"] = u["study"]
        um["behavior"] = u["behavior"]
        um["result"] = u["result"]
        unmet_seq.append(um)
    ca["unmet"] = unmet_seq
    ca["diverges_from_authored"] = new_acceptance["diverges_from_authored"]

    exec_map["computed_acceptance"] = ca

    buf = StringIO()
    ryaml.dump(rt_spec, buf)
    study_io.atomic_write(inv_yaml, buf.getvalue())
    return True
