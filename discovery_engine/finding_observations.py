"""finding_observations.py — deterministic writer for biology-forward finding slots.

``populate_finding_observations(study_dir) -> {filled, skipped}`` fills the
quantitative code-owned slots in study.yaml's ``findings[]`` from the data
that already exists after ``compute_outcomes`` runs:

  - ``evidence.observed``       ← ``computed_outcomes[T].measured_value``
  - ``evidence.units``          ← matched ``readouts[].units``
  - ``expected.range``          ← ``[low, high]`` from ``test.pass_if``
  - ``expected.threshold``      ← ``threshold`` from ``test.pass_if``
  - ``expected.cites``          ← ``test.cites``
  - ``provenance.run_ids``      ← ``[canonical_run.name]``
  - ``evidence.divergence_factor`` — see arithmetic below
  - ``calibration_anchor.observed_value`` ← measured_value (if finding has anchor)
  - ``calibration_anchor.divergence_factor`` ← see arithmetic below

**Code-owned (auto, fill-absent-only):** the fields above.
**NEVER touch (authored):** ``statement``, ``summary``, ``explanation``,
``status``, ``kind``, ``expected.summary``, ``expert_reference``,
``calibration_anchor.literature_target``, ``next_action``.

A finding with no resolvable ``evidence.from_test`` → SKIP.  Never fabricate.

Uses ruamel comment-preserving round-trip (mirrors ``band_provenance.set_band_provenance``).
Idempotent: fills only absent slots; second call with same data → ``filled=0``, no write.

---

## Divergence factor arithmetic

With ``expected.range [low, high]``::

    measured inside [low, high]  → divergence_factor = 0.0
    measured < low               → (low  - measured) / low   (guard: low==0 → abs(measured - low))
    measured > high              → (measured - high) / high  (guard: high==0 → abs(measured - high))

With ``calibration_anchor.literature_target`` (L)::

    divergence_factor = (measured - L) / L    (signed; guard: L==0 → None, omit field)

With ``expected.threshold`` (T) only::

    divergence_factor = (measured - T) / T    (signed; guard: T==0 → None, omit field)

Priority: range > calibration_anchor > threshold (first applicable wins).
"""
from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

from . import study_io
from .band_provenance import _band_from_pass_if
from .study_outcomes import canonical_run


# Fields that must NEVER be touched (authored prose + semantics).
_AUTHORED_FINDING_FIELDS: frozenset[str] = frozenset({
    "statement",
    "summary",
    "explanation",
    "status",
    "kind",
    "next_action",
    "expert_reference",
    # id is structural, also never touch
    "id",
})
_AUTHORED_EXPECTED_FIELDS: frozenset[str] = frozenset({"summary"})
_AUTHORED_ANCHOR_FIELDS: frozenset[str] = frozenset({"literature_target"})


# ---------------------------------------------------------------------------
# Units resolution helpers
# ---------------------------------------------------------------------------

def _readout_units_for_test(test: dict, readouts: list[dict]) -> str | None:
    """Try to find the units for a test by matching readouts.

    Strategy (in order):
    1. Readout whose ``identifier`` contains the test's ``measure.path`` token.
    2. Readout whose ``name`` matches the test's ``name``.
    3. None (units omitted — never fabricate).
    """
    test_name = (test.get("name") or "").lower()
    measure_path: str = ""
    measure = test.get("measure")
    if isinstance(measure, dict):
        measure_path = (measure.get("path") or "").strip().lower()

    for ro in readouts:
        if not isinstance(ro, dict):
            continue
        identifier = (ro.get("identifier") or "").lower()
        ro_name = (ro.get("name") or "").lower()
        units = ro.get("units")

        # Match by measure path appearing in identifier
        if measure_path and measure_path in identifier:
            return units

        # Match by test name appearing in readout name or vice versa
        if test_name and (test_name in ro_name or ro_name in test_name):
            return units

    return None


# ---------------------------------------------------------------------------
# Divergence arithmetic
# ---------------------------------------------------------------------------

