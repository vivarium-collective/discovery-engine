"""Grading: pass_if → test_contract.Expected mapping, the /v2 axis builder,
scalar comparison, and the windowed-data reduction engine (``_apply_op``).
"""
from __future__ import annotations

from typing import Any

import polars as pl

from discovery_engine.reporting.test_contract import Expected, band, check, predicate, value
from discovery_engine.reporting.test_vocab import RANK

from .common import (
    _agent,
    _as_float,
    _code_outcome,
    _equals_ok,
    _needs_rerun,
    _sig6,
)
from .config import _config_lookup, _resolve_expected


# ---------------------------------------------------------------------------
# pass_if -> test_contract.Expected (the unified grading vocabulary)
# ---------------------------------------------------------------------------

def _num(pass_if: dict, *keys):
    """First present numeric among keys, or None."""
    for k in keys:
        v = pass_if.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    return None


def _expected_from_pass_if(pass_if: dict, op: str) -> Expected | None:
    """Map a pass_if block to a test_contract.Expected, or None when the op has
    no scalar expectation this slice grades (it then yields no /v2 axis).

    Pure synonyms are folded in: at_most->'<=', at_least->'>=', and the
    ``operator: greater-than/less-than`` spelling. Aliases that need a new
    *measure* (e.g. ratio_at_most) are intentionally unmapped -> None."""
    o = (op or "").strip()
    # band
    if o in ("range", "in_range", "in_range_every_generation", "generation_average_in_range"):
        lo, hi = _num(pass_if, "low"), _num(pass_if, "high")
        return band(lo, hi) if lo is not None and hi is not None else None
    # comparators (+ extrema + pure synonyms + operator spelling)
    _LE = {"<=", "max_le", "at_most", "less-than-or-equal"}
    _LT = {"<", "max_lt", "less-than"}
    _GE = {">=", "min_ge", "at_least", "greater-than-or-equal", "greater-than"}
    _GT = {">", "min_gt"}
    spelled = pass_if.get("operator")
    if o in _LE or spelled in _LE:
        t = _num(pass_if, "value", "threshold")
        return value(t, op="<=") if t is not None else None
    if o in _LT or spelled in _LT:
        t = _num(pass_if, "value", "threshold")
        return value(t, op="<") if t is not None else None
    if o in _GE or spelled in _GE:
        t = _num(pass_if, "value", "threshold")
        return value(t, op=">=") if t is not None else None
    if o in _GT or spelled in _GT:
        t = _num(pass_if, "value", "threshold")
        return value(t, op=">") if t is not None else None
    # approx / tolerance
    if o in ("==", "eq", "equals"):
        t = _num(pass_if, "value", "target")
        if t is None:
            return None
        # Mirror _apply_op's _equals_ok precedence: tolerance_fraction (relative)
        # wins over tolerance (absolute) when both are present.
        tolf = _num(pass_if, "tolerance_fraction")
        if tolf is not None:
            return value(t, op="~=", tol=tolf)
        tol = _num(pass_if, "tolerance")
        if tol is not None:
            return band(t - tol, t + tol)
        return value(t, op="==")
    if o == "median_within_tolerance":
        t, tol = _num(pass_if, "target", "value"), _num(pass_if, "tolerance_fraction", "tolerance")
        return value(t, op="~=", tol=tol if tol is not None else 0.05) if t is not None else None
    if o == "periodic_doubling_every_generation":
        tol = _num(pass_if, "tolerance")
        return value(2.0, op="~=", tol=tol if tol is not None else 0.2)
    if o == "cv_below":
        t = _num(pass_if, "cv_threshold", "value")
        return value(t, op="<=") if t is not None else None
    # categorical -> predicate (verdict only, no numeric margin)
    if o in ("in_set", "!=", "exactly_one_initiation_per_generation"):
        return predicate(o)
    return None


