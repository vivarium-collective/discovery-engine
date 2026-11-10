"""Pre-publication linter for workspace study reports (decomposed package).

This package was split out of the former single-file
``discovery_engine/reporting/linter.py``. The entire former public surface
(every name previously importable as ``from discovery_engine.reporting.linter
import X``) is re-exported here unchanged, so existing imports keep working.

The former module was a single namespace, which let callers monkeypatch a
helper (e.g. ``monkeypatch.setattr(linter, "fetch_study_observable_check",
...)``) and have the check that uses it pick up the patch. After the split
the check lives in a submodule and reads that name from the submodule's own
namespace, so a patch on the package alone would not reach it. To preserve
the single-namespace patch semantics verbatim, this package module forwards
every attribute assignment to each submodule that also defines that name
(see ``_LinterPackage.__setattr__`` below).
"""
from __future__ import annotations

import sys as _sys
import types as _types

from discovery_engine.reporting.linter import (
    context,
    investigation_checks,
    checks_report,
    checks_findings,
    checks_structure,
    checks_completeness,
    checks_render,
    checks_rigor,
    engine,
)

# Primary entry points (explicit, for readability / static tools).
from discovery_engine.reporting.linter.context import (  # noqa: F401
    LintFinding,
    load_overrides,
    write_override,
    has_blocking_errors,
    apply_overrides,
    unresolved_composite_refs,
)
from discovery_engine.reporting.linter.engine import (  # noqa: F401
    lint_workspace_report,
    format_findings,
    main,
    register_ruleset,
    available_rulesets,
    set_default_rulesets,
    CHECKS,
)

_SUBMODULES = (
    context,
    investigation_checks,
    checks_report,
    checks_findings,
    checks_structure,
    checks_completeness,
    checks_render,
    checks_rigor,
    engine,
)

# Re-export the ENTIRE former top-level surface. Every name defined (or
# imported) at module top level in any submodule is injected into this
# package namespace so that pre-split imports keep working verbatim,
# including the underscore-prefixed names that are part of the contract.
for _sub in _SUBMODULES:
    for _name in dir(_sub):
        if _name.startswith("__"):
            continue
        globals().setdefault(_name, getattr(_sub, _name))
del _sub, _name


class _LinterPackage(_types.ModuleType):
    """Package module whose attribute sets propagate into the submodules.

    Restores the former single-module monkeypatch behaviour: setting
    ``linter.X`` also sets ``X`` on every submodule that defines it, so a
    check reading its own module-level ``X`` observes the patch (and its
    later restoration) exactly as it did before the split.
    """

    def __setattr__(self, name, value):
        super().__setattr__(name, value)
        if not name.startswith("__"):
            for _s in _SUBMODULES:
                if hasattr(_s, name):
                    setattr(_s, name, value)


_sys.modules[__name__].__class__ = _LinterPackage