def _divergence_factor(
    measured: float,
    band: dict | None,
    literature_target: float | None,
) -> float | None:
    """Compute the divergence_factor for a finding.

    Priority: range band > calibration_anchor.literature_target > threshold.

    Returns None when no arithmetic is applicable, a div-by-zero guard fires,
    or ``measured`` is not a plain scalar. Per-generation test ops
    (``in_range_every_generation``, ``generation_average_in_range``, see
    ``study_evaluator.py``) store ``measured_value`` as a ``{generation: value}``
    dict rather than a single number — divergence arithmetic against a single
    band/target is undefined for that shape, so it is skipped (no field
    written) rather than raising.
    """
    if not isinstance(measured, (int, float)) or isinstance(measured, bool):
        return None

    low = band.get("low") if isinstance(band, dict) else None
    high = band.get("high") if isinstance(band, dict) else None
    threshold = band.get("threshold") if isinstance(band, dict) else None

    # 1. Range band: [low, high]
    if low is not None and high is not None:
        if low <= measured <= high:
            return 0.0
        if measured < low:
            if low == 0:
                return abs(measured - low)
            return (low - measured) / low
        # measured > high
        if high == 0:
            return abs(measured - high)
        return (measured - high) / high

    # 2. calibration_anchor.literature_target
    if literature_target is not None:
        if literature_target == 0:
            return None  # guard div-by-zero
        return (measured - literature_target) / literature_target

    # 3. Threshold only
    if threshold is not None:
        if threshold == 0:
            return None
        return (measured - threshold) / threshold

    return None


# ---------------------------------------------------------------------------
# Test index builder
# ---------------------------------------------------------------------------

def _build_test_index(spec: dict) -> dict[str, dict]:
    """Return name → test dict for all entries in behavior_tests[] and tests[]."""
    index: dict[str, dict] = {}
    for section in ("behavior_tests", "tests"):
        for test in (spec.get(section) or []):
            if isinstance(test, dict) and test.get("name"):
                index[str(test["name"])] = test
    return index


# ---------------------------------------------------------------------------
# Core: fill one finding in a ruamel CommentedMap
# ---------------------------------------------------------------------------

