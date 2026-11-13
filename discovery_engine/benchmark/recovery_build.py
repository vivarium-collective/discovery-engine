"""Recovery-bridge spike (Task 1): the capability-gap build hook.

The engine never builds itself — it drives the workspace-injected ``build_hook``, then
verifies the newly built process COVERS the gap's declared mechanism (token-superset, not
name-only).  This hook writes a catalog component (a study whose composite's capability
tokens cover the gap mechanism) AND records ``implements: <implemented_mechanism>`` — what
was actually built, which the store-less evaluator reads back.

The decoy half of the chain test passes ``implemented="substrate_depletion"`` while the
gap's declared mechanism is still ``feedback_inhibition``: the structural coverage gate
still passes (capability covers the declared mechanism) but the implemented mechanism is
wrong, so the recovered observable lands out of band.

Minimal-but-real spike helper; Task 2 finalizes it.
"""
from __future__ import annotations

import re
from typing import Any, Callable

import yaml

from discovery_engine.benchmark.recovery_model import BAND, TOY_KIND
from discovery_engine.investigation.import_ import investigations_dir, studies_dir


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(text).lower()).strip("-") or "mechanism"


def built_study_slug(mechanism: str) -> str:
    """Deterministic slug of the study the build materializes for a gap's mechanism —
    the chain test points at this study dir to read back the recovered observation."""
    return "built-" + _slug(mechanism)


def build_hook(implemented_mechanism: str) -> Callable[[dict, Any], dict]:
    """Return a ``build_hook(gap, ws_root) -> dict`` that MATERIALIZES the recovered-mechanism
    study: a composite providing the gap's declared mechanism, the behavior test + finding that
    measure the recovered observable, and ``implements:`` recording what was ACTUALLY built.

    Why the build writes the whole runnable study (not just a bare composite): the engine ranks
    a ``test`` op strictly above a ``build_process`` op, and a study classified ``needs_build`` is
    marked ``seen`` for the rest of the advance — so a study present BEFORE the build always runs
    first (before its mechanism exists) and never re-runs. Materializing the study IN the build
    makes its ``test`` op appear only on the re-crank AFTER the build, so advance does build-THEN-run
    honestly, matching the brief's "a study blocked on the just-built mechanism becomes run-ready".

    The written study's ``topic`` is the gap's *declared* mechanism (so ``build_catalog`` derives
    capability tokens that COVER it and the engine's coverage gate passes), while the top-level
    ``implements:`` field records ``implemented_mechanism`` (faithful or decoy — the decoy covers
    the declared mechanism structurally yet implements the wrong one).
    """
    def hook(gap: dict, ws_root: Any) -> dict:
        mechanism = gap["mechanism"]                       # the gap's DECLARED mechanism
        topic = mechanism.replace("_", " ")                # capability tokens cover it
        composite = "recovered." + "".join(w.capitalize() for w in topic.split())
        slug = built_study_slug(mechanism)
        sd = studies_dir(ws_root) / slug
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "study.yaml").write_text(yaml.safe_dump({
            "schema_version": 4,
            "name": slug,
            "topic": topic,
            "baseline": [{"composite": composite}],
            "implements": implemented_mechanism,          # what was ACTUALLY built
            "observables": [],
            "behavior_tests": [{
                "name": "recover",
                "measure": {"kind": TOY_KIND},            # store-less toy kind, no path
                "pass_if": dict(BAND),
            }],
            "findings": [{
                "id": "f-recover",
                "statement": None,                        # unassessed -> open_question (test op)
                "evidence": {"measure": "recover", "from_test": "recover"},
            }],
        }), encoding="utf-8")
        # Register the new study in every investigation so build_catalog (which loads
        # studies from the investigation's `studies` list) sees the covering component.
        for ip in sorted(investigations_dir(ws_root).glob("*/investigation.yaml")):
            d = yaml.safe_load(ip.read_text(encoding="utf-8")) or {}
            d["studies"] = sorted(set(d.get("studies") or []) | {slug})
            ip.write_text(yaml.safe_dump(d), encoding="utf-8")
        return {"status": "built", "process": composite, "gap_id": gap["id"],
                "detail": f"built {composite} implementing {implemented_mechanism!r}"}
    return hook
