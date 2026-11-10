"""discover — the discovery-cycle write/wire layer, split into a cohesive package.

This package re-exports the complete public surface of the former single-file
``discovery_engine/discover.py`` module, so every existing
``from discovery_engine.discover import X`` keeps working and the console-script
entry point ``discovery_engine.discover:main`` still resolves.

Submodules:
  * :mod:`._common`  — shared leaf helpers + vocab (bottom of the import graph).
  * :mod:`.actions`  — author / write ops (resolutions, viz, verdict, revisions).
  * :mod:`.cycle`    — cycle orchestration (run/advance/persist/execute, readiness).
  * :mod:`.cli`      — ``main()`` + argparse + terminal-rendering helpers.
"""
from __future__ import annotations

# Re-exported third-party / stdlib / sibling-package names that external code
# imports from this path (e.g. ``from discovery_engine.discover import load_yaml``).

import argparse
import copy
import datetime as _dt
import hashlib
import re
from io import StringIO
from pathlib import Path

from ruamel.yaml import YAML

from discovery_engine.projection.argument import derive_argument, merge_argument
from discovery_engine.investigation.import_ import investigation_members, investigations_dir, studies_dir
from discovery_engine.projection.knowledge import canonical_verdict, derive_knowledge, merge_knowledge
from discovery_engine.projection.narrative import audit, narrate
from discovery_engine.study.run_command import render_command, run_command
from discovery_engine.study.io import atomic_write, load_yaml, load_yaml_mapping, save_yaml_atomic
from discovery_engine.projection.synthesis import open_calls, synthesize_gates
from discovery_engine.reporting.validation import ValidationContext, run_validators

# Shared leaf helpers + vocab
from discovery_engine.discover._common import (
    EPISTEMIC_RELATIONS,
    RESOLUTION_KINDS,
    VERDICT_VOCAB,
    _ruamel,
    _study_dir_on_disk,
    _viz_target,
)

# Author / write ops
from discovery_engine.discover.actions import (
    _apply_refinement,
    _cap_gap_load,
    _close_built_viz_proposals,
    _complement_band,
    _enrich_file,
    _now,
    _pick_candidate,
    _set_relation_inplace,
    _study_path,
    _validate_extensions,
    _viz_prop,
    _write_seeded_study,
    accept_capability_gap,
    accept_verdict,
    accept_viz_proposal,
    add_resolution,
    apply_revision,
    connect_investigation,
    declare_viz,
    record_resolution,
    refresh_narrative,
    reject_capability_gap,
    reject_viz_proposal,
    relate,
    review_revision,
)

# Cycle orchestration
from discovery_engine.discover.cycle import (
    _ADVANCE_OPS,
    _advance_study_for,
    _append_executed,
    _arm_mvs,
    _cycle_with_evidence,
    _declared_member_slugs,
    _default_runner,
    _dispatch_classify,
    _evaluate_applied_proposals,
    _has_run_at_current_mv,
    _latest_run_id_for_command,
    _load_runs_by_study,
    _member_slugs_on_disk,
    _parent_in_parens,
    _pinned_run,
    _post_render_stamp,
    _post_run_sync,
    _renew_executive_decisions,
    _reproduces_gap,
    _run_mv_id,
    _run_names,
    _tab_freshness,
    _viz_entry,
    _viz_resolvable,
    _viz_scope_of,
    advance_cycle,
    build_completion_map,
    build_cycle_state,
    dispatchable_actions,
    execute_action,
    load_cycle_inputs,
    load_workspace_build_hook,
    missing_members,
    persist_cycle,
    project_model_versions,
    run_cycle,
    run_readiness,
    set_verdict,
    snapshot_validations,
    suggest_studies,
    viz_inventory,
)

# CLI entry point + rendering
from discovery_engine.discover.cli import (
    _coerce_scalar,
    _env_decider,
    _fmt_expected,
    _fmt_when,
    _mod_coupling_issues,
    _summarize_design,
    _summarize_discovery_delta,
    _summarize_evaluation,
    _summarize_evidence_reconciliation,
    _summarize_hypotheses,
    _summarize_model_build,
    _summarize_portfolio,
    _summarize_refinement,
    _summarize_seeded_study,
    main,
    summarize,
    summarize_advance,
    summarize_completion,
    summarize_suggestions,
)

