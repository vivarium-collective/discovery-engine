"""Shared leaf helpers and vocab for the discover package.

Low-level, dependency-free utilities used by more than one of the discover
submodules (``actions``, ``cycle``, ``cli``). Importing this module must never
pull in a sibling submodule — it sits at the bottom of the package's import
graph so the others can depend on it without a cycle.
"""
from __future__ import annotations

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


def _ruamel() -> YAML:
    """Comment-preserving round-trip YAML — the same config the canonicalize/reconcile
    writers use (``study_canonicalize._ruamel``). A ``width`` well past any real line
    keeps long scalars on one line so an edge enrichment is a one-field diff, not a
    whole-file reflow that drops load-bearing ``# …`` annotations."""
    y = YAML()
    y.preserve_quotes = True
    y.width = 4096
    y.indent(mapping=2, sequence=4, offset=2)
    return y

#: the canonical scientific verdict vocabulary a human may author
VERDICT_VOCAB = ("supported", "qualified", "inconclusive", "refuted", "unassessed")

#: the epistemic relation vocabulary a prerequisite edge may carry (RFC-0005 §4).
#: These are the facet ``argument_store`` reads to build claim->claim edges; a bare
#: ``condition: tests-passed`` prerequisite is organizational only and yields no
#: argument edge until its epistemic relation is authored.
EPISTEMIC_RELATIONS = ("builds-on", "tests", "refutes", "alternative-to", "motivates")

# The frontier-item kinds a resolution can discharge (mirror discovery_engine priorities).
RESOLUTION_KINDS = ("unaddressed_criterion", "open_challenge", "narrative_gap",
                    "open_decision", "divergence", "open_question",
                    "validation_finding", "stale_validation", "pending_gate",
                    "revision_proposed", "model_disagreement", "anomaly", "verdict_overreach",
                    "viz_broken", "viz_missing", "viz_proposed", "viz_stale",
                    "viz_misplaced", "viz_duplicated", "viz_untied")

def _viz_target(source) -> tuple[str, str] | None:
    """``fig:<study>/<name>`` -> ``(study, name)`` else None."""
    src = str(source)
    if not src.startswith("fig:") or "/" not in src:
        return None
    study, _, name = src[4:].partition("/")
    return (study, name) if study and name else None

def _study_dir_on_disk(ws_root, inv_slug: str, slug: str) -> Path | None:
    for base in (studies_dir(Path(ws_root)), investigations_dir(Path(ws_root)) / inv_slug / "studies"):
        if (base / slug / "study.yaml").exists():
            return base / slug
    return None
