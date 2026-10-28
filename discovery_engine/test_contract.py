"""Typed check/axis contract for report_card_verdict/v2.

`check()` produces one v2 axis dict (v1 axis fields plus expected/margin/severity/
knob/citation) with a computed verdict + signed margin. Pure stdlib; the axis dict
is exactly what verdict_json/TestBuilder embed under groups[g]['axes'].
"""
from __future__ import annotations
import math
from dataclasses import dataclass, asdict

from discovery_engine.test_vocab import normalize_verdict


@dataclass(frozen=True)
class Expected:
    kind: str                       # "value" | "band" | "predicate"
    value: float | None = None
    low: float | None = None
    high: float | None = None
    op: str = "~="
    tol: float = 0.05
    statement: str | None = None
    units: str | None = None        # the band/target's declared unit (for reconciliation)
    def to_dict(self) -> dict:
        return asdict(self)


def value(target, op="~=", tol=0.05, units=None) -> Expected:
    return Expected(kind="value", value=float(target), op=op, tol=float(tol), units=units)

def band(low, high, units=None) -> Expected:
    return Expected(kind="band", low=float(low), high=float(high), units=units)


def _reconcile_units(value, from_u, to_u):
    """Convert ``value`` from ``from_u`` to ``to_u``. Returns ``(value, error)``:
    error is None when nothing to do / converted cleanly, or when no unit registry
    is available (can't check → never fabricate); error is a message when the units
    are dimensionally incompatible (fail loudly)."""
    if not from_u or not to_u or from_u == to_u:
        return value, None
    try:
        from bigraph_schema.units import get_quantity_registry
    except Exception:  # noqa: BLE001 — no registry: can't check, don't fabricate
        return value, None
    try:
        reg = get_quantity_registry()
        return reg.Quantity(value, from_u).to(to_u).magnitude, None
    except Exception:  # noqa: BLE001 — incompatible/unparseable: flag it
        return value, f"unit mismatch: cannot convert {from_u!r} to the expected {to_u!r}"

def predicate(statement) -> Expected:
    return Expected(kind="predicate", statement=str(statement))


def sanitize(obj):
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [sanitize(v) for v in obj]
    return obj


def _margin(observed: float, e: Expected):
    if e.kind == "band":
        return min(observed - e.low, e.high - observed)
    # kind == "value"
    t, target, op = e.tol, e.value, e.op
    if op == "~=":
        scale = abs(target) if target != 0 else 1.0
        return t * scale - abs(observed - target)
    if op in ("<=", "<"):
        return target - observed
    if op in (">=", ">"):
        return observed - target
    if op == "==":
        return -abs(observed - target)
    raise ValueError(f"unknown op {op!r}")


def _passes(margin: float, e: Expected) -> bool:
    if e.kind == "value" and e.op in ("<", ">"):
        return margin > 0          # strict
    return margin >= 0


def _meter(margin: float, e: Expected):
    ref = abs(e.value or e.high or e.low or 1.0) or 1.0
    return max(0.0, min(1.0, 0.5 + margin / (2.0 * ref)))


def check(id, label, observed, expected: Expected, *, severity="hard",
          units=None, knob=None, cite=None, detail=None, verdict=None) -> dict:
    margin = None
    meter = None
    if expected.kind != "predicate" and isinstance(observed, (int, float)):
        obs = float(observed)
        if not math.isfinite(obs):
            # a non-finite measurement isn't gradable, and nan/inf margins
            # break allow_nan=False JSON serialization of the axis dict.
            v = "ungraded"
            margin = None
            meter = None
        else:
            obs, unit_err = _reconcile_units(obs, units, expected.units)
            if unit_err is not None:
                # declared units disagree dimensionally — fail loudly, don't grade
                v = "mismatch"
                detail = (detail + " · " if detail else "") + unit_err
            else:
                margin = _margin(obs, expected)
                passed = _passes(margin, expected)
                if passed:
                    v = "within_tol"
                elif severity == "directional":
                    v = "drift"
                else:
                    v = "mismatch"
                meter = _meter(margin, expected)
    else:
        v = normalize_verdict(verdict)
    return sanitize({
        "id": id, "label": label, "verdict": v,
        "value": observed, "meter": meter, "detail": detail,
        "expected": expected.to_dict(), "margin": margin,
        "severity": severity, "units": units,
        "knob": list(knob) if knob else None, "citation": cite,
    })


from discovery_engine.test_vocab import worst as _worst


def _slug_group(label: str) -> str:
    return (label or "ungrouped").strip().lower().replace("&", "and").replace(" ", "_")


class TestBuilder:
    """Accumulate check() axes into a report_card_verdict/v2 document."""

    def __init__(self, model_ref="", reference_model="", generated=""):
        self.model_ref = model_ref
        self.reference_model = reference_model
        self.generated = generated
        self._groups: dict[str, list] = {}

    def add(self, group: str, axis: dict) -> "TestBuilder":
        self._groups.setdefault(_slug_group(group), []).append(axis)
        return self

    def build(self) -> dict:
        groups = {}
        all_verdicts = []
        for gslug, axes in self._groups.items():
            vs = [a.get("verdict", "ungraded") for a in axes]
            groups[gslug] = {"verdict": _worst(vs), "axes": axes}
            all_verdicts.extend(vs)
        return sanitize({
            "schema": "report_card_verdict/v2",
            "model_ref": self.model_ref,
            "reference_model": self.reference_model,
            "generated": self.generated,
            "overall": _worst(all_verdicts),
            "groups": groups,
        })
