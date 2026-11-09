"""Measure-kind helpers used by the evaluation orchestration.

Cross-run (``run_delta``) measures and bare-string composite-observable
measures. The ``evaluate_study`` / ``evaluate_test`` orchestration and
``required_observables_for_measure`` live in the package ``__init__`` so that
tests which monkeypatch ``discovery_engine.study.evaluator.<helper>`` are honored
(the functions read those names as package globals, exactly as the former flat
module did).
"""
from __future__ import annotations

from typing import Any

import polars as pl

from .common import ObservableNotFound, _agent, _needs_rerun, _op_supported
from .grading import _scalar_compare
from .resolver import _resolve_series


# ---------------------------------------------------------------------------
# Cross-run measures (issue #98): compare a readout across two runs.
# ---------------------------------------------------------------------------

def _series_time_value(df: "pl.DataFrame"):
    """(abs_time[], value[]) float arrays from a resolved series, NaN-dropped."""
    import numpy as np
    t = df["abs_time"].cast(pl.Float64).to_numpy()
    v = df["value"].cast(pl.Float64).to_numpy()
    mask = ~(np.isnan(t) | np.isnan(v))
    return t[mask], v[mask]


def _run_delta(primary_df, compare_df, align: str, metric: str) -> float | None:
    """Scalar distance between the same readout on two runs.

    ``align='time'`` interpolates both onto the primary's abs_time grid within
    their overlap; ``align='index'`` pairs by position. ``metric`` reduces the
    per-point diff (``max_abs_diff`` | ``mean_abs_diff`` | ``final_abs_diff`` |
    ``rmse``). Returns None on empty / non-overlapping series.
    """
    import numpy as np
    tp, vp = _series_time_value(primary_df)
    tc, vc = _series_time_value(compare_df)
    if len(tp) == 0 or len(tc) == 0:
        return None
    if align == "time":
        lo, hi = max(tp.min(), tc.min()), min(tp.max(), tc.max())
        grid = tp[(tp >= lo) & (tp <= hi)]
        if len(grid) == 0:
            return None
        a, b = np.interp(grid, tp, vp), np.interp(grid, tc, vc)
    elif align in (None, "index"):
        n = min(len(vp), len(vc))
        if n == 0:
            return None
        a, b = vp[:n], vc[:n]
    else:
        raise ValueError(f"unknown align: {align!r}")
    d = np.abs(a - b)
    if metric == "max_abs_diff":
        return float(d.max())
    if metric == "mean_abs_diff":
        return float(d.mean())
    if metric == "final_abs_diff":
        return float(abs(a[-1] - b[-1]))
    if metric == "rmse":
        return float(np.sqrt(np.mean((a - b) ** 2)))
    raise ValueError(f"unknown metric: {metric!r}")


def _evaluate_run_delta(test: dict, reader, run_opener) -> dict:
    """Evaluate a ``kind: run_delta`` cross-run measure (#98).

    Applies the inner ``of`` readout to two runs — the primary (``given.run``,
    defaulting to the passed ``reader``) and the compare run (``given.compare_to``,
    resolved via ``run_opener``) — aligns them, reduces to a scalar distance, and
    applies the ``pass_if`` comparator.
    """
    measure = test.get("measure") or {}
    pass_if = test.get("pass_if")
    if not pass_if:
        return _agent("missing pass_if block")
    op = pass_if.get("op", "")
    if not _op_supported(op):
        return _agent(f"unsupported op: {op!r}")
    of = measure.get("of") or {}
    inner = (of.get("readout") or of.get("path") or of.get("field") or "").strip()
    if not inner:
        return _agent("run_delta measure needs `of.readout`/`of.path`")
    given = test.get("given") or {}
    compare_sel = given.get("compare_to")
    if not compare_sel:
        return _agent("run_delta requires given.compare_to")
    if run_opener is None:
        return _needs_rerun("run_delta needs a run opener (cross-run unavailable here)")
    compare_reader = run_opener(compare_sel)
    if compare_reader is None:
        return _agent(f"compare run not resolvable: {compare_sel!r}")
    primary_reader = reader
    primary_sel = {k: v for k, v in given.items() if k != "compare_to"}
    if primary_sel:
        pr = run_opener(primary_sel)
        if pr is not None:
            primary_reader = pr
    if primary_reader is None:
        return _needs_rerun("run_delta: no primary run")
    try:
        pdf = _resolve_series(inner, primary_reader)
        cdf = _resolve_series(inner, compare_reader)
    except ObservableNotFound as exc:
        return _agent(str(exc))
    except Exception as exc:  # noqa: BLE001
        return _agent(f"run_delta series error: {exc}")
    try:
        delta = _run_delta(pdf, cdf, measure.get("align", "time"),
                           measure.get("metric", "max_abs_diff"))
    except ValueError as exc:
        return _agent(str(exc))
    if delta is None:
        return _needs_rerun("run_delta: empty or non-overlapping series")
    return _scalar_compare(op, delta, pass_if, f"run_delta/{op}")


