"""Back-compat shim: ``discovery_engine.card_criteria`` ->
``discovery_engine.reporting.card_criteria`` (moved by reorg #45 without a shim;
re-exposed for v2ecoli's ``library/card_criteria.py``). New code should import
from ``discovery_engine.reporting.card_criteria``."""
import warnings as _warnings

from discovery_engine.reporting.card_criteria import (  # noqa: F401
    _SEVERITY,
    _band,
    _fit_threshold_linear,
    _r2,
    _ungraded,
    _welch,
    grade_axis,
)

_warnings.warn(
    "discovery_engine.card_criteria moved to discovery_engine.reporting.card_criteria "
    "(reorg #45); the flat path is a back-compat shim.",
    DeprecationWarning,
    stacklevel=2,
)
