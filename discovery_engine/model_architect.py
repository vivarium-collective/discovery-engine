"""Model architect — Discovery Engine 2.0, Wave 2 (Cycle N+1 Phase D).

Capability-driven model acquisition: given a ``ModelBuildRequest`` (required
mechanisms + observables), query a catalog built from the campaign's own study
composites by *capability signature* (not keyword matching alone), and choose a
route per mechanism — **reuse** a catalog component, **wrap** a partial match, or
**build** a missing one. Records an immutable, content-addressed ``ModelVersion``.

Derive-first: catalog + search + routing are pure projections over the loaded study
specs; nothing is executed and no component is written. "Coupling quality is a
scientific question: an individually validated module can still form an invalid
composite" — ``validate_coupling`` flags mixed formalisms with no coupling contract.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
from typing import Any

from .hypotheses import _as_list

REUSE = "reuse"
WRAP = "wrap"
BUILD = "build"

# Mechanism → capability synonyms. A component *provides* a mechanism if any synonym
# appears in its capability signature (composite name tokens + topic/concept + observables).
_SYNONYMS = {
    "exchange": ("exchange", "metabolite", "metabolic", "cross_lineage", "flux", "syntroph"),
    "metabolic_exchange": ("exchange", "metabolite", "metabolic", "cross_lineage", "flux"),
    "diffusion": ("diffusion", "spatial", "reaction_diffusion", "pore", "placement"),
    "growth": ("growth", "population", "reproduction", "division", "propensity"),
    "adhesion": ("adhesion", "cpm", "potts", "wrapping", "contact", "differential"),
    "membrane_remodeling": ("membrane", "enclosure", "wrapping", "bending", "protrusion"),
    "membrane": ("membrane", "enclosure", "wrapping", "bending"),
    "spatial_contact": ("spatial", "contact", "placement", "proximity", "cpm"),
    "transport": ("transport", "permeability", "pump", "internalization"),
    "energy": ("energy", "energetic", "atp", "bioenergetic", "internalization", "electrochemical"),
    "heredity": ("heredity", "heritab", "inheritance", "lineage", "transmission"),
}


def ModelBuildRequest(*, question_id: str, required_mechanisms: list[str],
                      required_observables: list[str] | None = None,
                      candidate_formalisms: list[str] | None = None,
                      acquisition_policy: list[str] | None = None,
                      validation: list[str] | None = None,
                      alternatives_required: int = 2) -> dict:
    """Build a ModelBuildRequest record (the Phase-D capability specification)."""
    return {
        "question_id": question_id,
        "required_mechanisms": list(required_mechanisms),
        "required_observables": list(required_observables or []),
        "candidate_formalisms": list(candidate_formalisms or ["ODE", "FBA", "PDE", "ABM", "CPM"]),
        "acquisition_policy": list(acquisition_policy or [REUSE, WRAP, BUILD]),
        "validation": list(validation or ["units", "conservation", "convergence", "baseline", "controls"]),
        "alternatives_required": alternatives_required,
    }


def _tokens(s: Any) -> set[str]:
    return set(t for t in re.split(r"[^a-z0-9]+", str(s or "").lower()) if len(t) > 2)


def _top_importable(composite: str) -> bool:
    """True when the composite's top-level package can be located (find_spec only)."""
    try:
        return importlib.util.find_spec(composite.split(".")[0]) is not None
    except Exception:  # noqa: BLE001 — a broken intermediate package isn't resolvable
        return False


def resolve_composite(composite: str, ws_root) -> dict:
    """Does this composite's module exist? Pure: ``importlib.util.find_spec`` only, never
    imports composite code. ``composite`` is a dotted ``pkg.module.name`` path; the module is
    everything before the last dot. ``ws_root`` is accepted for API symmetry (resolution uses
    the current environment's import path)."""
    out = {"resolves": False, "module": None, "spec_path": None}
    if not composite or not isinstance(composite, str) or "." not in composite:
        return out
    module = composite.rsplit(".", 1)[0]
    if not _top_importable(composite):
        return out
    try:
        spec = importlib.util.find_spec(module)
    except Exception:  # noqa: BLE001 — module (or intermediate) not importable
        return out
    if spec is None:
        return out
    return {"resolves": True, "module": module, "spec_path": getattr(spec, "origin", None)}


