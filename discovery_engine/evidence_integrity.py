"""Evidence/run reconciliation — Discovery Engine 2.0, Milestone 1 (Cycle N+1 Phase A).

The first move of a discovery cycle is to decide *which preliminary results can be
trusted*. A finding can carry a real number and still have no resolvable run behind
it — a result from an unregistered in-process run, or prose copied into the report.
This module classifies each finding's evidence so a numeric result without a
registered run is marked **PROVISIONAL** rather than silently accepted as biological
support ("a passing software test is not biological support").

Pure / derive-first: ``reconcile_evidence_state`` takes the investigation spec, the
study specs, and each study's registered runs, and returns a classification ledger.
It performs no I/O; the CLI loads runs (``run_registry.list_runs``) and passes them in.

Classes (the Cycle N+1 Phase A vocabulary):

- ``verified``     numeric evidence with a resolvable run/provenance chain
                   (``evidence.from_test`` + ``provenance.run_ids`` that resolve to a
                   registered run) — the engine's existing L2 supporting-provenance rule.
- ``provisional``  a numeric result with no resolvable registered run.
- ``contradicted`` a numeric result that violates the behavior-test it claims to pass.
- ``unsupported``  a finding with no numeric observed value (prose only).
"""
from __future__ import annotations

from typing import Any

from .hypotheses import (
    _as_list, _evaluate, _expected_band, _finding_measure_observed, _findings,
    _study_slug, _to_float,
)

VERIFIED = "verified"
PROVISIONAL = "provisional"
CONTRADICTED = "contradicted"
UNSUPPORTED = "unsupported"
_STATUSES = (VERIFIED, PROVISIONAL, CONTRADICTED, UNSUPPORTED)


def _run_ids(run: dict) -> set[str]:
    out = set()
    for k in ("run_id", "id", "runId"):
        v = run.get(k) if isinstance(run, dict) else None
        if isinstance(v, str) and v.strip():
            out.add(v.strip())
    return out


def _registered_run_ids(runs: list[dict] | None) -> set[str]:
    """Every id by which a registered run of this study can be referenced."""
    out: set[str] = set()
    for r in runs or []:
        if isinstance(r, dict):
            out |= _run_ids(r)
    return out


def _behavior_test(study_spec: dict, name: str) -> dict | None:
    for t in _as_list((study_spec or {}).get("behavior_tests")):
        if isinstance(t, dict) and t.get("name") == name:
            return t
    return None


def _pass_if_band(test: dict) -> dict | None:
    """The ``pass_if`` of a behavior test as an expected band (op/value or low/high)."""
    pf = (test or {}).get("pass_if")
    if isinstance(pf, dict):
        return _expected_band({k: pf[k] for k in ("op", "value", "low", "high", "threshold")
                               if k in pf})
    return None


def classify_finding_evidence(finding: dict, study_spec: dict,
                              registered_run_ids: set[str]) -> dict:
    """Classify one finding's evidence. Returns ``{finding, measure, observed,
    status, reason, run_ids, from_test}``."""
    ev = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    prov = finding.get("provenance") if isinstance(finding.get("provenance"), dict) else {}
    measure, observed = _finding_measure_observed(finding)
    val = _to_float(observed)
    fid = finding.get("id") or finding.get("name")
    from_test = ev.get("from_test")
    run_ids = [str(r) for r in _as_list(prov.get("run_ids")) if str(r).strip()]
    base = {"finding": fid, "measure": measure, "observed": observed,
            "run_ids": run_ids, "from_test": from_test}

    if val is None:
        return {**base, "status": UNSUPPORTED,
                "reason": "no numeric observed value — a prose-only finding is not evidence"}

    # Contradiction applies only to an UNCONDITIONED positive claim: a finding
    # scoped to a regime (``evidence.conditions``) — a control or alternate-regime
    # result — is judged by its hypothesis's per-regime prediction, not by the
    # positive gate it happens to cite, so it is not auto-contradicted here. An
    # unconditioned finding that names a gate and violates its pass_if argues
    # against the gate it cites.
    conditioned = isinstance(ev.get("conditions"), dict) and bool(ev.get("conditions"))
    test = _behavior_test(study_spec, from_test) if (from_test and not conditioned) else None
    band = _pass_if_band(test) if test else None
    if band is not None and not _evaluate(val, band):
        return {**base, "status": CONTRADICTED,
                "reason": f"observed {observed} violates the pass_if of '{from_test}'"}

    resolvable = bool(from_test) and bool(run_ids) and all(r in registered_run_ids for r in run_ids)
    if resolvable:
        return {**base, "status": VERIFIED,
                "reason": f"resolvable run chain: from_test '{from_test}', run_ids {run_ids}"}
    missing = []
    if not from_test:
        missing.append("evidence.from_test")
    if not run_ids:
        missing.append("provenance.run_ids")
    elif not all(r in registered_run_ids for r in run_ids):
        missing.append("run_ids do not resolve to a registered run")
    return {**base, "status": PROVISIONAL,
            "reason": "numeric result with no resolvable registered run (missing: "
                      + ", ".join(missing) + ")"}


