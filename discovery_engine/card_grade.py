"""Back-compat shim: ``discovery_engine.card_grade`` ->
``discovery_engine.reporting.card_grade`` (moved by reorg #45 without a shim).
Restores the old flat import path for external consumers; new code should import
from ``discovery_engine.reporting.card_grade``. Lazy re-exports (PEP 562)."""
import warnings as _warnings

_NEW = "discovery_engine.reporting.card_grade"
_NAMES = frozenset({"_slug_group", "grade_card", "render_verdict_html", "verdict_json"})

_warnings.warn(
    f"discovery_engine.card_grade moved to {_NEW} (reorg #45); the flat path is a "
    "back-compat shim.", DeprecationWarning, stacklevel=2)


def __getattr__(name):
    if name in _NAMES:
        import importlib
        return getattr(importlib.import_module(_NEW), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(_NAMES)
