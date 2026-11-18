"""Back-compat shim: ``discovery_engine.card_criteria`` ->
``discovery_engine.reporting.card_criteria`` (moved by reorg #45 without a shim).
Restores the old flat import path for external consumers; new code should import
from ``discovery_engine.reporting.card_criteria``. Lazy re-exports (PEP 562)."""
import warnings as _warnings

_NEW = "discovery_engine.reporting.card_criteria"
_NAMES = frozenset({
    "_SEVERITY", "_band", "_fit_threshold_linear", "_r2", "_ungraded", "_welch", "grade_axis",
})

_warnings.warn(
    f"discovery_engine.card_criteria moved to {_NEW} (reorg #45); the flat path is "
    "a back-compat shim.", DeprecationWarning, stacklevel=2)


def __getattr__(name):
    if name in _NAMES:
        import importlib
        return getattr(importlib.import_module(_NEW), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(_NAMES)
