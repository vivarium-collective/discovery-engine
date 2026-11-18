"""Back-compat shim: ``discovery_engine.card_grade`` ->
``discovery_engine.reporting.card_grade`` (moved by reorg #45 without a shim;
re-exposed for v2ecoli's ``library/report_card.py``). New code should import from
``discovery_engine.reporting.card_grade``."""
import warnings as _warnings

from discovery_engine.reporting.card_grade import (  # noqa: F401
    _slug_group,
    grade_card,
    render_verdict_html,
    verdict_json,
)

_warnings.warn(
    "discovery_engine.card_grade moved to discovery_engine.reporting.card_grade "
    "(reorg #45); the flat path is a back-compat shim.",
    DeprecationWarning,
    stacklevel=2,
)