# ---------------------------------------------------------------------------
# Bare-string composite-observable measures (M-track / workbench convention)
# ---------------------------------------------------------------------------

def _study_composite_id(spec: dict) -> str | None:
    """The study's baseline composite id — v4 ``conditions.baseline.composite`` first, else legacy
    ``baseline[0].composite``, else the first arm's. The ``name(rows)->float`` functions a bare-string
    measure names live on THIS composite's module."""
    cond = (spec.get("conditions") or {}).get("baseline") or {}
    if cond.get("composite"):
        return str(cond["composite"])
    for arm in (spec.get("baseline") or spec.get("arms") or []):
        if isinstance(arm, dict) and arm.get("composite"):
            return str(arm["composite"])
    return None


def _import_composite_module(composite_id: str | None, ws_root: Any):
    """Import the module that holds a composite's observable functions (bare-string measures).

    A composite id is ``<module.path>.<generator_fn>``; the ``name(rows)`` functions live on the
    module. Ensures ``ws_root`` is importable (same mechanism as the workspace evaluators). Returns
    the module or ``None`` — never raises (a bad import degrades the measure to ungraded)."""
    if not composite_id:
        return None
    import importlib
    import sys
    from pathlib import Path
    if ws_root is not None:
        key = str(Path(ws_root).resolve())
        if key not in sys.path:
            sys.path.insert(0, key)
    for candidate in (composite_id.rsplit(".", 1)[0], composite_id):
        try:
            return importlib.import_module(candidate)
        except Exception:  # noqa: BLE001
            continue
    return None


def _grade_bare_string(measure_name: str, test: dict, reader, composite_module) -> dict:
    """Grade a bare-STRING measure (a composite-observable function ``name(rows)->float``, the
    M-track/workbench convention): resolve the function on the study's composite module, compute the
    scalar from the run trajectory via the SHARED resolver (viva_emitters.measure_resolver, so the
    engine grades byte-identically to the workbench), and apply the test's pass_if. Degrades to the
    agent/needs_rerun bucket — never crashes — when the module, reader, or resolver is unavailable."""
    if composite_module is None:
        return _agent(f"bare-string measure {measure_name!r}: study composite module not resolved")
    if reader is None:
        return _needs_rerun(f"bare-string measure {measure_name!r}: no run reader")
    try:
        from viva_emitters.measure_resolver import grade_bare_string_measure
    except ImportError:
        return _agent(f"bare-string measure {measure_name!r}: viva_emitters.measure_resolver "
                      "unavailable (needs viva-emitters with RunReader.rows)")
    pass_if = test.get("pass_if")
    if not isinstance(pass_if, dict):
        return _agent("missing pass_if block")
    op = pass_if.get("op", "")
    if not _op_supported(op):
        return _agent(f"unsupported op: {op!r}")
    try:
        value = float(grade_bare_string_measure(reader, composite_module, measure_name))
    except Exception as exc:  # noqa: BLE001
        return _agent(f"bare-string measure {measure_name!r} error: {exc}")
    return _scalar_compare(op, value, pass_if, measure_name)
