"""R2 — the Executable Model Architect: inspect + validate a model component (§2, P0).

The current model_architect routes acquisition (reuse / wrap / build) and versions models, but
``resolve_composite`` only checks importability — it never reads what a component actually *is*.
This adds the first real introspection:

- :func:`inspect_component` reads a component's interface into a :class:`ModelContract` —
  importability, whether it is a process-bigraph ``Process``, its declared parameters (with
  units), and its input/output ports.
- :func:`validate_composite` checks a component against an expected :class:`ModelContract` and
  reports the issues the engine must not ignore before composing: not importable, a **missing
  exchange port**, or a **unit mismatch** on a shared port/parameter.

These are the hooks §2's acceptance needs ("produce a runnable coupled model and detect an
intentionally incompatible unit or missing exchange port"). Pure: importing a component to read
its class interface is read-only; no run is triggered. [0.7.2 plan §2 / §10]
"""
from __future__ import annotations

import dataclasses
import importlib


@dataclasses.dataclass
class ModelContract:
    """What inspect_component reads off a component. Fields it cannot determine stay None."""
    component: str
    importable: bool
    is_process: bool
    module: str | None = None
    parameters: dict | None = None        # name -> {type, default, units}
    input_ports: dict | None = None       # name -> port type (str or schema dict)
    output_ports: dict | None = None
    notes: list = dataclasses.field(default_factory=list)

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


def _units_of(port_type) -> str | None:
    if isinstance(port_type, dict):
        return port_type.get("units") or port_type.get("_units")
    return None


def _load(component):
    """Resolve a component to its class/factory. Accepts a class/callable directly, or a dotted
    ``pkg.module.Name`` path (imported read-only)."""
    if not isinstance(component, str):
        return component, getattr(component, "__module__", None)
    if "." not in component:
        return None, None
    module, name = component.rsplit(".", 1)
    try:
        mod = importlib.import_module(module)
    except Exception:  # noqa: BLE001 — unimportable is a contract finding, not a crash
        return None, module
    return getattr(mod, name, None), module


def _is_process(cls) -> bool:
    try:
        import process_bigraph as pb
        return isinstance(cls, type) and issubclass(cls, pb.Process)
    except Exception:  # noqa: BLE001 — process_bigraph absent / not a class
        return False


def _config_params(cls) -> dict | None:
    cs = getattr(cls, "config_schema", None)
    if not isinstance(cs, dict):
        return None
    out: dict = {}
    for k, v in cs.items():
        if isinstance(v, dict):
            out[k] = {"type": v.get("_type"), "default": v.get("_default"),
                      "units": v.get("_units") or v.get("units")}
        else:
            out[k] = {"type": v, "default": None, "units": None}
    return out


def _static_ports(cls, which: str) -> dict | None:
    """Best-effort read of a Process's declared ports without a core: call the (static) port
    method on an uninitialized instance. Processes whose ports depend on config/self return
    None rather than a wrong answer."""
    fn = getattr(cls, which, None)
    if fn is None:
        return None
    try:
        ports = fn(object.__new__(cls))
        return ports if isinstance(ports, dict) else None
    except Exception:  # noqa: BLE001 — ports depend on config/core; not statically knowable
        return None


def inspect_component(component, ws_root=None) -> ModelContract:
    """Read ``component`` (a Process class/callable, or a dotted path) into a ModelContract."""
    cls, module = _load(component)
    name = component if isinstance(component, str) else getattr(component, "__name__", str(component))
    if cls is None:
        return ModelContract(component=name, importable=False, is_process=False, module=module,
                             notes=["not importable / unresolved"])
    is_proc = _is_process(cls)
    params = _config_params(cls) if is_proc else None
    inputs = _static_ports(cls, "inputs") if is_proc else None
    outputs = _static_ports(cls, "outputs") if is_proc else None
    notes: list = []
    if is_proc and inputs is None and outputs is None:
        notes.append("ports not statically introspectable (depend on config/core)")
    if not is_proc:
        notes.append("importable but not a process-bigraph Process — interface not inspected")
    return ModelContract(component=name, importable=True, is_process=is_proc, module=module,
                         parameters=params, input_ports=inputs, output_ports=outputs, notes=notes)