def build_catalog(studies: dict, ws_root=None) -> list[dict]:
    """Derive a capability catalog from the campaign's study arms. One component per
    (study, composite): its capability signature is the composite-name tokens plus
    the study's topic/concept/title tokens plus its declared observables."""
    by_comp: dict[str, dict] = {}
    for slug, spec in (studies or {}).items():
        if not isinstance(spec, dict):
            continue
        ctx = _tokens(spec.get("topic")) | _tokens(spec.get("concept")) | _tokens(spec.get("title"))
        obs = [o for o in _as_list(spec.get("observables")) if isinstance(o, str)]
        obs += [str(spec.get("model_observable"))] if spec.get("model_observable") else []
        for arm in _as_list(spec.get("baseline")) + _as_list(spec.get("variants")):
            if not isinstance(arm, dict):
                continue
            comp = arm.get("composite")  # a real composite path, not a `base:` reference to another arm
            if not comp or "." not in str(comp):
                continue
            # The same composite serves several studies (e.g. metabolite exchange backs both the
            # metabolic and the reaction-diffusion study) — UNION their capability contexts.
            entry = by_comp.setdefault(comp, {
                "id": comp.split(".")[-1], "composite": comp, "study": slug,
                "studies": [], "fidelities": set(), "_cap": set(_tokens(comp)), "_obs": set()})
            entry["studies"].append(slug)
            if spec.get("fidelity") or arm.get("fidelity"):
                entry["fidelities"].add(spec.get("fidelity") or arm.get("fidelity"))
            entry["_cap"] |= ctx | set(t for o in obs for t in _tokens(o))
            entry["_obs"] |= set(obs)
    catalog: list[dict] = []
    for comp, e in by_comp.items():
        fids = sorted(f for f in e["fidelities"] if f)
        catalog.append({
            "id": e["id"], "composite": comp, "study": e["study"], "studies": e["studies"],
            "fidelity": fids[0] if fids else None, "fidelities": fids,
            "capability": sorted(e["_cap"]), "observables": sorted(e["_obs"]),
        })
        if ws_root is not None:
            catalog[-1].update(resolve_composite(comp, ws_root))
    return catalog


def _provides(component: dict, mechanism: str) -> int:
    """How strongly a component provides a mechanism (0 = not, higher = stronger)."""
    cap = set(component.get("capability", []))
    syns = set(_SYNONYMS.get(mechanism, (mechanism,)))
    return sum(1 for s in syns if any(s in c or c in s for c in cap))


def search_components(request: dict, catalog: list[dict]) -> dict:
    """For each required mechanism, the catalog components that provide it, ranked
    by capability match then fidelity presence. Returns ``{mechanism: [components]}``."""
    out: dict[str, list[dict]] = {}
    for mech in request.get("required_mechanisms", []):
        ranked = sorted(
            ({**c, "_match": _provides(c, mech)} for c in catalog if _provides(c, mech) > 0),
            key=lambda c: (-c["_match"], 0 if c.get("fidelity") else 1, c["id"]))
        out[mech] = ranked
    return out


def choose_acquisition_route(request: dict, catalog: list[dict]) -> dict:
    """Per mechanism, choose reuse / wrap / build.

    - **reuse**: a strong catalog match (match >= 2) — pin and reuse it.
    - **wrap**: a weak/partial match (match == 1) — an adapter around an existing
      component is needed (ports, units, step semantics).
    - **build**: no catalog component provides it — draft + implement a new module.
    """
    found = search_components(request, catalog)
    routes: dict[str, dict] = {}
    for mech in request.get("required_mechanisms", []):
        hits = found.get(mech, [])
        if hits and hits[0]["_match"] >= 2:
            c = hits[0]
            routes[mech] = {"route": REUSE, "component": c["composite"],
                            "rationale": f"strong catalog match ({c['id']}); pin and reuse"}
        elif hits:
            c = hits[0]
            routes[mech] = {"route": WRAP, "component": c["composite"],
                            "rationale": f"partial match ({c['id']}); wrap behind a shared "
                                         "observables/ports contract"}
        else:
            routes[mech] = {"route": BUILD, "component": None,
                            "rationale": "no catalog component provides this mechanism; "
                                         "draft a mathematical spec + implement + validate"}
    return routes


def validate_coupling(routes: dict, catalog: list[dict]) -> list[str]:
    """Hybrid-coupling contract checks over the chosen components. Flags issues when
    reused/wrapped components span different fidelities/formalisms with no declared
    coupling contract — an individually valid module can still form an invalid composite."""
    issues: list[str] = []
    by_comp = {c["composite"]: c for c in catalog}
    chosen = [by_comp[r["component"]] for r in routes.values()
              if r.get("component") and r["component"] in by_comp]
    fidelities = {c.get("fidelity") for c in chosen if c.get("fidelity")}
    if len(fidelities) > 1:
        issues.append(f"coupling spans mixed fidelities {sorted(fidelities)} — declare state-port "
                      "map, units and synchronization (validate_coupling: units/conservation/sync)")
    if any(r["route"] == BUILD for r in routes.values()):
        built = [m for m, r in routes.items() if r["route"] == BUILD]
        issues.append(f"mechanisms to build ({', '.join(built)}) have no validated component yet — "
                      "run unit/dimensional/conservation/numerical tests before coupling")
    return issues


_MV_DENYLIST = {"seed", "seeds", "emitter", "out_dir", "n_steps"}


def _canonical_params(params: dict | None) -> dict:
    """Drop non-semantic run knobs (a seed is a replicate of the SAME model) and sort."""
    return {k: params[k] for k in sorted(params or {}) if k not in _MV_DENYLIST}


def routes_composite(routes: dict) -> str:
    """A stable composite identifier for a routed set of components."""
    return "+".join(sorted(str(r.get("component") or f"build:{m}") for m, r in routes.items()))


def model_version(composite: str, params: dict, *, parent_ids=(), origin=None) -> dict:
    """Content-addressed model identity: composite + canonical params. Pure.
    Validation is a property of evidence under a model (recorded on findings), not of the mv."""
    canon = _canonical_params(params)
    digest = hashlib.sha256(json.dumps({"composite": composite, "params": canon},
                                       sort_keys=True).encode()).hexdigest()[:16]
    return {"id": f"mv:{digest}", "composite": composite, "params": dict(params or {}),
            "parent_ids": list(parent_ids), "origin": origin}
