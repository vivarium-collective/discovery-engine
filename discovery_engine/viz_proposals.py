"""Visualization proposals (viz-loop decision §2F, §3.4): an uncovered gate / discriminating
observable / model disagreement becomes a typed, content-addressed ``VizProposal``.

Pure: proposes, never acts, never renders, no I/O, no ``scientific_judgment``. The engine
fills ``address`` only from the viz catalog (data); with no matching catalog class the
proposal is ``needs_author``. Non-blocking (never in ``_CLOSURE_BLOCKING``).

Triggers (kind-ordered, then by id; cap ``CAP`` per study per cycle):
  * ``gate_uncovered``           - a behavior-test gate whose source observables no declared
                                   viz covers          -> ``band_overlay``
  * ``discriminating_uncovered`` - ``discriminating_hypotheses(inv)[study].discriminable`` on
                                   an uncovered observable -> ``prediction_bands``
  * ``disagreement_uncovered``   - a ``model_disagreement_map`` row (``disagreement: true``)
                                   on an uncovered observable -> ``arm_comparison``
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any

CAP = 2
TERMINAL = ("rejected", "built")
_KIND_ORDER = {"gate_uncovered": 0, "discriminating_uncovered": 1, "disagreement_uncovered": 2}
_FORM = {"gate_uncovered": "band_overlay", "discriminating_uncovered": "prediction_bands",
         "disagreement_uncovered": "arm_comparison"}


def _as_list(v: Any) -> list:
    return v if isinstance(v, list) else ([] if v is None else [v])


def _hash8(payload: dict) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:8]


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "viz"


def _class_ref(address) -> str | None:
    if isinstance(address, str) and address.startswith(("local:", "dashboard:")):
        return address.split(":", 1)[1]
    return None


def _catalog_by_address(viz_catalog) -> dict:
    out: dict = {}
    for c in viz_catalog or []:
        if isinstance(c, dict) and c.get("address"):
            out[str(c["address"])] = c
            ref = _class_ref(c["address"])
            if ref:
                out.setdefault(ref, c)
    return out


def covered_observables(spec: dict, viz_catalog) -> set:
    """Observables a study's declared visualizations cover: ``covers`` U ``requires`` U
    ``config.inputs_map`` values U the catalog input ports of a ``local:`` class entry."""
    from .viz_freshness import normalize_entry
    cat = _catalog_by_address(viz_catalog)
    cov: set = set()
    for e in (spec or {}).get("visualizations") or []:
        if not isinstance(e, dict):
            continue
        ns = normalize_entry(e)
        cov |= set(ns["covers"]) | set(ns["requires"])
        cfg = e.get("config") if isinstance(e.get("config"), dict) else {}
        im = cfg.get("inputs_map") or e.get("inputs_map")
        if isinstance(im, dict):
            cov |= {str(v) for v in im.values() if isinstance(v, str)}
        ent = cat.get(ns["address"] or "") or cat.get(_class_ref(ns["address"]) or "")
        if ent:
            cov |= {str(k) for k in (ent.get("inputs") or {})}
    return cov


def _catalog_match(form: str, viz_catalog) -> dict | None:
    """A catalog class that can take an observable series: prefer one declaring ``form``,
    else a form-less one with declared inputs. Sorted by address (deterministic)."""
    ents = sorted((c for c in viz_catalog or [] if isinstance(c, dict) and c.get("address")
                   and c.get("inputs")), key=lambda c: str(c["address"]))
    return (next((c for c in ents if c.get("form") == form), None)
            or next((c for c in ents if not c.get("form")), None))


def _tests(spec: dict) -> list[dict]:
    t = spec.get("tests") or spec.get("behavior_tests") or []
    return [x for x in _as_list(t) if isinstance(x, dict)]


def _build(kind, study, spec, observable, source, source_ref, viz_catalog, now) -> dict:
    form = _FORM[kind]
    ent = _catalog_match(form, viz_catalog)
    derivable = ent is not None
    runs = [r.get("run_id") or r.get("name") for r in _as_list(spec.get("runs")) if isinstance(r, dict)]
    runs = [r for r in runs if r]
    port = next(iter(ent["inputs"]), None) if ent else None
    finding = next((f.get("id") or f.get("name") for f in _as_list(spec.get("findings"))
                    if isinstance(f, dict) and source.get("behavior_test")
                    and (f.get("evidence") or {}).get("from_test") == source["behavior_test"]), None)
    return {
        "id": f"vp:{study}:{_hash8({'kind': kind, 'study': study, 'observable': observable, 'ref': source_ref})}",
        "status": "proposed",
        "study": study,
        "source": {"kind": kind, **source, "observable": observable},
        "suggested": {
            "form": form,
            "title": f"{observable} ({form.replace('_', ' ')})",
            "data_source": {"observable": observable, "run_ids": runs},
            "address": ent["address"] if derivable else None,
            "inputs_map": {port: observable} if port else {},
            "needs_author": not derivable,
            "effort": ("render-only" if runs else "needs-run") if derivable else "needs-author",
        },
        "sharpens": {"finding": finding, "claim": f"claim:{study}"},
        "approval": "review",
        "history": [{"at": now, "event": "proposed", "by": "engine"}],
        "built": None,
        "derivable": derivable,
    }


def propose_visualizations(inv_spec: dict, studies: dict, cycle_result: dict | None,
                           viz_state: dict | None, viz_catalog: list | None, *,
                           now: str | None = None, ws_root: Any = None) -> list[dict]:
    """Derive ``VizProposal`` records (see module docstring). Content-addressed ids: the same
    inputs yield the same proposals, and any id already on ``inv_spec['viz_proposals']`` with
    a non-``proposed`` status is not re-emitted (rejected/built terminal; accepted persisted)."""
    from .evaluation import model_disagreement_map
    from .hypotheses import discriminating_hypotheses
    from .study_evaluator import required_observables_for_measure
    inv_spec = inv_spec or {}
    studies = studies or {}
    now = now or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    done = {p.get("id") for p in inv_spec.get("viz_proposals") or []
            if isinstance(p, dict) and p.get("status", "proposed") != "proposed"}
    cands: dict[str, dict] = {}

    def add(p):
        cands.setdefault(p["id"], p)

    for study in sorted(studies):
        spec = studies[study]
        if not isinstance(spec, dict):
            continue
        cov = covered_observables(spec, viz_catalog)
        for t in _tests(spec):
            obs_set = required_observables_for_measure(t.get("measure"), ws_root)
            for obs in sorted(o for o in obs_set if o not in cov):
                add(_build("gate_uncovered", study, spec, obs, {"behavior_test": t.get("name")},
                           t.get("name"), viz_catalog, now))
    for study, d in sorted(discriminating_hypotheses(inv_spec).items()):
        spec = studies.get(study)
        if not isinstance(spec, dict) or not d.get("discriminable"):
            continue
        obs = d.get("observable")
        if obs and obs not in covered_observables(spec, viz_catalog):
            hyps = sorted(str(c.get("hypothesis")) for c in d.get("competing") or [])
            add(_build("discriminating_uncovered", study, spec, obs, {"hypotheses": hyps},
                       hyps, viz_catalog, now))
    for r in model_disagreement_map(inv_spec, studies):
        study, obs = r.get("study"), r.get("observable")
        spec = studies.get(study)
        if not r.get("disagreement") or not obs or not isinstance(spec, dict):
            continue
        if obs not in covered_observables(spec, viz_catalog):
            add(_build("disagreement_uncovered", study, spec, obs, {}, None, viz_catalog, now))

    ordered = sorted((p for p in cands.values() if p["id"] not in done),
                     key=lambda p: (_KIND_ORDER[p["source"]["kind"]], p["id"]))
    out: list[dict] = []
    per: dict = {}
    for p in ordered:
        if per.get(p["study"], 0) >= CAP:
            continue
        per[p["study"]] = per.get(p["study"], 0) + 1
        out.append(p)
    return out
