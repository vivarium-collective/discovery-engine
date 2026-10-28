"""discovery-engine — a derive-first scientific-investigation cycle engine.

Pure projection + reasoning over an investigation (``investigation.yaml``) and
its member studies (``study.yaml``):

    run_cycle        -> knowledge / argument / narrative / gates / validators / suggestions
    frontier/closure -> the prioritized unresolved work + the machine-checkable closure test
    opportunities    -> value-ranked frontier (info / impact / enable / closure - cost)
    run_readiness    -> is a study materially ready to produce an evaluable, rendered result?
    execute_action   -> carry out the next move, consequence-gated
    advance_cycle    -> build-if-needed and run the open questions
    persist_cycle    -> the slim regenerable cycle_state + narrative renewal

The projection is pure (no disk I/O) and derive-first: every view is computed
from the studies, then merged over any authored override (authored wins, empty
never clobbers). Standalone; consumed by viva-superpowers (the AI agent skills
that drive it) and vivarium-workbench (the UI/server).
"""
from __future__ import annotations

from . import (  # noqa: F401  submodule access for the full surface
    argument_store,
    discover,
    discovery_engine,
    knowledge_store,
    narrative,
    study_evaluator,
    synthesis,
    validation,
)
from .argument_store import derive_argument, merge_argument  # noqa: F401
from .discover import (  # noqa: F401
    advance_cycle,
    build_completion_map,
    build_cycle_state,
    execute_action,
    load_cycle_inputs,
    persist_cycle,
    run_cycle,
    run_readiness,
    set_verdict,
)
from .discovery_engine import (  # noqa: F401
    closure,
    crank,
    diagnose,
    execute_plan,
    frontier,
    invariants,
    next_action,
    opportunities,
)
from .knowledge_store import (  # noqa: F401
    canonical_verdict,
    derive_knowledge,
    merge_knowledge,
)

__version__ = "0.1.0"