def _fill_finding(
    rt_finding: Any,
    test: dict,
    measured_value: Any,
    run_name: str,
    readouts: list[dict],
) -> bool:
    """Fill absent code-owned slots on a ruamel CommentedMap finding.

    Returns True if any field was changed.
    """
    changed = False

    # --- evidence sub-map ------------------------------------------------
    if "evidence" not in rt_finding:
        from ruamel.yaml import CommentedMap
        rt_finding["evidence"] = CommentedMap()
    ev = rt_finding["evidence"]

    # evidence.observed (fill-absent-only)
    if "observed" not in ev and measured_value is not None:
        ev["observed"] = measured_value
        changed = True

    # evidence.units (fill-absent-only)
    if "units" not in ev:
        units = _readout_units_for_test(test, readouts)
        if units is not None:
            ev["units"] = units
            changed = True

    # evidence.divergence_factor (fill-absent-only)
    if "divergence_factor" not in ev and measured_value is not None:
        band = _band_from_pass_if(test.get("pass_if"))
        # Get calibration anchor literature_target from the FINDING (not the test)
        anchor_in_finding = rt_finding.get("calibration_anchor")
        lit_target = None
        if isinstance(anchor_in_finding, dict):
            lit_target = anchor_in_finding.get("literature_target")
        # Fallback: test-level calibration_anchor
        if lit_target is None:
            test_anchor = test.get("calibration_anchor")
            if isinstance(test_anchor, dict):
                lit_target = test_anchor.get("literature_target")
        df = _divergence_factor(measured_value, band, lit_target)
        if df is not None:
            ev["divergence_factor"] = round(df, 8)
            changed = True

    # --- expected sub-map ------------------------------------------------
    if "expected" not in rt_finding:
        from ruamel.yaml import CommentedMap
        rt_finding["expected"] = CommentedMap()
    ex = rt_finding["expected"]

    # expected.range (fill-absent-only, from pass_if)
    if "range" not in ex:
        band = _band_from_pass_if(test.get("pass_if"))
        if band and "low" in band and "high" in band:
            ex["range"] = [band["low"], band["high"]]
            changed = True

    # expected.threshold (fill-absent-only)
    if "threshold" not in ex:
        band = _band_from_pass_if(test.get("pass_if"))
        if band and "threshold" in band and "low" not in band:
            ex["threshold"] = band["threshold"]
            changed = True

    # expected.cites (fill-absent-only)
    if "cites" not in ex:
        test_cites = test.get("cites")
        if test_cites:
            ex["cites"] = list(test_cites)
            changed = True

    # --- provenance sub-map ----------------------------------------------
    if "provenance" not in rt_finding:
        from ruamel.yaml import CommentedMap
        rt_finding["provenance"] = CommentedMap()
    prov = rt_finding["provenance"]

    # provenance.run_ids (fill-absent-only)
    if "run_ids" not in prov:
        prov["run_ids"] = [run_name]
        changed = True

    # --- calibration_anchor (measured side only) -------------------------
    if "calibration_anchor" in rt_finding:
        anchor = rt_finding["calibration_anchor"]
        if isinstance(anchor, dict) and measured_value is not None:
            lit_target = anchor.get("literature_target")
            if lit_target is not None:
                if "observed_value" not in anchor:
                    anchor["observed_value"] = measured_value
                    changed = True
                if "divergence_factor" not in anchor:
                    df = _divergence_factor(measured_value, None, lit_target)
                    if df is not None:
                        anchor["divergence_factor"] = round(df, 8)
                        changed = True

    return changed


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def populate_finding_observations(study_dir: Path | str) -> dict:
    """Fill absent code-owned finding slots from computed_outcomes + band + readouts.

    Parameters
    ----------
    study_dir:
        Directory containing ``study.yaml``.

    Returns
    -------
    dict
        ``{"filled": N, "skipped": N}`` where *filled* is the number of
        findings that received at least one new code-owned field, and *skipped*
        is the number of findings that had no resolvable ``evidence.from_test``
        link or no ``measured_value`` available.
    """
    from ruamel.yaml import YAML

    study_dir = Path(study_dir)
    study_yaml = study_dir / "study.yaml"

    # Plain load for logical lookups (test index, canonical_run)
    spec = study_io.load_yaml_mapping(study_yaml)

    findings = spec.get("findings") or []
    if not findings:
        return {"filled": 0, "skipped": 0}

    # Build test index and readouts list
    test_index = _build_test_index(spec)
    readouts = spec.get("readouts") or []

    # Find the canonical run and its computed_outcomes
    run = canonical_run(spec)
    if run is None:
        return {"filled": 0, "skipped": len(findings)}
    run_name = run.get("name") or ""
    computed: dict = run.get("computed_outcomes") or {}
    # Skip stub computed_outcomes (store_unresolved etc.)
    if isinstance(computed, dict) and "_status" in computed:
        computed = {}

    # ruamel round-trip load for comment-preserving writes
    ryaml = YAML()
    ryaml.preserve_quotes = True
    ryaml.width = 4096

    original_text = study_yaml.read_text(encoding="utf-8")
    rt_spec = ryaml.load(original_text)
    if rt_spec is None:
        return {"filled": 0, "skipped": 0}

    rt_findings = rt_spec.get("findings") or []

    filled = 0
    skipped = 0

    for rt_finding in rt_findings:
        if not isinstance(rt_finding, dict):
            skipped += 1
            continue

        # Resolve from_test link
        ev = rt_finding.get("evidence")
        from_test: str | None = None
        if isinstance(ev, dict):
            ft = ev.get("from_test")
            if isinstance(ft, str):
                from_test = ft

        if from_test is None:
            skipped += 1
            continue

        test = test_index.get(from_test)
        if test is None:
            # Unknown test name — never fabricate
            skipped += 1
            continue

        # Get measured_value from computed_outcomes
        co_entry = computed.get(from_test)
        measured_value: Any = None
        if isinstance(co_entry, dict):
            measured_value = co_entry.get("measured_value")

        if measured_value is None:
            # Can't fill without a measured value
            skipped += 1
            continue

        changed = _fill_finding(rt_finding, test, measured_value, run_name, readouts)
        if changed:
            filled += 1

    # Write only when something changed
    if filled > 0:
        buf = StringIO()
        ryaml.dump(rt_spec, buf)
        study_io.atomic_write(study_yaml, buf.getvalue())

    return {"filled": filled, "skipped": skipped}


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None) -> int:
    import argparse
    from .workspace_paths import WorkspacePaths

    ap = argparse.ArgumentParser(
        description=(
            "Populate finding evidence/expected/provenance slots from "
            "computed_outcomes (spine stage #5)."
        )
    )
    ap.add_argument("--workspace", default=".")
    grp = ap.add_mutually_exclusive_group(required=True)
    grp.add_argument("--study", help="study slug")
    grp.add_argument("--all", action="store_true", help="every study in the workspace")
    args = ap.parse_args(argv)

    paths = WorkspacePaths.load(Path(args.workspace))
    if args.all:
        dirs = list(paths.iter_study_dirs())
    else:
        dirs = [paths.study_dir(args.study, must_exist=True)]

    total = {"filled": 0, "skipped": 0}
    for d in dirs:
        r = populate_finding_observations(d)
        total["filled"] += r["filled"]
        total["skipped"] += r["skipped"]
        print(f"{d.name}: filled={r['filled']} skipped={r['skipped']}")
    print(f"TOTAL filled={total['filled']} skipped={total['skipped']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
