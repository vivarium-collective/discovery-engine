"""Back-compat shim: ``discovery_engine.post_sim`` -> ``discovery_engine.integration.post_sim``.

Reorg #45 ("organize the flat module tree into architecture-aligned subpackages")
moved this module into ``integration/`` WITHOUT a compatibility shim, which broke
external consumers that import the old flat path — notably v2ecoli's WCM sim
workflow (``workflow/analysis.py``, ``workflow/post_sim.py``,
``workflow/report_cards/__init__.py``). This restores the old import surface.

New code should import from ``discovery_engine.integration.post_sim``.
"""
import warnings as _warnings

from discovery_engine.integration.post_sim import (  # noqa: F401
    ANALYSIS_SCALES,
    KINDS,
    POST_SIM_REGISTRY,
    REPORT_CARD_REGISTRY,
    VISUALIZATION_REGISTRY,
    Analysis,
    AnalysisStep,
    ReportCardStep,
    StudyContext,
    VisualizationStep,
    iter_post_sim,
    prune,
    register_post_sim,
    write_card,
)

_warnings.warn(
    "discovery_engine.post_sim moved to discovery_engine.integration.post_sim "
    "(reorg #45); the flat path is a back-compat shim and may be removed in a "
    "future major release.",
    DeprecationWarning,
    stacklevel=2,
)