def _grade_axis_from_outcome(test: dict, pass_if: dict, op: str, outcome: dict) -> dict | None:
    """Build a report_card_verdict/v2 axis from a code outcome by grading its
    measured_value through test_contract.check. Returns None when the op has no
    scalar expectation (Task 2) — the outcome then simply carries no axis."""
    expected = _expected_from_pass_if(pass_if, op)
    if expected is None:
        return None
    name = test.get("name", "test")
    label = test.get("description") or name
    cites = test.get("cites") or []
    cite = "; ".join(str(c) for c in cites) or None
    units = (test.get("measure") or {}).get("units")
    common = dict(severity=test.get("severity", "hard"), cite=cite, units=units)
    mv = outcome.get("measured_value")

    if expected.kind == "predicate":
        # categorical: verdict comes from the code result, no numeric margin.
        v = "within_tol" if outcome.get("result") == "PASS" else "mismatch"
        return check(name, label, mv, expected, verdict=v, **common)

    if isinstance(mv, dict):
        # per-generation: grade each generation, keep the worst.
        graded = [(g, check(name, label, val, expected, **common))
                  for g, val in mv.items() if isinstance(val, (int, float))]
        if not graded:
            return None

        def _severity_key(ga):
            ax = ga[1]
            m = ax.get("margin")
            return (RANK.get(ax.get("verdict", "ungraded"), 0), -(m if m is not None else 0.0))

        g_worst, ax = max(graded, key=_severity_key)
        ax = dict(ax)
        ax["detail"] = {"per_generation": mv, "worst_generation": g_worst}
        return ax

    if isinstance(mv, (int, float)):
        return check(name, label, mv, expected, **common)
    return None


# ---------------------------------------------------------------------------
# Scalar comparison (shared by cross-run and bare-string measures)
# ---------------------------------------------------------------------------

def _scalar_compare(op: str, measured: float, pass_if: dict, label: str) -> dict:
    """Apply a scalar pass_if op to a pre-computed measured value."""
    if op in ("==", "eq", "equals"):
        ok = _equals_ok(measured, pass_if.get("value"), pass_if)
        return _code_outcome("PASS" if ok else "FAIL", _sig6(measured), label,
                             f"{measured:.4g} == {pass_if.get('value')}")
    if op in ("range", "in_range"):
        low, high = float(pass_if["low"]), float(pass_if["high"])
        ok = low <= measured <= high
        return _code_outcome("PASS" if ok else "FAIL", _sig6(measured), label,
                             f"{measured:.4g} in [{low}, {high}]")
    cmps = {"<=": (lambda a, b: a <= b), ">=": (lambda a, b: a >= b),
            "<": (lambda a, b: a < b), ">": (lambda a, b: a > b),
            "!=": (lambda a, b: a != b)}
    if op in cmps:
        target = float(pass_if.get("value", pass_if.get("threshold", 0)))
        ok = cmps[op](measured, target)
        return _code_outcome("PASS" if ok else "FAIL", _sig6(measured), label,
                             f"{measured:.4g} {op} {target}")
    return _agent(f"run_delta: unsupported op {op!r}")


# ---------------------------------------------------------------------------
# Windowed-data reduction helpers
# ---------------------------------------------------------------------------

def _clean_floats(s: "pl.Series") -> list[float]:
    """Cast to float and drop nulls + NaN (NaN = undefined tick, e.g. a 0/0
    ratio at cell birth — it must NOT poison a mean/comparison reduction;
    polars' .mean() skips nulls but propagates float-NaN)."""
    s = s.cast(pl.Float64)
    return s.drop_nulls().drop_nans().to_list()


def _complete_generations(gen_counts: dict[int, int]) -> set[int]:
    """Return the generations to keep for a per-generation reduction, dropping
    INCOMPLETE generations (e.g. a truncated final generation, or a 1-tick
    birth-stub) whose tick count is < 50% of the median generation length.

    A partial generation is not a complete cell cycle, so it must not drag a
    per-generation steady-state average out of band. With <3 generations there
    is nothing reliable to compare against, so all are kept.
    """
    if len(gen_counts) < 3:
        return set(gen_counts)
    import statistics
    med = statistics.median(gen_counts.values())
    if med <= 0:
        return set(gen_counts)
    return {g for g, n in gen_counts.items() if n >= 0.5 * med}


def _flat_values(data: Any, window_kind: str) -> list[float]:
    """Extract a flat list of float values from a windowed result (NaN/null-free)."""
    if window_kind == "flat":
        return _clean_floats(data["value"])
    if window_kind == "per_gen_scalar":
        return [v for v in data.values() if v is not None and v == v]  # nan != nan
    if window_kind == "per_gen_all":
        vals: list[float] = []
        for df in data.values():
            vals.extend(_clean_floats(df["value"]))
        return vals
    return []


