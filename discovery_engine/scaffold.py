"""Compatibility alias: moved to :mod:`discovery_engine.integration.scaffold`.

Workspace/server plumbing is not discovery logic; import it from the
integration layer. This alias keeps old import paths (and monkeypatching) working.
"""
import sys as _sys
from discovery_engine.integration import scaffold as _m

_sys.modules[__name__] = _m
