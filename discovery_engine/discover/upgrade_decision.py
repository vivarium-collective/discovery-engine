"""The model-upgrade decision — audit an investigation's verified evidence and
decide, per candidate model change, whether it is justified.

This closes the last hop the design spec left open
(``docs/superpowers/specs/2026-10-30-closed-loop-upgrade-decision.md`` §7): the
engine can classify evidence (``reconcile_evidence_state``), promote claims
(``compare_hypotheses``), and record human approve/reject of a revision
(``review_revision``), but nothing *gates a model-update decision on the
verified-evidence ledger*. ``run_upgrade_decision`` is that gate. It is the
composing procedure the spec asks for — it adds no new evidence rule; it reads
the existing ledger and turns it into a defensible, reproducible go/no-go.

A candidate change is authored on the investigation under ``upgrade_candidates``::

    upgrade_candidates:
      - id: krela-stability-fix
        change: "Reduce RelA synthesis gain (k_RelA x0.6) in ecoli_baseline"
        target: "v2ecoli.composites.ecoli_baseline"   # the composite/param it changes
        requires:   [grc01-f2, grc02-f2]  # findings that must be VERIFIED for GO
        contradicted_if: [grc03-f1]        # findings whose CONTRADICTED state blocks
        rationale: "..."                    # authored argument for the change

The verdict ladder (strict, evidence-gated):

* **no-go**     — any ``requires`` or ``contradicted_if`` finding is CONTRADICTED.
* **go**        — every ``requires`` finding is VERIFIED and none is contradicted.
* **insufficient** — otherwise: a required finding is UNSUPPORTED / PROVISIONAL
  (the evidence exists but is not yet verified), so the change is not yet
  justified. The reason names exactly which finding and why.

The decision is recorded with the **evidence signature** it was made against
(a hash of the relevant ledger rows), so a later re-run whose evidence has
drifted is flagged ``stale`` — the "come back to this point if needed" contract,
the same mechanism ``snapshot-validations`` uses for validators.
"""
from __future__ import annotations

import hashlib
import json

from discovery_engine.phases.evidence_integrity import (
    CONTRADICTED,
    PROVISIONAL,
    UNSUPPORTED,
    VERIFIED,
    reconcile_evidence_state,
)

GO = "go"
NO_GO = "no-go"
INSUFFICIENT = "insufficient"

# Ordering for the ranked report: a justified change first, a blocked one last.
_VERDICT_RANK = {GO: 0, INSUFFICIENT: 1, NO_GO: 2}


def _ledger_by_finding(ev_state: dict) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for row in (ev_state.get("ledger") or []):
        if isinstance(row, dict) and row.get("finding"):
            out[str(row["finding"])] = row
    return out


def _status_rows(ledger: dict[str, dict], ids: list[str]) -> list[dict]:
    """Classification rows for the named findings, in declared order. A finding
    the ledger does not know (bad id / dropped study) is surfaced as its own
    ``unknown`` row so a decision can never silently rest on a missing finding."""
    rows = []
    for fid in ids:
        row = ledger.get(fid)
        if row is None:
            rows.append({"finding": fid, "status": "unknown",
                         "reason": "no such finding in the evidence ledger"})
        else:
            rows.append(row)
    return rows


def _evidence_signature(rows: list[dict]) -> str:
    """A content hash of the ledger rows a decision rests on — its meaning, not
    its provenance. Re-running against drifted evidence yields a different
    signature, which flags the recorded decision stale."""
    payload = [
        {"finding": r.get("finding"), "status": r.get("status"),
         "observed": r.get("observed"), "measured_value": r.get("measured_value")}
        for r in rows
    ]
    payload.sort(key=lambda d: str(d.get("finding")))
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:16]


def _as_ids(value) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(v) for v in value if str(v).strip()]


def decide_candidate(candidate: dict, ledger: dict[str, dict]) -> dict:
    """Gate one authored candidate change against the evidence ledger. Pure.

    Returns ``{id, change, target, verdict, rationale, required, contradicted,
    evidence_signature}`` where ``required`` / ``contradicted`` are the per-finding
    classification rows the verdict rests on."""
    cid = candidate.get("id") or candidate.get("change") or "candidate"
    requires = _as_ids(candidate.get("requires"))
    contra_if = _as_ids(candidate.get("contradicted_if"))
    req_rows = _status_rows(ledger, requires)
    contra_rows = _status_rows(ledger, contra_if)

    contradicted = [r for r in (req_rows + contra_rows) if r.get("status") == CONTRADICTED]
    not_yet = [r for r in req_rows if r.get("status") in (UNSUPPORTED, PROVISIONAL, "unknown")]
    verified = [r for r in req_rows if r.get("status") == VERIFIED]

    if contradicted:
        verdict = NO_GO
        why = ("evidence CONTRADICTS the change: "
               + "; ".join(f"{r['finding']} ({r.get('reason', '')})" for r in contradicted))
    elif not requires:
        verdict = INSUFFICIENT
        why = ("no required evidence declared — a change must name the findings that must be "
               "VERIFIED (requires:) before it can be judged justified")
    elif not_yet:
        verdict = INSUFFICIENT
        why = ("required evidence is not yet VERIFIED: "
               + "; ".join(f"{r['finding']} [{r.get('status')}] {r.get('reason', '')}".rstrip()
                           for r in not_yet))
    else:
        verdict = GO
        why = ("every required finding is VERIFIED against a code-graded registered run: "
               + ", ".join(r["finding"] for r in verified))

    return {
        "id": cid,
        "change": candidate.get("change") or cid,
        "target": candidate.get("target"),
        "verdict": verdict,
        "rationale": why,
        "authored_rationale": candidate.get("rationale"),
        "required": req_rows,
        "contradicted_if": contra_rows,
        "n_verified": len(verified),
        "evidence_signature": _evidence_signature(req_rows + contra_rows),
    }