def _apply_op(windowed: tuple, pass_if: dict, kind: str, op: str, config=None) -> dict:
    """Apply a pass_if predicate to windowed data.

    Returns a code outcome dict or an agent/needs_rerun dict.

    Note: reduction logic is keyed by *op*, not *kind*.  The measure ``kind``
    field is used upstream (RUN_DATA_KINDS gate) to decide whether the
    observable is run-data-evaluable at all, but it does NOT select the
    reduction branch here.  Per-generation ops (e.g. ``in_range_every_generation``)
    are what trigger per-gen reduction — so a future reader must not assume
    ``kind`` drives the branching below.
    """
    window_kind, data = windowed
    label = f"{kind}/{op}"

    # -- range / in_range --
    if op in ("range", "in_range"):
        vals = _flat_values(data, window_kind)
        measured = float(pl.Series(vals, dtype=pl.Float64).mean())
        low = float(pass_if["low"])
        high = float(pass_if["high"])
        ok = low <= measured <= high
        return _code_outcome(
            result="PASS" if ok else "FAIL",
            measured_value=_sig6(measured),
            operator=label,
            detail=(f"{measured:.4g} in [{low}, {high}]" if ok
                    else f"{measured:.4g} not in [{low}, {high}]"),
        )

    # -- in_range_every_generation / generation_average_in_range --
    if op in ("in_range_every_generation", "generation_average_in_range"):
        low = float(pass_if["low"])
        high = float(pass_if["high"])

        if window_kind == "per_gen_scalar":
            gen_vals: dict[int, float] = data
        elif window_kind == "per_gen_all":
            counts = {int(g): df.height for g, df in data.items()}
            keep = _complete_generations(counts)
            gen_vals = {int(g): float(pl.Series(_clean_floats(df["value"])).mean())
                        for g, df in data.items() if int(g) in keep}
        elif window_kind == "flat":
            counts = {int(g): data.filter(pl.col("generation") == g).height
                      for g in data["generation"].unique().to_list()}
            keep = _complete_generations(counts)
            gen_vals = {}
            for g in sorted(keep):
                sub = data.filter(pl.col("generation") == g)
                gen_vals[int(g)] = float(pl.Series(_clean_floats(sub["value"])).mean())
        else:
            return _agent(f"unexpected window kind for {op}: {window_kind!r}")

        if not gen_vals:
            return _needs_rerun("no generation data for in_range_every_generation")

        per_gen: dict[int, tuple[bool, float]] = {
            g: (low <= v <= high, v) for g, v in gen_vals.items()
        }
        all_pass = all(ok for ok, _ in per_gen.values())
        failing = [(g, round(v, 3)) for g, (ok, v) in per_gen.items() if not ok]

        return _code_outcome(
            result="PASS" if all_pass else "FAIL",
            measured_value={g: round(v, 4) for g, (_, v) in per_gen.items()},
            operator=label,
            detail=(f"all {len(gen_vals)} gens in [{low}, {high}]" if all_pass
                    else f"gens out of [{low}, {high}]: {failing}"),
        )

    # -- scalar comparators (incl. equals with tolerance + config: refs, #98) --
    if op in ("<=", ">=", "<", ">", "==", "!=", "eq", "equals"):
        vals = _flat_values(data, window_kind)
        measured = float(pl.Series(vals, dtype=pl.Float64).mean())
        # target: a `config:` reference into the run's declared params wins over
        # a literal `value:` — lets an observable be asserted equal to the
        # CONFIGURED value ("emitted coupling interval == configured value").
        target = _as_float(_resolve_expected(pass_if, config))
        if target is None:
            target = float(pass_if.get("value", 0))
        if op in ("==", "eq", "equals"):
            ok = _equals_ok(measured, target, pass_if)
            effective_op = "=="
        else:
            effective_op = op
            comparators = {
                "<=": lambda a, b: a <= b,
                ">=": lambda a, b: a >= b,
                "<":  lambda a, b: a < b,
                ">":  lambda a, b: a > b,
                "!=": lambda a, b: a != b,
            }
            ok = comparators[effective_op](measured, target)
        return _code_outcome(
            result="PASS" if ok else "FAIL",
            measured_value=_sig6(measured),
            operator=label,
            detail=f"{measured:.4g} {effective_op} {target}",
        )

    # -- in_set (set may be a literal `set:` or a `config:`-referenced list) --
    if op == "in_set":
        vals = _flat_values(data, window_kind)
        measured = float(pl.Series(vals, dtype=pl.Float64).mean())
        target_set = set(pass_if.get("set", []))
        if "config" in pass_if:
            cv = _config_lookup(config or {}, str(pass_if["config"]))
            if isinstance(cv, (list, tuple, set)):
                target_set = set(cv)
        ok = measured in target_set
        return _code_outcome(
            result="PASS" if ok else "FAIL",
            measured_value=_sig6(measured),
            operator=label,
            detail=(f"{measured} in {target_set}" if ok
                    else f"{measured} not in {target_set}"),
        )

    # -- cv_below --
    if op == "cv_below":
        vals = _flat_values(data, window_kind)
        if len(vals) < 2:
            return _needs_rerun("too few data points to compute CV")
        s = pl.Series(vals, dtype=pl.Float64)
        mean_val = float(s.mean())
        if mean_val == 0.0:
            return _agent("cannot compute CV: mean is zero")
        std_val = float(s.std())
        cv = std_val / abs(mean_val)
        threshold = float(pass_if["cv_threshold"])
        ok = cv < threshold
        return _code_outcome(
            result="PASS" if ok else "FAIL",
            measured_value=round(cv, 6),
            operator=label,
            detail=f"CV={cv:.4f} {'<' if ok else '>='} {threshold}",
        )

    # -- median_within_tolerance --
    if op == "median_within_tolerance":
        vals = _flat_values(data, window_kind)
        if not vals:
            return _needs_rerun("empty series for median_within_tolerance")
        median_val = float(pl.Series(vals, dtype=pl.Float64).median())
        target = float(pass_if["target"])
        tol = float(pass_if["tolerance_fraction"])
        if target == 0.0:
            return _agent("cannot compute median_within_tolerance: target is zero")
        rel_err = abs(median_val - target) / abs(target)
        ok = rel_err <= tol
        return _code_outcome(
            result="PASS" if ok else "FAIL",
            measured_value=round(median_val, 6),
            operator=label,
            detail=(f"median={median_val:.4g}, "
                    f"|{median_val:.4g}-{target}|/|{target}|={rel_err:.4f} "
                    f"{'<=' if ok else '>'} {tol}"),
        )

    # -- periodic_doubling_every_generation --
    if op == "periodic_doubling_every_generation":
        tol = float(pass_if.get("tolerance", 0.2))

        if window_kind == "per_gen_all":
            gen_data: dict[int, pl.DataFrame] = data
        elif window_kind == "flat":
            try:
                from viva_emitters import by_generation  # noqa: PLC0415
            except ImportError as _ie:
                raise ImportError(
                    "by_generation requires the parquet reader: "
                    "pip install 'viva-emitters[parquet]'"
                ) from _ie
            gen_data = by_generation(data)
        else:
            return _agent(f"periodic_doubling requires per-gen data, got: {window_kind!r}")

        if not gen_data:
            return _needs_rerun("no generation data for periodic_doubling")

        # Drop incomplete generations (e.g. a truncated final cycle) — they did
        # not complete a doubling and would spuriously fail the test.
        keep_pd = _complete_generations({int(g): df.height for g, df in gen_data.items()})

        per_gen_ratios: dict[int, float] = {}
        for g, df in gen_data.items():
            if int(g) not in keep_pd:
                continue
            arr = df["value"].cast(pl.Float64).to_numpy()
            if len(arr) < 2:
                continue
            min_v = float(arr.min())
            max_v = float(arr.max())
            if min_v <= 0.0:
                per_gen_ratios[g] = float("nan")
            else:
                per_gen_ratios[g] = max_v / min_v

        # Exclude NaN entries
        valid = {g: r for g, r in per_gen_ratios.items() if r == r}  # nan != nan
        if not valid:
            return _needs_rerun("insufficient per-gen data for periodic_doubling")

        per_gen_ok = {g: (abs(r - 2.0) <= tol * 2.0, r) for g, r in valid.items()}
        all_pass = all(ok for ok, _ in per_gen_ok.values())
        failing = [(g, round(r, 3)) for g, (ok, r) in per_gen_ok.items() if not ok]

        return _code_outcome(
            result="PASS" if all_pass else "FAIL",
            measured_value={g: round(r, 4) for g, (_, r) in per_gen_ok.items()},
            operator=label,
            detail=(f"all gens: doubling ratio within {tol * 2.0:.2f} of 2.0" if all_pass
                    else f"gens outside tolerance: {failing}"),
        )

    # -- exactly_one_initiation_per_generation --
    if op == "exactly_one_initiation_per_generation":
        if window_kind == "per_gen_all":
            gen_data_2: dict[int, pl.DataFrame] = data
        elif window_kind == "flat":
            try:
                from viva_emitters import by_generation  # noqa: PLC0415
            except ImportError as _ie:
                raise ImportError(
                    "by_generation requires the parquet reader: "
                    "pip install 'viva-emitters[parquet]'"
                ) from _ie
            gen_data_2 = by_generation(data)
        else:
            return _agent(f"exactly_one_initiation requires per-gen data, got: {window_kind!r}")

        if not gen_data_2:
            return _needs_rerun("no generation data for exactly_one_initiation")

        per_gen_counts = {g: float(df["value"].cast(pl.Float64).sum())
                          for g, df in gen_data_2.items()}
        per_gen_ok_2 = {g: (c == 1.0, c) for g, c in per_gen_counts.items()}
        all_pass_2 = all(ok for ok, _ in per_gen_ok_2.values())
        failing_2 = [(g, c) for g, (ok, c) in per_gen_ok_2.items() if not ok]

        return _code_outcome(
            result="PASS" if all_pass_2 else "FAIL",
            measured_value={g: c for g, (_, c) in per_gen_ok_2.items()},
            operator=label,
            detail=("all gens have exactly 1 initiation" if all_pass_2
                    else f"gens with != 1 initiation: {failing_2}"),
        )

    # -- extreme-value comparators (max_le / max_lt / min_ge / min_gt) --
    # Reduce by MAX or MIN over the window (not the mean), so a test can assert
    # "the value NEVER exceeds / drops below X" — e.g. oriC never re-initiates
    # (max number_of_oric <= 2). The mean-based ops can't express this.
    if op in ("max_le", "max_lt", "min_ge", "min_gt"):
        vals = _flat_values(data, window_kind)
        if not vals:
            return _needs_rerun(f"empty series for {op}")
        target = float(pass_if.get("value", pass_if.get("threshold", 0)))
        reducer, cmp = {
            "max_le": (max, lambda a, b: a <= b),
            "max_lt": (max, lambda a, b: a < b),
            "min_ge": (min, lambda a, b: a >= b),
            "min_gt": (min, lambda a, b: a > b),
        }[op]
        measured = float(reducer(vals))
        sym = {"max_le": "<=", "max_lt": "<", "min_ge": ">=", "min_gt": ">"}[op]
        red = "max" if op.startswith("max") else "min"
        ok = cmp(measured, target)
        return _code_outcome(
            result="PASS" if ok else "FAIL",
            measured_value=_sig6(measured),
            operator=label,
            detail=f"{red}={measured:.4g} {sym} {target}" + ("" if ok else " (violated)"),
        )

    # -- rises_within_cycle --
    # Per-generation trend test: the value is LOW early in the cycle and RISES to
    # a within-cycle PEAK (e.g. oriC-low box occupancy fills gradually toward
    # initiation, rather than being pre-filled at birth). The peak can occur
    # mid-cycle (at initiation) with a fall afterwards (de-occupancy), so the
    # comparison is early-third mean vs the cycle PEAK, not the late-third.
    # Passes when a majority of complete generations rise by at least `min_rise`.
    if op == "rises_within_cycle":
        if window_kind == "per_gen_all":
            gen_data_3: dict[int, pl.DataFrame] = data
        elif window_kind == "flat":
            try:
                from viva_emitters import by_generation  # noqa: PLC0415
            except ImportError as _ie:
                raise ImportError(
                    "by_generation requires the parquet reader: "
                    "pip install 'viva-emitters[parquet]'"
                ) from _ie
            gen_data_3 = by_generation(data)
        else:
            return _agent(f"rises_within_cycle requires per-gen data, got: {window_kind!r}")
        if not gen_data_3:
            return _needs_rerun("no generation data for rises_within_cycle")
        counts3 = {int(g): df.height for g, df in gen_data_3.items()}
        keep3 = _complete_generations(counts3)
        min_rise = float(pass_if.get("min_rise", 0.02))
        min_frac = float(pass_if.get("min_fraction", 0.6))
        per_gen_rise: dict[int, float] = {}
        for g, df in gen_data_3.items():
            if int(g) not in keep3:
                continue
            sub = df.sort("abs_time") if "abs_time" in df.columns else df
            arr = _clean_floats(sub["value"])
            n = len(arr)
            if n < 3:
                continue
            early = sum(arr[: n // 3]) / max(1, n // 3)
            peak = max(arr)   # the within-cycle maximum (rise-to-peak, may fall after)
            per_gen_rise[int(g)] = peak - early
        if not per_gen_rise:
            return _needs_rerun("insufficient per-gen data for rises_within_cycle")
        rising = {g: d for g, d in per_gen_rise.items() if d >= min_rise}
        frac = len(rising) / len(per_gen_rise)
        ok = frac >= min_frac
        return _code_outcome(
            result="PASS" if ok else "FAIL",
            measured_value={g: round(d, 4) for g, d in per_gen_rise.items()},
            operator=label,
            detail=(f"{len(rising)}/{len(per_gen_rise)} gens rise >= {min_rise} "
                    f"(late-third - early-third); fraction {frac:.2f} "
                    f"{'>=' if ok else '<'} {min_frac}"),
        )

    # Should never reach here — _op_supported guards above
    return _agent(f"unhandled op: {op!r}")
