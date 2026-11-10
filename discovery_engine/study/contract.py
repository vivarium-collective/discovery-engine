"""Study-level ``contract.face`` auditing and reconciliation, built on the
shared bigraph-schema audit kernel.

This is the viva-superpowers half of unifying the two audits. The composite
contract audit (``bigraph_schema.contract_audit``) and the L0-L5 study audit
(``study_audit``) now share one typed kernel:

* :func:`study_face_findings` runs a study's declared ``contract.face`` through
  ``bigraph_schema.contract_audit.audit_face`` -- the SAME typed checks the
  composite contract audit uses (numeric ``_min``/``_max`` bounds, ``_units``
  parseability, predicate references to declared ports). So a study's face gets
  the composite audit's rigor, not just JSON-Schema shape validation.
* :func:`contract_face_diff` reconciles a declared study face against a resolved
  composite face (port presence + base-type compatibility) -- the reconciler
  the ``study.schema.json`` ``contract.face`` description promises. It is pure
  (it resolves nothing); a caller that already holds a resolved composite face
  (e.g. at composite-build time) passes it in.
"""
from __future__ import annotations

from bigraph_schema.contract_audit import audit_face


def _study_face(spec):
    contract = (spec or {}).get("contract")
    if not isinstance(contract, dict):
        return None, None
    face = contract.get("face")
    conditions = contract.get("conditions")
    return (face if isinstance(face, dict) else None,
            conditions if isinstance(conditions, list) else None)


def study_face_findings(spec, core=None):
    """Audit a study's declared ``contract.face`` with the shared kernel.

    Returns a list of ``bigraph_schema.contract_audit.Finding`` (empty when the
    study declares no ``contract.face``)."""
    face, conditions = _study_face(spec)
    if not face:
        return []
    return audit_face(core, face, conditions=conditions)


def _port_base_type(decl):
    """Base type of a port declaration, as a string, for compatibility
    comparison. Accepts a dict (``{'_type': ...}``) or a string
    (``'float[mM]'`` -> ``'float'``)."""
    if isinstance(decl, dict):
        return decl.get("_type")
    if isinstance(decl, str):
        return decl.split("[", 1)[0].split("{", 1)[0].strip() or decl
    return None


def contract_face_diff(declared_face, resolved_face):
    """Reconcile a declared study ``contract.face`` against a resolved composite
    face.

    Returns a list of discrepancy dicts ``{direction, port, kind, detail}``,
    where ``kind`` is ``"missing"`` (a declared port is absent from the resolved
    composite face) or ``"type_mismatch"`` (declared vs resolved base ``_type``
    differ). An empty list means the composite honours everything the study
    declared. Pure -- it never resolves or builds anything; the caller supplies
    the resolved face."""
    declared_face = declared_face or {}
    resolved_face = resolved_face or {}
    out = []
    for direction in ("inputs", "outputs"):
        decl_ports = declared_face.get(direction) or {}
        res_ports = resolved_face.get(direction) or {}
        for port, decl in decl_ports.items():
            if port not in res_ports:
                out.append({
                    "direction": direction, "port": port, "kind": "missing",
                    "detail": f"declared {direction} port {port!r} is not in the resolved composite face",
                })
                continue
            dt, rt = _port_base_type(decl), _port_base_type(res_ports[port])
            if dt is not None and rt is not None and dt != rt:
                out.append({
                    "direction": direction, "port": port, "kind": "type_mismatch",
                    "detail": f"{direction}.{port}: declared type {dt!r} vs resolved {rt!r}",
                })
    return out