def validate_composite(component, ws_root=None, *, expected=None) -> dict:
    """Validate ``component``, optionally against an ``expected`` ModelContract (or dict). Reports
    issues: ``not_importable``, ``missing_port`` (an expected input/output port the component does
    not declare), and ``unit_mismatch`` (a shared port/param whose units differ). Returns
    ``{valid, issues, contract}``."""
    c = inspect_component(component, ws_root)
    issues: list = []
    if not c.importable:
        issues.append({"kind": "not_importable", "detail": f"{c.component} does not resolve"})
    exp = expected.to_dict() if isinstance(expected, ModelContract) else (expected or {})

    for side in ("input_ports", "output_ports"):
        want = exp.get(side) or {}
        have = getattr(c, side) or {}
        for port, wt in want.items():
            if port not in have:
                issues.append({"kind": "missing_port", "side": side, "port": port,
                               "detail": f"expected {side[:-1]} '{port}' not declared by {c.component}"})
            elif _units_of(wt) and _units_of(have[port]) and _units_of(wt) != _units_of(have[port]):
                issues.append({"kind": "unit_mismatch", "side": side, "port": port,
                               "detail": f"port '{port}' units {_units_of(have[port])} != expected {_units_of(wt)}"})

    want_params = exp.get("parameters") or {}
    have_params = c.parameters or {}
    for p, wv in want_params.items():
        if p in have_params and _units_of(wv) and _units_of(have_params[p]) \
                and _units_of(wv) != _units_of(have_params[p]):
            issues.append({"kind": "unit_mismatch", "param": p,
                           "detail": f"param '{p}' units {_units_of(have_params[p])} != expected {_units_of(wv)}"})

    return {"valid": not issues, "issues": issues, "contract": c.to_dict()}


def compose_and_validate_model(components: dict, wiring: list, ws_root=None) -> dict:
    """Compose independently-authored components by wiring their ports, and validate the
    coupling before the model is declared runnable (§2).

    ``components``: ``{name -> Process class/callable or dotted path}``.
    ``wiring``: a list of edges ``{"source": [comp, out_port], "target": [comp, in_port]}`` —
    each connects a producer's output port to a consumer's input port.

    Validates, per §2's acceptance ("produce a runnable coupled model and detect an intentionally
    incompatible unit or missing exchange port"): every component is importable and a Process;
    every wired port exists on its component (else ``missing_port``), and the two ends of each
    wire carry compatible units (else ``unit_mismatch``). Returns ``{runnable, issues, model}``.
    Pure: no run is triggered. (Runtime mass-balance conservation and a reproducible smoke run
    are follow-up increments; this is the static coupling gate.)"""
    contracts = {name: inspect_component(c, ws_root) for name, c in (components or {}).items()}
    issues: list = []

    for name, c in contracts.items():
        if not c.importable:
            issues.append({"kind": "not_importable", "component": name})
        elif not c.is_process:
            issues.append({"kind": "not_a_process", "component": name})

    for edge in wiring or []:
        src = edge.get("source") or []
        tgt = edge.get("target") or []
        if len(src) != 2 or len(tgt) != 2:
            issues.append({"kind": "malformed_wire", "edge": edge})
            continue
        s_comp, s_port = src
        t_comp, t_port = tgt
        sc, tc = contracts.get(s_comp), contracts.get(t_comp)
        if sc is None or tc is None:
            issues.append({"kind": "unknown_component", "edge": edge,
                           "detail": f"wire references unknown component(s): {s_comp!r}/{t_comp!r}"})
            continue
        s_ports = sc.output_ports or {}
        t_ports = tc.input_ports or {}
        ok = True
        if s_port not in s_ports:
            issues.append({"kind": "missing_port", "side": "output", "component": s_comp,
                           "port": s_port, "detail": f"{s_comp} declares no output port '{s_port}'"})
            ok = False
        if t_port not in t_ports:
            issues.append({"kind": "missing_port", "side": "input", "component": t_comp,
                           "port": t_port, "detail": f"{t_comp} declares no input port '{t_port}'"})
            ok = False
        if ok:
            su, tu = _units_of(s_ports[s_port]), _units_of(t_ports[t_port])
            if su and tu and su != tu:
                issues.append({"kind": "unit_mismatch", "edge": edge,
                               "detail": f"wire {s_comp}.{s_port} ({su}) -> {t_comp}.{t_port} ({tu})"})

    runnable = not issues
    model = {"components": sorted(contracts), "wiring": list(wiring or []),
             "contracts": {n: c.to_dict() for n, c in contracts.items()}}
    return {"runnable": runnable, "issues": issues, "model": model}
