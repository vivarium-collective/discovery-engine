"""Synthetic concealed-mechanism scenario: the baseline model is WRONG on one observable.

The study's finding records the wrong model's output (``base_value``) as the observed value.
Two live rival hypotheses each predict a band that excludes it, so the cycle raises an
ANOMALY (observed outside every live band) with no revision proposal behind it. Only a new,
generative hypothesis (the concealed mechanism) can explain it; ``true_value`` lies inside
the acceptance ``band``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from discovery_engine.study.io import save_yaml_atomic

WRONG_COMPOSITE = "json.tool.main"                                       # resolves (stdlib)
CORRECT_COMPOSITE = "discovery_engine.phases.model_architect.build_catalog"  # resolves


@dataclass(frozen=True)
class GroundTruth:
    observable: str
    base_value: float
    true_value: float
    band: dict
    concealed_mechanism: str
    correct_composite: str
    decoy_mechanism: str = "substrate_depletion"  # wrong-but-plausible; drives the negative control


@dataclass(frozen=True)
class Scenario:
    ws_root: Path
    inv_slug: str
    ground_truth: GroundTruth


def build_scenario(tmp_path, *, name: str = "bench") -> Scenario:
    root = Path(tmp_path)
    obs = "obs.val"
    truth = GroundTruth(
        observable=obs, base_value=0.95, true_value=0.55,
        band={"op": "range", "low": 0.3, "high": 0.8},
        concealed_mechanism="feedback_inhibition", correct_composite=CORRECT_COMPOSITE)
    inv_slug, study = name, f"{name}-s1"
    (root / "workspace.yaml").write_text(f"name: {name}\n", encoding="utf-8")
    inv = root / "investigations" / inv_slug
    inv.mkdir(parents=True, exist_ok=True)

    def hyp(hid, low, high):
        return {"id": hid, "study": study, "status": "open",
                "predictions": [{"observable": obs,
                                 "expected": {"op": "range", "low": low, "high": high}}]}

    save_yaml_atomic(inv / "investigation.yaml", {
        "name": inv_slug, "studies": [study],
        "hypotheses": [hyp("h-low", 0.0, 0.25), hyp("h-mid", 0.25, 0.3)]})
    sd = root / "studies" / study
    sd.mkdir(parents=True, exist_ok=True)
    params = {"gain": 1.0}
    save_yaml_atomic(sd / "study.yaml", {
        "name": study, "investigation": inv_slug, "schema_version": 4,
        "claim": f"{obs} falls in the accepted band",
        # Authored gate_status "passed" disagrees with the computed verdict (the one run's
        # test is pending → verdict is `blocked`), so the cycle raises a verdict_overreach
        # signal → a `critique` request. It does NOT touch the anomaly path (no `from_test`
        # link below), so acquire_model still fires too. This is what lets `no_critique`
        # discriminate: dropping the critique skill drops the recorded CritiqueReport.
        "gate_status": "passed",
        "baseline": [{"name": "baseline", "composite": WRONG_COMPOSITE, "params": dict(params)}],
        "observables": [obs],
        "behavior_tests": [{"name": "val_in_band",
                            "measure": {"kind": "range_check_per_generation", "path": obs,
                                        "window": "full_lineage_from_gen_0"},
                            "pass_if": dict(truth.band)}],
        "runs": [{"name": "r0", "params": dict(params)}],
        "findings": [{"id": "f-val", "statement": f"baseline gives {obs}={truth.base_value}",
                      # No `from_test`: linking the finding to the band test would make the
                      # evidence ledger mark it `contradicted` (observed violates pass_if),
                      # which routes to REPAIR and suppresses the anomaly signal.
                      "evidence": {"measure": obs, "observed": truth.base_value}}]})
    return Scenario(root, inv_slug, truth)
