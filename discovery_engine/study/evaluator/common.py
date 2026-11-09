"""Shared primitives for the study evaluator package.

Foundational, dependency-free pieces used across the evaluator submodules:
the closed run-data measure-kind set, the two evaluator exceptions, the outcome
bucket constructors, small scalar utilities, and the closed pass_if operator set.
"""
from __future__ import annotations

from typing import Any


# ---------------------------------------------------------------------------
# Closed set: run-data-evaluable measure kinds
# ---------------------------------------------------------------------------

RUN_DATA_KINDS: frozenset[str] = frozenset({
    "range_check_per_generation",
    "generation_average",
    "derived",         # authored spelling in study.yaml; "derived_scalar" is the alias
    "derived_scalar",
    "per_generation_mass_ratio",
    "oric_initiations_per_generation",
    "rate_match",
    "snapshot_window",
    "count_over_lineage",
    "periodicity_check",
    "per_gen",
})


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ObservableNotFound(Exception):
    """Raised when a path token cannot be resolved via RunReader."""


class WindowNotSupported(Exception):
    """Raised when a window spec is not in the closed vocabulary."""


# ---------------------------------------------------------------------------
# Bucket constructors
# ---------------------------------------------------------------------------

def _agent(reason: str) -> dict:
    return {"evaluated_by": "agent", "reason": reason}


def _needs_rerun(reason: str) -> dict:
    return {"evaluated_by": "needs_rerun", "reason": reason}


def _sig6(x: Any, n: int = 6) -> Any:
    """Round a measured scalar to ``n`` SIGNIFICANT figures (not decimal places).

    Fixed-decimal ``round(x, 6)`` zeroes any value below ~1e-6, which silently
    destroyed small-magnitude observables (e.g. ~1e-22 metabolite fluxes) so their
    findings could never match ``observed`` and never verify. Significant-figure
    rounding preserves magnitude: ``3.34e-22`` stays ``3.34e-22``, ``0.536`` stays
    ``0.536``. Non-finite / None / non-numeric pass through unchanged."""
    import math
    xf = _as_float(x)
    if xf is None:
        return x
    if xf == 0 or not math.isfinite(xf):
        return xf
    return float(f"{xf:.{n}g}")


def _code_outcome(result: str, measured_value: Any, operator: str, detail: str) -> dict:
    return {
        "result": result,
        "measured_value": measured_value,
        "evaluated_by": "code",
        "operator": operator,
        "detail": detail,
    }


# ---------------------------------------------------------------------------
# Scalar utilities
# ---------------------------------------------------------------------------

def _as_float(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _equals_ok(measured: Any, target: Any, pass_if: dict) -> bool:
    """Equality with optional numeric tolerance; exact for categoricals."""
    m, t = _as_float(measured), _as_float(target)
    if m is not None and t is not None:
        tolf = pass_if.get("tolerance_fraction")
        if tolf is not None:
            return abs(m - t) <= abs(float(tolf)) * abs(t)
        tol = float(pass_if["tolerance"]) if pass_if.get("tolerance") is not None else 0.0
        return abs(m - t) <= tol
    return measured == target


# ---------------------------------------------------------------------------
# Closed pass_if operator set
# ---------------------------------------------------------------------------

_SUPPORTED_OPS: frozenset[str] = frozenset({
    "range",
    "in_range",
    "in_range_every_generation",
    "generation_average_in_range",
    "<=", ">=", "<", ">", "==", "!=", "eq", "equals",
    "in_set",
    "cv_below",
    "median_within_tolerance",
    "periodic_doubling_every_generation",
    "exactly_one_initiation_per_generation",
    "max_le", "max_lt", "min_ge", "min_gt",
    "rises_within_cycle",
})


def _op_supported(op: str) -> bool:
    return op in _SUPPORTED_OPS
