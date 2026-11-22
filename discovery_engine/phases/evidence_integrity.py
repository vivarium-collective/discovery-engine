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
- ``provisional``  a numeric result with no resolvable registered run. An
                   ``adopted-remote-run`` stamp (a committed remote measurement with
                   no local runs.db, issue #90) is PROVISIONAL *attributed* —
                   ``adopted: True`` + ``evidence_source`` — never promoted to
                   verified / run-of-record; the guard refuses a bare scalar literal.
- ``contradicted`` a numeric result that violates the behavior-test it claims to pass.
- ``unsupported``  a finding with no numeric observed value (prose only).

Evidence may declare an ``evidence.source_type`` (``simulation`` (default) / ``lab`` /
``external_dataset`` / ``literature``) and an ``evidence.uncertainty``; both are carried into
the classification record. The VERIFIED rule is simulation-specific, so non-simulation evidence
is never VERIFIED by it — it stays PROVISIONAL pending a source-specific validator (DE-11).
"""
from __future__ import annotations


from discovery_engine.phases.hypotheses import (
    _as_list, _evaluate, _expected_band, _finding_measure_observed, _findings,
    _SCALAR_REL_TOL, _to_float,
)

VERIFIED = "verified"
PROVISIONAL = "provisional"
CONTRADICTED = "contradicted"
UNSUPPORTED = "unsupported"
_STATUSES = (VERIFIED, PROVISIONAL, CONTRADICTED, UNSUPPORTED)

# Evidence source types (DE-11, plan §7 EvidenceRecord / §13 multi-source). The
# registered-run VERIFIED rule below is SIMULATION-specific: it matches `observed` against a
# simulation run's code-graded `computed_outcome`. Non-simulation evidence (a lab measurement,
# a curated external dataset, a literature value) cannot be verified by that rule and must not
# be — until a source-specific validator exists it stays PROVISIONAL. Absent/blank source_type
# defaults to `simulation`, so all existing evidence classifies exactly as before.
SIMULATION = "simulation"
LAB = "lab"
EXTERNAL_DATASET = "external_dataset"
LITERATURE = "literature"
SOURCE_TYPES = (SIMULATION, LAB, EXTERNAL_DATASET, LITERATURE)


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
        return _expected_band({k: pf[k] for k in ("op", "value", "low", "high", "threshold",
                                                   "tolerance", "epsilon")
                               if k in pf})
    return None


_DONE_STATUSES = {"complete", "done", "ran", "completed"}
_ABS_TOL = 1e-9


def _run_rows(study_spec: dict) -> dict[str, dict]:
    """The study's ``runs[]`` records keyed by run name (and run_id/id aliases)."""
    out: dict[str, dict] = {}
    for r in _as_list((study_spec or {}).get("runs")):
        if isinstance(r, dict):
            for k in ("name", "run_id", "id"):
                v = r.get(k)
                if isinstance(v, str) and v.strip():
                    out.setdefault(v.strip(), r)
    return out


def _is_int(x) -> bool:
    return isinstance(x, int) and not isinstance(x, bool)


def _value_matches(observed, measured, tol: float) -> bool:
    m = _to_float(measured)
    o = _to_float(observed)
    if m is None or o is None:
        return False
    if _is_int(observed) and _is_int(measured):
        return observed == measured
    if abs(m) < _ABS_TOL:
        return abs(o - m) <= _ABS_TOL
    return abs(o - m) <= tol * abs(m)


def _tolerance(test: dict | None) -> float:
    pf = (test or {}).get("pass_if")
    tol = _to_float(pf.get("tolerance")) if isinstance(pf, dict) else None
    return tol if tol is not None and tol >= 0 else _SCALAR_REL_TOL


# The 3-part "registered run" contract a numeric finding must satisfy to be run-backed evidence,
# documented in ONE place (trial finding G3: the checks existed but were scattered inside
# classify_finding_evidence with no single validator/doc). ALL THREE must hold — study.yaml
# ``runs[]`` rows and ``evidence.from_run`` do NOT by themselves make a finding run-backed:
REGISTERED_RUN_CONTRACT = (
    "evidence.from_test — the finding names the behavior test it is evidence for",
    "provenance.run_ids — present AND each resolves to a run registered via run_registry in runs.db",
    "a computed outcome for from_test on the cited run (study_evaluator.compute_outcomes)",
)


def registered_run_contract(finding: dict, registered_run_ids: set[str],
                            run_rows: dict[str, dict] | None = None) -> dict:
    """Check a finding against the 3-part registered-run contract (see
    :data:`REGISTERED_RUN_CONTRACT`) — the single source of truth for "what makes a finding
    run-backed", so callers / validators / docs need not re-derive it.

    Returns ``{satisfied: bool, missing: [str, ...], parts: {from_test, run_ids_resolve,
    computed_outcome}}``. ``run_rows`` maps run_id -> its row (with ``computed_outcomes``); omit it
    to check only the first two parts (presence + resolution), leaving ``computed_outcome`` unknown."""
    run_rows = run_rows or {}
    ev = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    prov = finding.get("provenance") if isinstance(finding.get("provenance"), dict) else {}
    from_test = ev.get("from_test")
    run_ids = [str(r) for r in _as_list(prov.get("run_ids")) if str(r).strip()]
    resolves = bool(run_ids) and all(r in registered_run_ids for r in run_ids)
    has_outcome = False
    if from_test and resolves:
        row = run_rows.get(run_ids[0]) if isinstance(run_rows.get(run_ids[0]), dict) else {}
        out = (row.get("computed_outcomes") or {}).get(from_test)
        has_outcome = isinstance(out, dict) and out.get("measured_value") is not None
    missing: list[str] = []
    if not from_test:
        missing.append("evidence.from_test")
    if not run_ids:
        missing.append("provenance.run_ids")
    elif not resolves:
        missing.append("provenance.run_ids resolve to a registered run")
    if from_test and resolves and run_rows and not has_outcome:
        missing.append(f"computed outcome for '{from_test}' on the cited run")
    return {"satisfied": not missing, "missing": missing,
            "parts": {"from_test": bool(from_test), "run_ids_resolve": resolves,
                      "computed_outcome": has_outcome}}


# ---------------------------------------------------------------------------
# Adopted remote-run recognition (issue #90).
#
# A remote measurement — produced on another host, committed as an artifact (e.g.
# results/*.json), with NO local runs.db — cannot resolve to a registered run, so
# it lands in the generic PROVISIONAL bucket indistinguishable from an unbacked
# number. An ``adopted-remote-run`` provenance stamp makes the attribution explicit
# WITHOUT promoting it to a run-of-record, and a guard refuses a bare scalar literal
# (adopt only a reconstructable series / committed raw inputs). The stamp lives on
# the cited run row's ``provenance`` (``method: adopted-remote-run``) or directly on
# the finding's ``provenance.adopted_run``.
# ---------------------------------------------------------------------------

ADOPTED_RUN_METHOD = "adopted-remote-run"

#: The honesty contract an adopted-remote-run stamp must satisfy to be credited.
ADOPTED_RUN_CONTRACT = (
    f"method == '{ADOPTED_RUN_METHOD}'",
    "origin_host — the host that produced the measurement",
    "source_artifact — the committed artifact the values were read from",
    "source_sha256 — content hash of that artifact (so the adoption is checkable)",
    "measured_at — when the remote host measured (distinct from adopted_at)",
    "adopted_at — when the measurement entered this record",
    "series_reconstructable: true — refuses a bare scalar literal; adopt only a "
    "reconstructable series (or committed raw inputs a re-derivation can read)",
)

_ADOPTED_STR_FIELDS = ("origin_host", "source_artifact", "source_sha256",
                       "measured_at", "adopted_at")


def validate_adopted_run_stamp(stamp: dict) -> dict:
    """Validate an ``adopted-remote-run`` provenance stamp (issue #90).

    Honored only when it carries the full honesty contract (see
    :data:`ADOPTED_RUN_CONTRACT`) AND refuses a bare scalar literal —
    ``series_reconstructable`` must be ``True``. Returns ``{valid, missing}``;
    ``missing`` names each unmet part so an invalid stamp's overclaim is visible
    rather than silently granting adoption credit."""
    if not isinstance(stamp, dict):
        return {"valid": False, "missing": ["stamp is not a mapping"]}
    missing: list[str] = []
    if str(stamp.get("method") or "").strip() != ADOPTED_RUN_METHOD:
        missing.append(f"method == '{ADOPTED_RUN_METHOD}'")
    for field in _ADOPTED_STR_FIELDS:
        v = stamp.get(field)
        if not (isinstance(v, str) and v.strip()):
            missing.append(field)
    if stamp.get("series_reconstructable") is not True:
        missing.append("series_reconstructable: true (refuses a bare scalar literal)")
    return {"valid": not missing, "missing": missing}


def _adopted_run_stamp(finding: dict, run_rows: dict[str, dict],
                       run_ids: list[str]) -> dict | None:
    """The adopted-remote-run stamp for a finding, or ``None``.

    Checks the finding's own ``provenance.adopted_run`` first, then the cited run
    row(s)' ``provenance`` for ``method: adopted-remote-run`` (the stamp lives on
    ``runs[].provenance`` by convention). Returns the raw stamp (unvalidated)."""
    prov = finding.get("provenance") if isinstance(finding.get("provenance"), dict) else {}
    direct = prov.get("adopted_run")
    if isinstance(direct, dict):
        return direct
    for rid in run_ids:
        row = run_rows.get(rid)
        rp = row.get("provenance") if isinstance(row, dict) else None
        if isinstance(rp, dict) and str(rp.get("method") or "").strip() == ADOPTED_RUN_METHOD:
            return rp
    return None


def classify_finding_evidence(finding: dict, study_spec: dict,
                              registered_run_ids: set[str],
                              run_rows: dict[str, dict] | None = None) -> dict:
    """Classify one finding's evidence. Returns ``{finding, measure, observed,
    status, reason, run_ids, from_test, measured_value, evaluated_by}``.

    VERIFIED needs more than a registered run id: the canonical (first) cited run
    must be complete, the cited test must be graded by code, and ``observed`` must
    match the run's computed ``measured_value`` within tolerance. Pure."""
    run_rows = run_rows or {}
    ev = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    prov = finding.get("provenance") if isinstance(finding.get("provenance"), dict) else {}
    measure, observed = _finding_measure_observed(finding)
    val = _to_float(observed)
    fid = finding.get("id") or finding.get("name")
    from_test = ev.get("from_test")
    run_ids = [str(r) for r in _as_list(prov.get("run_ids")) if str(r).strip()]
    # Absent (None) or blank -> simulation (the backward-compatible default). Any other value,
    # including a falsy non-string like `False`/`0` from a YAML typo, is normalized and routed to
    # the non-simulation branch — malformed source_type fails CLOSED (never silently verified).
    _raw_src = ev.get("source_type")
    source_type = SIMULATION if _raw_src is None else (str(_raw_src).strip().lower() or SIMULATION)
    base = {"finding": fid, "measure": measure, "observed": observed,
            "run_ids": run_ids, "from_test": from_test, "source_type": source_type,
            "uncertainty": ev.get("uncertainty"),
            "measured_value": None, "evaluated_by": None}

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

    # The registered-run VERIFIED rule below matches `observed` against a simulation run's
    # code-graded outcome, so it applies ONLY to simulation evidence. Non-simulation evidence
    # (lab / external_dataset / literature, or any unrecognized source) is never VERIFIED by it —
    # it stays PROVISIONAL until a source-specific validator exists (DE-11 follow-up).
    if source_type != SIMULATION:
        kind = source_type if source_type in SOURCE_TYPES else f"unknown source_type '{source_type}'"
        return {**base, "status": PROVISIONAL,
                "reason": f"non-simulation evidence ({kind}) — the simulation run rule does not "
                          f"apply; a source-specific validator is required"}

    resolvable = bool(from_test) and bool(run_ids) and all(r in registered_run_ids for r in run_ids)
    if resolvable:
        run = run_ids[0]
        row = run_rows.get(run) if isinstance(run_rows.get(run), dict) else {}
        status = str(row.get("status") or "").strip().lower()
        out = (row.get("computed_outcomes") or {}).get(from_test)
        out = out if isinstance(out, dict) else {}
        measured = out.get("measured_value")
        graded = out.get("evaluated_by")
        base = {**base, "measured_value": measured, "evaluated_by": graded}

        def prov(why: str) -> dict:
            return {**base, "status": PROVISIONAL, "reason": why}

        if status not in _DONE_STATUSES:
            return prov(f"run {run} status={status or 'unknown'}")
        if measured is None:
            return prov(f"run {run} has no computed outcome for '{from_test}'")
        if graded != "code":
            return prov(f"test '{from_test}' graded by {graded or 'unknown'}, not code (run {run})")
        if not _value_matches(observed, measured, _tolerance(_behavior_test(study_spec, from_test))):
            return prov(f"observed {observed} \u2260 computed {measured} for '{from_test}' in run {run}")
        return {**base, "status": VERIFIED,
                "reason": f"observed {observed} matches computed {measured} for "
                          f"'{from_test}' in run {run} (graded by code)"}
    # An adopted remote-run measurement (committed artifact, no local runs.db) is
    # PROVISIONAL *attributed* — explicitly NOT promoted to a run-of-record — when its
    # stamp is valid; an invalid / bare-literal stamp is flagged, not credited (#90).
    stamp = _adopted_run_stamp(finding, run_rows, run_ids)
    if stamp is not None:
        check = validate_adopted_run_stamp(stamp)
        if check["valid"]:
            sha = str(stamp.get("source_sha256") or "")
            return {**base, "status": PROVISIONAL, "adopted": True,
                    "evidence_source": ADOPTED_RUN_METHOD,
                    "reason": f"adopted remote-run measurement from {stamp.get('origin_host')} "
                              f"(artifact {stamp.get('source_artifact')} sha256={sha[:12]}…, "
                              f"measured_at={stamp.get('measured_at')}) — provisional, not a "
                              f"local run-of-record"}
        return {**base, "status": PROVISIONAL, "adopted": False,
                "adopted_invalid": check["missing"],
                "reason": "adopted-remote-run stamp present but INVALID, not credited "
                          "(missing: " + ", ".join(check["missing"]) + ")"}

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
    repro_gaps: list[dict] = []
    summary = {s: 0 for s in _STATUSES}
    member_slugs = [s for s in (_as_list(inv_spec.get("studies")) if inv_spec else []) if isinstance(s, str)]
    for slug in (member_slugs or list(studies)):
        spec = studies.get(slug)
        if not isinstance(spec, dict):
            continue
        reg = _registered_run_ids(runs_by_study.get(slug))
        # Reproducibility gap: the study records a run in its YAML but NO run of this study is
        # registered on disk (runs.db). advance keys off the YAML run record, so such a study is
        # treated as "ran" and skipped — an unbacked `ran` claim indistinguishable from a real run,
        # the run-side twin of an authored verdict with no evidence. Surface it, don't trust it.
        recorded_runs = [r for r in (spec.get("runs") or []) if isinstance(r, dict)]
        if recorded_runs and not reg:
            repro_gaps.append({
                "study": slug,
                "recorded_runs": [r.get("name") or r.get("run_id") or r.get("id")
                                  for r in recorded_runs],
                "note": "study records a run with no registered runs.db artifact — the run is "
                        "unreproducible and its evidence can never be verified"})
        rows = []
        for f in _findings(spec):
            row = classify_finding_evidence(f, spec, reg, _run_rows(spec))
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
    return {"ledger": ledger, "summary": summary, "by_study": by_study,
            "reproducibility_gaps": repro_gaps}


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
            res = classify_finding_evidence(f, spec, reg, _run_rows(spec))
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
