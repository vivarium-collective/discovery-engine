"""Pure scoring for the recovery track: measured value vs the acceptance band / true value.

Reads ONLY ``result.observed`` and ``result.ran`` plus the ground-truth band and
``true_value``. Never reads the config name or any mechanism/decoy name.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RecoveryMetrics:
    mechanism_recovered: bool
    held_out_error: float | None
    ran: bool


def score_recovery(result, ground_truth) -> RecoveryMetrics:
    obs = result.observed
    band = ground_truth.band
    recovered = obs is not None and band["low"] <= obs <= band["high"]
    err = abs(obs - ground_truth.true_value) if obs is not None else None
    return RecoveryMetrics(mechanism_recovered=bool(recovered), held_out_error=err,
                           ran=result.ran)