def run_upgrade_decision(inv_spec: dict, studies: dict, runs_by_study: dict | None = None,
                         *, candidate_id: str | None = None) -> dict:
    """Audit an investigation's evidence and decide each candidate model change.

    Reconciles evidence (the same ledger ``reconcile-evidence`` prints), then
    gates every ``inv_spec['upgrade_candidates']`` entry (or just ``candidate_id``)
    against it and ranks them (go → insufficient → no-go, verified-support as the
    tiebreak). Pure. Returns ``{candidates, recommended, evidence_summary,
    evidence_signature}``."""
    ev_state = reconcile_evidence_state(inv_spec, studies, runs_by_study or {})
    ledger = _ledger_by_finding(ev_state)

    declared = [c for c in (inv_spec.get("upgrade_candidates") or []) if isinstance(c, dict)]
    if candidate_id:
        declared = [c for c in declared if (c.get("id") == candidate_id)]

    decided = [decide_candidate(c, ledger) for c in declared]
    decided.sort(key=lambda d: (_VERDICT_RANK.get(d["verdict"], 9), -d["n_verified"], str(d["id"])))

    gos = [d for d in decided if d["verdict"] == GO]
    recommended = gos[0]["id"] if gos else None
    overall_sig = _evidence_signature([r for d in decided for r in d["required"] + d["contradicted_if"]])

    return {
        "investigation": inv_spec.get("name") or inv_spec.get("slug"),
        "candidates": decided,
        "recommended": recommended,
        "evidence_summary": ev_state.get("summary"),
        "evidence_signature": overall_sig,
    }


def format_upgrade_decision(result: dict) -> str:
    """Human-readable report of a :func:`run_upgrade_decision` result."""
    s = result.get("evidence_summary") or {}
    lines = [
        f"model-upgrade decision — {result.get('investigation')}",
        (f"  evidence: {s.get(VERIFIED, 0)} verified · {s.get(PROVISIONAL, 0)} provisional · "
         f"{s.get(CONTRADICTED, 0)} contradicted · {s.get(UNSUPPORTED, 0)} unsupported"),
        "",
    ]
    for d in result.get("candidates") or []:
        mark = {"go": "GO ", "no-go": "NO-GO", "insufficient": "INSUFF"}.get(d["verdict"], d["verdict"])
        lines.append(f"[{mark:>6}] {d['id']} — {d['change']}")
        lines.append(f"           {d['rationale']}")
    rec = result.get("recommended")
    lines += ["", (f"recommended: adopt '{rec}' — evidence justifies it"
                   if rec else "recommended: none — no candidate is justified by verified evidence yet")]
    lines.append(f"evidence signature: {result.get('evidence_signature')}  "
                 "(a later re-run whose evidence differs is flagged stale)")
    return "\n".join(lines)


def record_upgrade_decision(inv_path, result: dict, *, by: str | None = None) -> dict:
    """Persist a decision audit to ``investigation.yaml``: an ``upgrade_decision``
    block (the ranked verdicts + the evidence signature it was made against) plus,
    for each GO / NO-GO candidate, an ``open_decision`` resolution so the frontier
    item is discharged. Idempotent on candidate id. Returns the stored block.

    The recorded ``evidence_signature`` is the revisit anchor: re-run the audit and
    compare — a different signature means the evidence changed and the decision
    must be reconsidered (the ``snapshot-validations`` contract for decisions)."""
    import datetime as _dt
    from pathlib import Path

    from discovery_engine.discover.actions import add_resolution
    from discovery_engine.study.io import load_yaml_mapping, save_yaml_atomic

    path = Path(inv_path)
    spec = load_yaml_mapping(path)
    block = {
        "at": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "recommended": result.get("recommended"),
        "evidence_signature": result.get("evidence_signature"),
        "candidates": [
            {"id": d["id"], "change": d["change"], "verdict": d["verdict"],
             "rationale": d["rationale"], "evidence_signature": d["evidence_signature"]}
            for d in (result.get("candidates") or [])
        ],
    }
    if by:
        block["by"] = by
    spec = dict(spec)
    spec["upgrade_decision"] = block
    # Discharge each decided candidate as an open_decision resolution (go / no-go are
    # decisions; an insufficient candidate stays open — it is not yet decided).
    for d in (result.get("candidates") or []):
        if d["verdict"] in (GO, NO_GO):
            spec = add_resolution(
                spec, "open_decision", target=f"upgrade:{d['id']}",
                rationale=f"{d['verdict'].upper()} — {d['rationale']} "
                          f"(evidence sig {d['evidence_signature']})",
                by=by or "discover upgrade-decision")
    save_yaml_atomic(path, spec)
    return block
