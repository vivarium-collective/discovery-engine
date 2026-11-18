"""Back-compat shim: ``discovery_engine.post_sim`` -> ``discovery_engine.integration.post_sim``.

Reorg #45 ("organize the flat module tree into architecture-aligned subpackages")
moved this module into ``integration/`` without a compatibility shim, which broke
external consumers that import the old flat path. This restores the old import
surface. New code should import from ``discovery_engine.integration.post_sim``.

Re-exports are LAZY (PEP 562 ``__getattr__``) so merely importing this root-level
module does not eagerly pull in the integration subpackage.
"""
import warnings as _warnings

_NEW = "discovery_engine.integration.post_sim"
_NAMES = frozenset({
    "ANALYSIS_REGISTRY", "ANALYSIS_SCALES", "Analysis", "AnalysisStep", "KINDS", "POST_SIM_REGISTRY",
    "REPORT_CARD_REGISTRY", "VISUALIZATION_REGISTRY", "ReportCardStep",
    "VisualizationStep", "StudyContext", "iter_post_sim", "prune",
    "register_post_sim", "write_card",
})

_warnings.warn(
    f"discovery_engine.post_sim moved to {_NEW} (reorg #45); the flat path is a "
    "back-compat shim and may be removed in a future major release.",
    DeprecationWarning, stacklevel=2)


def __getattr__(name):
    if name in _NAMES:
        import importlib
        return getattr(importlib.import_module(_NEW), name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def __dir__():
    return sorted(_NAMES)
