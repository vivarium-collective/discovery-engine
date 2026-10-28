"""Reconcile a study's declared ``contract.face`` against its run's observables.

A study declares a typed ``contract.face`` (its intended observable interface).
This checks, best-effort, that each declared port is actually *emitted* by the
study's latest run — catching "you declared ``formate`` but the simulation never
produces it". Observables are untyped (a run emits values, not a schema), so the
check is **presence-only**: type/units are not reconciled here.

Abstains (returns ``[]``) when the study declares no ``contract.face`` or has no
readable run — never fabricates a mismatch.
"""
from __future__ import annotations

from typing import Iterable, List

from discovery_engine.run_observables import study_run_observables


def observable_gaps(study_face: dict, observables: Iterable[str]) -> List[str]:
    """A message per declared ``inputs``/``outputs`` port absent from
    ``observables``. Presence-only; the port name is all that is compared."""
    emitted = set(observables or [])
    gaps: List[str] = []
    study_face = study_face or {}
    for side in ("inputs", "outputs"):
        for port in (study_face.get(side) or {}):
            if port not in emitted:
                gaps.append(
                    f"{side} port '{port}' declared in contract.face is not "
                    f"emitted by the study's most recent run")
    return gaps


def reconcile_study_observables(study_spec: dict, ws_root) -> List[dict]:
    """Contract-observable findings for one study.

    ``[]`` when the study declares no ``contract.face`` or has no readable run;
    otherwise one ``{severity: "warning", check: "contract-observable",
    message}`` per declared port the latest run does not emit.
    """
    face = ((study_spec or {}).get("contract") or {}).get("face")
    if not isinstance(face, dict):
        return []
    observables = study_run_observables(study_spec, ws_root)
    if observables is None:
        return []
    return [
        {"severity": "warning", "check": "contract-observable", "message": message}
        for message in observable_gaps(face, observables)
    ]