__all__ = [
    'EPISTEMIC_RELATIONS', 'Path', 'RESOLUTION_KINDS', 'StringIO', 'VERDICT_VOCAB',
    'ValidationContext', 'YAML', '_ADVANCE_OPS', '_advance_study_for', '_append_executed',
    '_apply_refinement', '_arm_mvs', '_cap_gap_load', '_close_built_viz_proposals',
    '_coerce_scalar', '_complement_band', '_cycle_with_evidence', '_declared_member_slugs',
    '_default_runner', '_dispatch_classify', '_dt', '_enrich_file', '_env_decider',
    '_evaluate_applied_proposals', '_fmt_expected', '_fmt_when', '_has_run_at_current_mv',
    '_latest_run_id_for_command', '_load_runs_by_study', '_member_slugs_on_disk',
    '_mod_coupling_issues', '_now', '_parent_in_parens', '_pick_candidate', '_pinned_run',
    '_post_render_stamp', '_post_run_sync', '_renew_executive_decisions', '_reproduces_gap',
    '_ruamel', '_run_mv_id', '_run_names', '_set_relation_inplace', '_study_dir_on_disk',
    '_study_path', '_summarize_design', '_summarize_discovery_delta', '_summarize_evaluation',
    '_summarize_evidence_reconciliation', '_summarize_hypotheses', '_summarize_model_build',
    '_summarize_portfolio', '_summarize_refinement', '_summarize_seeded_study',
    '_tab_freshness', '_validate_extensions', '_viz_entry', '_viz_prop', '_viz_resolvable',
    '_viz_scope_of', '_viz_target', '_write_seeded_study', 'accept_capability_gap',
    'accept_verdict', 'accept_viz_proposal', 'add_resolution', 'advance_cycle', 'annotations',
    'apply_revision', 'argparse', 'atomic_write', 'audit', 'build_completion_map',
    'build_cycle_state', 'canonical_verdict', 'connect_investigation', 'copy', 'declare_viz',
    'derive_argument', 'derive_knowledge', 'dispatchable_actions', 'execute_action', 'hashlib',
    'investigation_members', 'investigations_dir', 'load_cycle_inputs',
    'load_workspace_build_hook', 'load_yaml', 'load_yaml_mapping', 'main', 'merge_argument',
    'merge_knowledge', 'missing_members', 'narrate', 'open_calls', 'persist_cycle',
    'project_model_versions', 're', 'record_resolution', 'refresh_narrative',
    'reject_capability_gap', 'reject_viz_proposal', 'relate', 'render_command',
    'review_revision', 'run_command', 'run_cycle', 'run_readiness', 'run_validators',
    'save_yaml_atomic', 'set_verdict', 'snapshot_validations', 'studies_dir',
    'suggest_studies', 'summarize', 'summarize_advance', 'summarize_completion',
    'summarize_suggestions', 'synthesize_gates', 'viz_inventory',
]



def _install_patch_forwarding() -> None:
    """Preserve single-module ``monkeypatch`` semantics after the package split.

    When ``discover`` was one module, ``monkeypatch.setattr(discovery_engine.discover,
    name, obj)`` rebound a module global that every internal caller resolved — so the
    patch took effect everywhere. Now that the helpers live in submodules, each caller
    resolves ``name`` in its own submodule namespace. This installs a thin module
    subclass whose ``__setattr__`` forwards any write on the package to every submodule
    that already binds that ``name``, so a patch on ``discovery_engine.discover.X`` is
    observed by callers in ``cycle``/``actions``/``cli`` exactly as before. Pure wiring:
    no function logic changes.
    """
    import sys
    import types

    from discovery_engine.discover import _common, actions, cli, cycle

    submodules = (_common, actions, cycle, cli)

    class _DiscoverPackage(types.ModuleType):
        def __setattr__(self, name, value):
            super().__setattr__(name, value)
            for mod in submodules:
                if name in vars(mod):
                    mod.__dict__[name] = value

    sys.modules[__name__].__class__ = _DiscoverPackage


_install_patch_forwarding()
del _install_patch_forwarding
