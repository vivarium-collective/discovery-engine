"""Back-compat shim — moved to process_bigraph.core_introspection (Phase 1)."""
from process_bigraph.core_introspection import *          # noqa: F401,F403
from process_bigraph.core_introspection import (           # noqa: F401 - explicit: names not in __all__ that consumers use
    _PROCESS_ATTRS, _TYPE_ATTRS, _try,
    list_processes, list_types, registry_snapshot,
)