def reconcile_evidence_state(inv_spec: dict, studies: dict,
                             runs_by_study: dict | None = None) -> dict:
    """Classify every finding of every member study (Cycle N+1 Phase A).

    ``studies``: ``{slug: study_spec}``. ``runs_by_study``: ``{slug: [run dicts]}``
    (a study's registered runs; absent/empty means no registered runs). Returns
    ``{ledger, summary, by_study}`` — ``ledger`` is the per-finding classification,
    ``summary`` the status tally, ``by_study`` the per-study rollup with the
    study's worst (most-cautious) evidence status."""
    runs_by_study = runs_by_study or {}
    order = {s: i for i, s in enumerate((VERIFIED, PROVISIONAL, CONTRADICTED, UNSUPPORTED))}
    ledger: list[dict] = []
    by_study: dict[str, dict] = {}
    summary = {s: 0 for s in _STATUSES}
    member_slugs = [s for s in (_as_list(inv_spec.get("studies")) if inv_spec else []) if isinstance(s, str)]
    for slug in (member_slugs or list(studies)):
        spec = studies.get(slug)
        if not isinstance(spec, dict):
            continue
        reg = _registered_run_ids(runs_by_study.get(slug))
        rows = []
        for f in _findings(spec):
            row = classify_finding_evidence(f, spec, reg)
            row["study"] = slug
            ledger.append(row)
            rows.append(row)
            summary[row["status"]] += 1
        numeric = [r for r in rows if r["observed"] is not None]
        worst = max((r["status"] for r in numeric), key=lambda s: order[s], default=None)
        by_study[slug] = {
            "n_findings": len(rows),
            "n_numeric": len(numeric),
            "registered_runs": len(reg),
            "status": worst,  # the study's evidence is only as strong as its weakest numeric finding
            "statuses": {s: sum(1 for r in rows if r["status"] == s) for s in _STATUSES},
        }
    return {"ledger": ledger, "summary": summary, "by_study": by_study}


def trace_claim_to_runs(finding_id: str, studies: dict,
                        runs_by_study: dict | None = None) -> dict:
    """Expose the run/provenance chain (or the missing links) for one finding.
    Returns ``{found, study, from_test, run_ids, registered_runs, resolvable,
    missing}`` — ``missing`` names the broken links."""
    runs_by_study = runs_by_study or {}
    for slug, spec in (studies or {}).items():
        if not isinstance(spec, dict):
            continue
        for f in _findings(spec):
            if (f.get("id") or f.get("name")) != finding_id:
                continue
            reg = _registered_run_ids(runs_by_study.get(slug))
            res = classify_finding_evidence(f, spec, reg)
            missing = []
            if not res["from_test"]:
                missing.append("evidence.from_test")
            unresolved = [r for r in res["run_ids"] if r not in reg]
            if not res["run_ids"]:
                missing.append("provenance.run_ids")
            elif unresolved:
                missing.append(f"unregistered run_ids: {unresolved}")
            return {"found": True, "study": slug, "from_test": res["from_test"],
                    "run_ids": res["run_ids"], "registered_runs": sorted(reg),
                    "resolvable": res["status"] == VERIFIED, "status": res["status"],
                    "missing": missing}
    return {"found": False, "finding": finding_id}
