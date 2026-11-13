"""Recovery-track scenario: a workspace that raises a ``capability_gap`` and nothing more.

Only the gap-trigger side is set up here. ``s1`` declares ``required_mechanisms:
[feedback_inhibition]`` and references a composite the catalog cannot resolve, so it stays
``needs_build`` and is never run. The study that actually runs is MATERIALIZED later by
``recovery_build.build_hook`` (``built-<mechanism>``): a ``test`` op outscores a
``build_process`` op, so a pre-existing study cannot become ready after the build.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from discovery_engine.benchmark import recovery_model
from discovery_engine.benchmark.scenario import GroundTruth
from discovery_engine.investigation.import_ import investigations_dir, studies_dir
from discovery_engine.study import evaluator as se

MECH = "feedback_inhibition"


@dataclass(frozen=True)
class RecoveryScenario:
    ws_root: Path
    inv_slug: str
    study_slug: str          # the gap-trigger study (s1), never run
    ground_truth: GroundTruth
    test_kind: str


def build_recovery_scenario(tmp_path, *, name: str = "rec") -> RecoveryScenario:
    se.clear_workspace_evaluator_cache()
    ws = Path(tmp_path)
    base, kind = recovery_model.DEFAULT_BASE, recovery_model.TOY_KIND
    (ws / "workspace.yaml").write_text(f"name: {name}ws\n", encoding="utf-8")

    pkg = ws / f"pbg_{name}ws"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "evaluators.py").write_text(
        recovery_model.STORELESS_EVALUATOR_SRC(kind, base), encoding="utf-8")

    study = "s1"
    inv = investigations_dir(ws) / name
    inv.mkdir(parents=True, exist_ok=True)
    (inv / "investigation.yaml").write_text(yaml.safe_dump({
        "name": name, "slug": name, "studies": [study],
        "required_mechanisms": [MECH],
    }), encoding="utf-8")

    sd = studies_dir(ws) / study
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "study.yaml").write_text(yaml.safe_dump({
        "schema_version": 4,
        "name": study,
        "topic": "growth",                    # does NOT tokenize to feedback_inhibition
        "required_mechanisms": [MECH],
        "builds": [MECH],                     # derivable gap (accept authorizes the build)
        "baseline": [{"composite": "json.nonexistent_mod.GrowthComposite"}],  # top pkg importable, module not -> unresolvable (not run-ready)
    }), encoding="utf-8")

    truth = GroundTruth(
        observable=kind, base_value=base, true_value=base / 2,
        band={"op": "range", "low": 0.3, "high": 0.8},
        concealed_mechanism=MECH, correct_composite="")
    return RecoveryScenario(ws, name, study, truth, kind)
