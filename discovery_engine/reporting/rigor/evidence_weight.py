"""Per-finding evidential weight (item 8) — a strong/moderate/weak chip per
finding, computed by REUSING the study-level rigor predicates restricted to the
one finding (degrading to study-level signals when it can't be matched).
"""
from __future__ import annotations

from discovery_engine.reporting.rigor.common import (
    _as_list,
    _nonempty,
    _replicate_count,
    _replication_agreement,
)


def _finding_match_tokens(finding: dict) -> set[str]:
    """Lower-cased identifiers a finding can be matched on — keyed primarily by
    ``evidence.from_test`` (per the contract), plus the finding's measure/name."""
    toks: set[str] = set()
    ev = finding.get("evidence")
    if isinstance(ev, dict):
        for k in ("from_test", "measure", "observable"):
            v = ev.get(k)
            if isinstance(v, str) and v.strip():
                toks.add(v.strip().lower())
    for k in ("measure", "id", "name", "test"):
        v = finding.get(k)
        if isinstance(v, str) and v.strip():
            toks.add(v.strip().lower())
    return toks


def _finding_divergence_present(finding: dict) -> bool:
    """Effect-size signal: ``calibration_anchor.divergence_factor`` (or the
    mirrored ``evidence.divergence_factor``) is present and meaningfully > 0.
    No recompute — reads the value the finding-observations pass already wrote."""
    for src in (finding.get("calibration_anchor"), finding.get("evidence")):
        if isinstance(src, dict):
            df = src.get("divergence_factor")
            try:
                if df is not None and abs(float(df)) > 0:
                    return True
            except (TypeError, ValueError):
                pass
    return False


def _control_matches(control: dict, tokens: set[str]) -> bool:
    if not tokens:
        return False
    hay = " ".join(str(control.get(k) or "") for k in
                   ("name", "test", "for_test", "discriminates", "hypothesis", "measure")).lower()
    return any(t in hay for t in tokens)


def _excluded_alternatives(spec: dict) -> list[dict]:
    """The excluded competing explanations — same source preference as the
    ``alternatives`` rigor dimension (DI-synthesis preferred, top-level fallback)."""
    _di = spec.get("discovery_implications") or {}
    alts: list[dict] = []
    if isinstance(_di, dict):
        alts = [a for a in _as_list(_di.get("alternate_hypotheses")) if isinstance(a, dict)]
    if not alts:
        alts = [a for a in _as_list(spec.get("alternative_hypotheses")) if isinstance(a, dict)]
    return [a for a in alts if (a.get("status") or "").lower() == "excluded"]


def _alt_matches(alt: dict, tokens: set[str]) -> bool:
    if not tokens:
        return False
    hay = " ".join(str(alt.get(k) or "") for k in
                   ("discriminated_by", "discriminator", "discriminates", "claim")).lower()
    return any(t in hay for t in tokens)


def finding_evidential_weight(spec: dict, finding: dict) -> dict:
    """Per-finding evidential weight (item 8).

    Returns ``{"weight": "strong"|"moderate"|"weak", "dims": {...}, "n_supporting": int}``
    where ``dims`` carries the five booleans ``replication, effect_size,
    control_strength, independence, alternatives``.

    Pure and tolerant. Every dimension REUSES an existing study-level predicate,
    restricted to the finding via a TOLERANT matcher keyed on
    ``finding['evidence']['from_test']``; when a finding can't be matched to
    per-finding inputs it degrades to the study-level signal (never mislabels).
    """
    spec = spec or {}
    finding = finding or {}
    tokens = _finding_match_tokens(finding)

    # replication — _replication_agreement restricted to the finding's measure;
    # degrade to the full study robustness block when no per-measure matches.
    n_rep, sweep = _replicate_count(spec)
    rob = spec.get("robustness")
    rep_spec = spec
    if isinstance(rob, dict) and tokens:
        per = [m for m in _as_list(rob.get("per_measure"))
               if isinstance(m, dict) and str(m.get("name") or "").lower() in tokens]
        if per:
            rep_spec = {**spec, "robustness": {**rob, "per_measure": per}}
    disagrees, _ = _replication_agreement(rep_spec, n_rep)
    replication = (sweep or n_rep >= 3) and not disagrees

    # effect_size — the finding's recorded divergence_factor (no recompute).
    effect_size = _finding_divergence_present(finding)

    # control_strength — reuse the discriminating-control filter (PASS + non-empty
    # observed), matched to this finding's test; degrade to "any discriminating
    # control in the study" when none names this test.
    controls = [c for c in _as_list(spec.get("controls")) if isinstance(c, dict)]
    negs = [c for c in controls if (c.get("kind") or "").lower() in ("negative", "adversarial")]
    discriminating = [c for c in negs
                      if str(c.get("result", "")).upper() == "PASS" and _nonempty(c.get("observed"))]
    matched_controls = [c for c in discriminating if _control_matches(c, tokens)]
    control_strength = bool(matched_controls) if matched_controls else bool(discriminating)

    # independence — emergent (not engineered) interpretation.
    independence = (finding.get("mechanism_origin") or "").lower() == "emergent"

    # alternatives — an excluded competing explanation whose discriminator names
    # this finding's test; degrade to "any excluded alternative in the study".
    excluded = _excluded_alternatives(spec)
    matched_alts = [a for a in excluded if _alt_matches(a, tokens)]
    alternatives = bool(matched_alts) if matched_alts else bool(excluded)

    dims = {
        "replication": bool(replication),
        "effect_size": bool(effect_size),
        "control_strength": bool(control_strength),
        "independence": bool(independence),
        "alternatives": bool(alternatives),
    }
    n_supporting = sum(1 for v in dims.values() if v)
    if n_supporting >= 4:
        weight = "strong"
    elif n_supporting >= 2:
        weight = "moderate"
    else:
        weight = "weak"
    return {"weight": weight, "dims": dims, "n_supporting": n_supporting}
