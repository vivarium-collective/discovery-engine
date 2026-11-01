"""study_evaluator.py — pure run-data evaluator + computed-outcomes write-back (B2).

Evaluates study behavior_tests against a RunReader: closed measure/pass_if
DSL → per-test PASS/FAIL/PARTIAL + provenance.  B2b adds write-back of a
parallel ``computed_outcomes`` block per run — never touching ``outcomes``.

Public API:
    evaluate_study(spec, reader, ws_root=None) -> dict[str, dict]
    evaluate_test(test, reader, ws_root=None) -> dict
    load_workspace_evaluators(ws_root) -> dict[str, Callable]
    compute_outcomes(study_dir, ws_root=None) -> summary_dict
    _resolve_run_store(run, study_dir, ws_root=None) -> str | None

Outcome shapes:
    code path:  {"result": "PASS"|"FAIL", "measured_value": ...,
                 "evaluated_by": "code", "operator": "kind/op", "detail": "..."}
    agent:      {"evaluated_by": "agent", "reason": "..."}
    needs_rerun:{"evaluated_by": "needs_rerun", "reason": "..."}
"""
from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING, Any, Callable

import polars as pl

from discovery_engine import study_io
from discovery_engine.test_contract import Expected, band, check, predicate, value
from discovery_engine.test_vocab import RANK

if TYPE_CHECKING:
    from viva_emitters import RunReader


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
# Closed set: run-data-evaluable measure kinds
# ---------------------------------------------------------------------------

RUN_DATA_KINDS: frozenset[str] = frozenset({
    "range_check_per_generation",
    "generation_average",
    "derived",         # authored spelling in study.yaml; "derived_scalar" is the alias
    "derived_scalar",
    "per_generation_mass_ratio",
    "oric_initiations_per_generation",
    "rate_match",
    "snapshot_window",
    "count_over_lineage",
    "periodicity_check",
    "per_gen",
})


# ---------------------------------------------------------------------------
# Pluggable workspace evaluators (generic seam — framework ships none).
#
# A workspace augments evaluation by shipping a `register_evaluators(registry)`
# hook in its `pbg_<name>` package (the same package that hosts build_core()).
# When evaluate_test hits a measure.kind that is NOT a native RUN_DATA_KIND, it
# consults the workspace registry before falling back to the agent bucket.
# ---------------------------------------------------------------------------

_WS_EVALUATOR_CACHE: dict[str, dict[str, Callable]] = {}
_WS_DERIVED_SCALAR_CACHE: dict[str, dict[str, Callable]] = {}


def clear_workspace_evaluator_cache() -> None:
    """Drop the per-workspace evaluator + derived-scalar caches."""
    _WS_EVALUATOR_CACHE.clear()
    _WS_DERIVED_SCALAR_CACHE.clear()


def _workspace_package_slug(ws_root: Any) -> str:
    """Importable package name for the workspace, mirroring build_core()'s home.

    Canonical is ``viva_<name>`` (post-rebrand), but a not-yet-migrated workspace
    may still ship ``pbg_<name>/`` on disk. ``resolve_package_dir`` returns
    whichever exists (viva_ preferred, pbg_ recognized during the migration
    window), so the evaluators hook imports the real package. Derived from the
    workspace ``name`` (where build_core lives for a viva-template workspace).
    dashes -> underscores.
    """
    import yaml
    from pathlib import Path

    from viva_workspace import resolve_package_dir
    name = "workspace"
    wy = Path(ws_root) / "workspace.yaml"
    if wy.is_file():
        data = yaml.safe_load(wy.read_text(encoding="utf-8")) or {}
        name = data.get("name") or name
    return resolve_package_dir(ws_root, name).name


def _workspace_evaluator_packages(ws_root: Any) -> list[str]:
    """Importable package names that may host the workspace's ``evaluators`` hook.

    Two conventions exist and both must be tried:

    * the **name-derived slug** (``viva_<name>`` / ``pbg_<name>``) — where
      ``build_core`` lives for a viva-template workspace whose package differs
      from its simulation package (e.g. package_path = ``<pkg>`` but
      the workspace package is ``viva_<name>``), and
    * workspace.yaml **``package_path``** — the workspace's own package for the
      common case where the package IS the package_path (e.g. multiscale-BATS,
      package_path = ``multiscale_bats``). The name-derived slug there would be
      ``viva_multiscale_BATS``, which does not exist, so ``package_path`` is the
      only place the hook lives.

    Returned de-duplicated, name-slug first. Never raises.
    """
    import yaml
    from pathlib import Path

    cands: list[str] = []
    try:
        cands.append(_workspace_package_slug(ws_root))
    except Exception:  # noqa: BLE001
        pass
    try:
        wy = Path(ws_root) / "workspace.yaml"
        if wy.is_file():
            data = yaml.safe_load(wy.read_text(encoding="utf-8")) or {}
            pkg_path = data.get("package_path")
            if pkg_path:
                cands.append(str(pkg_path).replace("-", "_"))
    except Exception:  # noqa: BLE001
        pass
    seen: set[str] = set()
    out: list[str] = []
    for c in cands:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def load_workspace_evaluators(ws_root: Any) -> dict[str, Callable]:
    """Import the workspace's ``<pkg>.evaluators`` hook(s) and collect registrations.

    Tries each candidate package (name-derived slug AND workspace.yaml
    ``package_path`` — see :func:`_workspace_evaluator_packages`) so the hook is
    found whether the workspace package is ``viva_<name>`` or its own
    ``package_path``. Registrations from every hook found are merged.

    Returns a {measure_kind: callable} dict. Empty if ws_root is None or no hook
    is present. A hook that raises is skipped (a broken workspace hook must never
    crash evaluation — degrade to the agent bucket). Cached per ws_root.
    """
    import sys
    from pathlib import Path
    if ws_root is None:
        return {}
    key = str(Path(ws_root).resolve())
    if key in _WS_EVALUATOR_CACHE:
        return _WS_EVALUATOR_CACHE[key]
    registry: dict[str, Callable] = {}
    if key not in sys.path:
        sys.path.insert(0, key)
    for pkg in _workspace_evaluator_packages(ws_root):
        try:
            mod = __import__(f"{pkg}.evaluators", fromlist=["register_evaluators"])
            hook = getattr(mod, "register_evaluators", None)
            if callable(hook):
                hook(registry)
        except Exception:  # noqa: BLE001 — never let a workspace hook break evaluation
            continue
    _WS_EVALUATOR_CACHE[key] = registry
    return registry


# Bump when the grading LOGIC changes (not for data changes). Stamped into each run's
# computed_outcomes as ``_grader`` so a consumer (the investigation-graph card) can detect
# a stale computed gate and force a re-grade when the grader has moved on.
GRADER_VERSION = "2-storeless"


def storeless(fn: Callable) -> Callable:
    """Mark a workspace evaluator as STORE-LESS: it re-derives its result internally
    (e.g. a recovery evaluator that re-runs SINDy+fit) and needs no run store. Such an
    evaluator is graded by :func:`compute_outcomes` even when the run store is unresolved,
    instead of being skipped as ``store_unresolved``. Use as a decorator on the evaluator
    a workspace registers via ``register_evaluators``."""
    fn.storeless = True  # type: ignore[attr-defined]
    return fn


def _storeless_kinds(ws_root: Any) -> set[str]:
    """Measure kinds whose registered workspace evaluator is store-less (see :func:`storeless`)."""
    return {k for k, fn in load_workspace_evaluators(ws_root).items()
            if getattr(fn, "storeless", False)}


def _grader_stamp() -> dict:
    """The grader-identity stamp written into every computed_outcomes block. A consumer
    (the investigation-graph card) compares ``version`` against the current GRADER_VERSION
    and re-grades when it differs — so a changed grader forces a refresh rather than serving
    a stale computed gate."""
    return {"version": GRADER_VERSION}


def load_workspace_derived_scalars(ws_root: Any) -> dict[str, Callable]:
    """Import the workspace's ``<pkg>.evaluators`` hook and collect derived-scalar
    computers registered via ``register_derived_scalars(reg)``.

    Returns a ``{field_name: fn}`` dict where ``fn(reader, test, ws_root) -> float``.
    Empty if ws_root is None or no hook is present. A hook that raises is skipped
    (a broken workspace hook must never crash evaluation). Cached per ws_root.
    """
    import sys
    from pathlib import Path
    if ws_root is None:
        return {}
    key = str(Path(ws_root).resolve())
    if key in _WS_DERIVED_SCALAR_CACHE:
        return _WS_DERIVED_SCALAR_CACHE[key]
    registry: dict[str, Callable] = {}
    if key not in sys.path:
        sys.path.insert(0, key)
    for pkg in _workspace_evaluator_packages(ws_root):
        try:
            mod = __import__(f"{pkg}.evaluators", fromlist=["register_derived_scalars"])
            hook = getattr(mod, "register_derived_scalars", None)
            if callable(hook):
                hook(registry)
        except Exception:  # noqa: BLE001 — never let a workspace hook break evaluation
            continue
    _WS_DERIVED_SCALAR_CACHE[key] = registry
    return registry


def declares_sources(*observables: str) -> Callable:
    """Decorator for a derived-scalar computer: declare the emitted observables it reads.

    A derived scalar (e.g. ``log10_enrichment``) is computed at evaluation time from one or
    more run observables (e.g. ``enrichment_factor``). Declaring those sources lets a run be
    CHECKED and CONFIGURED to emit them --- the run-readiness contract maps a behavior-test's
    derived measure to the observables the run must emit, instead of the run emitting the
    (unproduced) derived-scalar name and recording an empty readout."""
    def _decorate(fn: Callable) -> Callable:
        fn.source_observables = tuple(observables)  # type: ignore[attr-defined]
        return fn
    return _decorate


def required_observables_for_measure(measure: Any, ws_root: Any = None) -> set:
    """The emitted observables a behavior-test ``measure`` needs present in the run store.

    - A direct-observable measure needs its ``path``.
    - A ``derived``/``derived_scalar`` measure needs the SOURCE observables its registered
      computer declares (via :func:`declares_sources`), falling back to the path itself when
      none are declared.
    - ``config_value`` reads declared params, not the store, so it needs none.
    Cross-run (``run_delta``) and path-less measures contribute nothing here."""
    if not isinstance(measure, dict):
        return set()
    kind, path = measure.get("kind"), measure.get("path")
    if kind in ("config_value", "run_delta") or not path:
        return set()
    if kind in RUN_DATA_KINDS:
        fn = load_workspace_derived_scalars(ws_root).get(path)
        srcs = getattr(fn, "source_observables", ()) if fn is not None else ()
        return set(srcs) if srcs else {path}
    return {path}


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ObservableNotFound(Exception):
    """Raised when a path token cannot be resolved via RunReader."""


class WindowNotSupported(Exception):
    """Raised when a window spec is not in the closed vocabulary."""


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Config-selection (issue #98): read the run's DECLARED params from the study's
# condition block — deterministic and backend-independent (no RunReader change).
# ---------------------------------------------------------------------------

def _config_lookup(cfg: dict, path: str) -> Any:
    """Resolve a dotted path in a params dict; None if any token is missing."""
    cur: Any = cfg or {}
    for tok in str(path).split("."):
        if isinstance(cur, dict) and tok in cur:
            cur = cur[tok]
        else:
            return None
    return cur


def _resolve_run_config(spec: dict, test: dict) -> dict:
    """Declared params for the run a test targets.

    baseline by default; a variant's params merged over the baseline when
    ``given.run == variant``. Reads the v4 ``conditions.baseline``/``conditions.variants``
    shape, falling back to the v3 top-level ``baseline``/``variants``.
    """
    conditions = spec.get("conditions") if isinstance(spec.get("conditions"), dict) else {}
    base = conditions.get("baseline") if isinstance(conditions.get("baseline"), dict) else None
    if base is None:
        bl = spec.get("baseline")
        if isinstance(bl, list) and bl and isinstance(bl[0], dict):
            base = bl[0]
        elif isinstance(bl, dict):
            base = bl
    cfg = dict((base or {}).get("params") or {})
    given = test.get("given") or {}
    if given.get("run") == "variant" and given.get("variant"):
        variants: list = []
        if isinstance(conditions.get("variants"), list):
            variants += conditions["variants"]
        if isinstance(spec.get("variants"), list):
            variants += spec["variants"]
        for v in variants:
            if isinstance(v, dict) and v.get("name") == given["variant"]:
                cfg.update(v.get("params") or v.get("parameter_overrides") or {})
                break
    return cfg


def _as_float(x: Any) -> float | None:
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _resolve_expected(pass_if: dict, config: dict | None) -> Any:
    """Expected value for an equals/comparison op: a `config:` reference (into
    the run's declared params) takes precedence over a literal `value:`."""
    if "config" in pass_if:
        return _config_lookup(config or {}, str(pass_if["config"]))
    return pass_if.get("value")


def _equals_ok(measured: Any, target: Any, pass_if: dict) -> bool:
    """Equality with optional numeric tolerance; exact for categoricals."""
    m, t = _as_float(measured), _as_float(target)
    if m is not None and t is not None:
        tolf = pass_if.get("tolerance_fraction")
        if tolf is not None:
            return abs(m - t) <= abs(float(tolf)) * abs(t)
        tol = float(pass_if["tolerance"]) if pass_if.get("tolerance") is not None else 0.0
        return abs(m - t) <= tol
    return measured == target


def _evaluate_config_value(test: dict, config: dict | None) -> dict:
    """Evaluate a ``kind: config_value`` measure against the run's declared params.

    No run data / windowing — the measured value is the config field itself, so
    this supports categorical (string) params that the numeric series path can't.
    """
    measure = test.get("measure") or {}
    pass_if = test.get("pass_if")
    if not pass_if:
        return _agent("missing pass_if block")
    op = pass_if.get("op", "")
    if not _op_supported(op):
        return _agent(f"unsupported op: {op!r}")
    path = (measure.get("path") or measure.get("field") or "").strip()
    if not path:
        return _agent("config_value measure needs a path/field")
    measured = _config_lookup(config or {}, path)
    if measured is None:
        return _agent(f"config field {path!r} not found in declared params")
    label = f"config_value/{op}"
    if op in ("equals", "==", "eq"):
        target = _resolve_expected(pass_if, config)
        ok = _equals_ok(measured, target, pass_if)
        return _code_outcome("PASS" if ok else "FAIL", measured, label,
                             f"{measured!r} == {target!r}")
    if op == "in_set":
        target_set = set(pass_if.get("set") or [])
        if "config" in pass_if:
            cv = _config_lookup(config or {}, str(pass_if["config"]))
            if isinstance(cv, (list, tuple, set)):
                target_set = set(cv)
        ok = measured in target_set
        return _code_outcome("PASS" if ok else "FAIL", measured, label,
                             f"{measured!r} in {sorted(map(str, target_set))}")
    m = _as_float(measured)
    t = _as_float(_resolve_expected(pass_if, config))
    cmps = {"<=": (lambda a, b: a <= b), ">=": (lambda a, b: a >= b),
            "<": (lambda a, b: a < b), ">": (lambda a, b: a > b),
            "!=": (lambda a, b: a != b)}
    if m is not None and t is not None and op in cmps:
        ok = cmps[op](m, t)
        return _code_outcome("PASS" if ok else "FAIL", measured, label, f"{m} {op} {t}")
    return _agent(f"config_value: unsupported op {op!r} for value {measured!r}")


# ---------------------------------------------------------------------------
# Cross-run measures (issue #98): compare a readout across two runs.
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


def evaluate_study(spec: dict, reader: "RunReader", ws_root=None, run_opener=None) -> dict[str, dict]:
    """Evaluate all behavior tests in a study spec.

    ws_root enables workspace-pluggable evaluators (load_workspace_evaluators);
    omit it to evaluate with native kinds only. ``run_opener`` — an optional
    ``callable(selector) -> RunReader | None`` — enables cross-run measures
    (``kind: run_delta``); omit it and those tests report ``needs_rerun``.
    """
    tests = spec.get("tests") or spec.get("behavior_tests") or []
    results: dict[str, dict] = {}
    for i, test in enumerate(tests):
        name = test.get("name", f"test_{i}")
        config = _resolve_run_config(spec, test)
        results[name] = evaluate_test(test, reader, ws_root=ws_root,
                                      config=config, run_opener=run_opener)
    return results


def evaluate_test(test: dict, reader: "RunReader", ws_root=None, config=None,
                  run_opener=None) -> dict:
    """Evaluate a single behavior test against a run.

    Resolution order: config-selection (``kind: config_value``) → cross-run
    (``kind: run_delta``) → native RUN_DATA_KIND (code) → workspace-registered
    evaluator for the kind → agent bucket. ``config`` is the run's declared
    params (see ``_resolve_run_config``), used by ``config_value`` measures and
    ``config:``-referenced expected values. ``run_opener`` — ``callable(selector)
    -> RunReader | None`` — enables ``run_delta`` cross-run measures.
    """
    # 1. Require measure block
    measure = test.get("measure")
    if not measure:
        return _agent("missing measure block")

    # 1b. Config-selection: read a declared param, no run data needed (#98).
    if measure.get("kind", "") == "config_value":
        return _evaluate_config_value(test, config)

    # 1c. Cross-run: compare a readout across two runs (#98).
    if measure.get("kind", "") == "run_delta":
        return _evaluate_run_delta(test, reader, run_opener)

    # 2. Kind: native run-data, else a workspace-registered evaluator, else agent.
    #    A candidate_model sentinel (e.g. "@recovered") means "grade the real search, not the
    #    recorded run" — route to the registered evaluator for the kind REGARDLESS of
    #    RUN_DATA_KINDS, so the native path can't silently grade the recorded (baseline) run.
    kind = measure.get("kind", "")
    cm = measure.get("candidate_model")
    sentinel = isinstance(cm, str) and cm.startswith("@")
    if sentinel or kind not in RUN_DATA_KINDS:
        registry = load_workspace_evaluators(ws_root)
        evaluator = registry.get(kind)
        if evaluator is not None:
            try:
                return evaluator(test, reader, ws_root)
            except Exception as exc:  # noqa: BLE001
                return _agent(f"workspace evaluator {kind!r} error: {exc}")
        if sentinel:
            return _agent(f"candidate_model {cm!r} needs a workspace evaluator for kind "
                          f"{kind!r}; refusing to grade the recorded run as the candidate")
        return _agent(f"non-run-data kind: {kind!r}")

    # 3. Require pass_if block
    pass_if = test.get("pass_if")
    if not pass_if:
        return _agent("missing pass_if block")

    # 4. Op must be in the closed set
    op = pass_if.get("op", "")
    if not _op_supported(op):
        return _agent(f"unsupported op: {op!r}")

    # 5. Require path/field/formula
    path = (measure.get("path") or measure.get("field") or measure.get("formula") or "").strip()
    if not path:
        return _agent("missing path/field/formula in measure")

    # 6. Validate window before touching the reader
    window_spec = (measure.get("window") or "full_lineage_from_gen_0").strip()
    try:
        _validate_window(window_spec)
    except WindowNotSupported as exc:
        return _agent(str(exc))

    # 7. Resolve the observable series — falling back to a workspace-registered
    #    derived-scalar computer when the field is not an emitted observable.
    try:
        series = _resolve_series(path, reader)
    except ObservableNotFound as exc:
        fn = load_workspace_derived_scalars(ws_root).get(path)
        if fn is None:
            return _agent(str(exc))
        try:
            series = _scalar_series(fn(reader, test, ws_root))
        except Exception as exc2:  # noqa: BLE001
            return _agent(f"derived-scalar computer {path!r} error: {exc2}")
    except Exception as exc:  # noqa: BLE001
        return _agent(f"series resolution error: {exc}")

    # 8. Apply window
    try:
        windowed = _apply_window(series, window_spec)
    except WindowNotSupported as exc:
        return _agent(str(exc))

    # 9. Guard against empty/partial data
    if _is_empty_window(windowed):
        return _needs_rerun("empty or partial series data after windowing")

    # 10. Reduce + predicate → outcome, then attach the /v2 axis (best-effort).
    try:
        outcome = _apply_op(windowed, pass_if, kind, op, config=config)
    except Exception as exc:  # noqa: BLE001
        return _agent(f"evaluation error: {exc}")
    if outcome.get("evaluated_by") == "code":
        try:
            axis = _grade_axis_from_outcome(test, pass_if, op, outcome)
            if axis is not None:
                outcome["axis"] = axis
        except Exception:  # noqa: BLE001 — grading is enrichment; never fail the test on it
            pass
    return outcome


# ---------------------------------------------------------------------------
# Bucket constructors
# ---------------------------------------------------------------------------

def _agent(reason: str) -> dict:
    return {"evaluated_by": "agent", "reason": reason}


def _needs_rerun(reason: str) -> dict:
    return {"evaluated_by": "needs_rerun", "reason": reason}


def _sig6(x: Any, n: int = 6) -> Any:
    """Round a measured scalar to ``n`` SIGNIFICANT figures (not decimal places).

    Fixed-decimal ``round(x, 6)`` zeroes any value below ~1e-6, which silently
    destroyed small-magnitude observables (e.g. ~1e-22 metabolite fluxes) so their
    findings could never match ``observed`` and never verify. Significant-figure
    rounding preserves magnitude: ``3.34e-22`` stays ``3.34e-22``, ``0.536`` stays
    ``0.536``. Non-finite / None / non-numeric pass through unchanged."""
    import math
    xf = _as_float(x)
    if xf is None:
        return x
    if xf == 0 or not math.isfinite(xf):
        return xf
    return float(f"{xf:.{n}g}")


def _code_outcome(result: str, measured_value: Any, operator: str, detail: str) -> dict:
    return {
        "result": result,
        "measured_value": measured_value,
        "evaluated_by": "code",
        "operator": operator,
        "detail": detail,
    }


# ---------------------------------------------------------------------------
# Path / expression resolver
# ---------------------------------------------------------------------------

# Regex for dotted identifiers (e.g. listeners.mass.cell_mass, obs_name, DnaA)
_DOTTED_IDENT = re.compile(r"[A-Za-z_]\w*(?:\.\w+)*")

# Regex for molecule/bulk IDs with brackets (e.g. MONOMER0-160[c], PD03831[c])
# Must match BEFORE the plain ident pattern so the bracket part is captured.
_BRACKET_ID = re.compile(r"[A-Z][A-Z0-9_]*(?:-[A-Z0-9]+)*\[[a-z]+\]")


def _extract_observable_tokens(path: str) -> list[str]:
    """Extract all identifier tokens from an expression/path string.

    Returns unique tokens in order of first appearance.  Both dotted paths
    (listeners.mass.cell_mass) and bracketed molecule IDs (MONOMER0-160[c])
    are recognised.
    """
    seen: set[str] = set()
    tokens: list[str] = []

    # Find bracket-style molecule IDs first
    bracket_spans: set[int] = set()
    for m in _BRACKET_ID.finditer(path):
        tok = m.group(0)
        bracket_spans.update(range(m.start(), m.end()))
        if tok not in seen:
            seen.add(tok)
            tokens.append(tok)

    # Then find dotted/simple identifiers, skipping already-matched spans
    for m in _DOTTED_IDENT.finditer(path):
        if any(i in bracket_spans for i in range(m.start(), m.end())):
            continue
        tok = m.group(0)
        if tok not in seen:
            seen.add(tok)
            tokens.append(tok)

    return tokens


def _try_select_fallback(token: str, reader: "RunReader") -> "pl.DataFrame | None":
    """Try to resolve a failed ``series(token)`` via ``reader.select``.

    Handles two selector types:
    - Bracket-style bulk IDs (e.g. ``MONOMER0-160[c]``) → ``bulk_id`` selector.
    - Dotted-path literal-index tokens (not applicable here; handled at the
      path level in :func:`_resolve_series`).

    Returns the ``[generation, time, abs_time, value]`` DataFrame on success,
    or ``None`` when the token cannot be mapped to a selector (never-guess
    preserved: only bulk-id bracket tokens are attempted).
    """
    if _BRACKET_ID.fullmatch(token):
        try:
            return reader.select({"type": "bulk_id", "value": token})
        except Exception:  # noqa: BLE001
            return None
    return None


def _resolve_series(path: str, reader: "RunReader") -> pl.DataFrame:
    """Resolve an observable path or arithmetic expression to a series.

    Falls back to ``reader.select`` when ``reader.series`` fails:

    - Bracket-style bulk IDs (e.g. ``MONOMER0-160[c]``) in expressions →
      ``select({"type": "bulk_id", "value": token})``.
    - Simple literal-index path (e.g. ``listeners.monomer_counts[3]``) →
      ``select({"type": "literal_index", "value": 3, "observable": "..."})``.

    ``ObservableNotFound`` is raised only when BOTH ``series`` and the
    appropriate ``select`` fallback fail — never-guess is preserved.

    Verdict-safe element-only resolver hybrid (SP2b-iii): only **element-kind**
    readouts are routed through the canonical ``readout_resolver`` (folding the
    literal-index regex into a single source — see the fast path below). The
    ``scalar``, ``expression``, bare bracket **bulk-id**, and trailing-**prose**
    dialects are deliberately handled by this function's local body, NOT the
    resolver, because the resolver (a) returns ``UnresolvedReadout`` for a bare
    bracket bulk id such as ``MONOMER0-160[c]`` — which the tokenize/``select``
    body here resolves — and (b) strips a trailing parenthetical to a bare
    ``scalar`` — which the evaluator correctly rejects. Routing either through
    the resolver would change verdicts; only the literal-index dedup is safe.
    Grounding: docs/plans/2026-06-12-sp2b-iii-evaluator-hybrid-plan.md.

    Args:
        path:   Observable path (e.g. ``listeners.mass.cell_mass``) or a
                simple arithmetic expression over observables
                (e.g. ``a / (b + c)``).  Literal numbers (``* 2``) are
                allowed.  Bulk-id expressions are also supported
                (e.g. ``MONOMER0-160[c] / (PD03831[c] + MONOMER0-160[c])``).
        reader: RunReader opened on the target run.

    Returns:
        Polars DataFrame with columns ``[generation, time, abs_time, value]``.

    Raises:
        ObservableNotFound: If any token in *path* cannot be resolved via
            either ``series()`` or the selector fallback.
    """
    # Fast path: element-kind readouts (e.g. literal-index
    # "listeners.monomer_counts[3]") are routed through the canonical
    # readout_resolver so the literal-index regex is single-sourced — before
    # general tokenisation to avoid subscript in _eval_expression. The
    # resolver's ResolvedReadout.to_select_dict() for a literal-index element
    # is byte-identical to the dict the old in-evaluator fast path produced
    # ({"type": "literal_index", "value": N, "observable": "..."}).
    #
    # SP2b-iii hybrid (verdict-safe): ONLY element-kind is routed here.
    # scalar/expression/bare-bulk/prose deliberately stay on the local body
    # below because the resolver (a) returns UnresolvedReadout for bare bracket
    # bulk ids like MONOMER0-160[c] (which the tokenize/select body resolves)
    # and (b) strips trailing prose to a scalar (which the evaluator correctly
    # rejects) — routing either through the resolver would change verdicts.
    # See docs/plans/2026-06-12-sp2b-iii-evaluator-hybrid-plan.md.
    from discovery_engine.readout_resolver import ResolvedReadout, resolve_readout

    r = resolve_readout({"identifier": path.strip()})
    if isinstance(r, ResolvedReadout) and r.kind == "element":
        select_dict = r.to_select_dict()
        try:
            return reader.select(select_dict)
        except Exception as exc:  # noqa: BLE001
            raise ObservableNotFound(
                f"element readout {path!r} not resolvable: {exc}"
            ) from exc

    tokens = _extract_observable_tokens(path)
    if not tokens:
        raise ObservableNotFound(f"no observable tokens found in path: {path!r}")

    # Resolve each token — with selector fallback for bulk-id tokens.
    resolved: dict[str, pl.DataFrame] = {}
    for token in tokens:
        try:
            s = reader.series(token)
            resolved[token] = s
        except (KeyError, Exception):  # noqa: BLE001 — try fallback before giving up
            s = _try_select_fallback(token, reader)
            if s is None:
                raise ObservableNotFound(
                    f"observable {token!r} not resolvable"
                )
            resolved[token] = s

    # If there is exactly one observable AND the path is just that token
    # (no surrounding arithmetic), return the series directly.
    if len(resolved) == 1:
        sole_token = list(resolved.keys())[0]
        if path.strip() == sole_token:
            return list(resolved.values())[0]

    # Either multiple observables OR a single observable embedded in an
    # arithmetic expression (e.g. "obs.a / 2") → evaluate the full expression.
    return _eval_expression(path, resolved)


def _scalar_series(value: float) -> pl.DataFrame:
    """Wrap a single computed scalar as the flat series shape the pipeline expects.

    One row so the default ``full_lineage_from_gen_0`` window keeps it and
    ``_apply_op`` reduces it exactly as for an emitted observable.
    """
    return pl.DataFrame({
        "generation": [1], "time": [0.0], "abs_time": [0.0], "value": [float(value)],
    })


def _eval_expression(expr: str, token_series: dict[str, pl.DataFrame]) -> pl.DataFrame:
    """Evaluate a multi-observable arithmetic expression over aligned series.

    Substitutes each token with a safe Python identifier ``_v0``, ``_v1``, …,
    parses the result with :py:func:`ast.parse`, and evaluates it using numpy
    array arithmetic — no ``eval`` of user-supplied code.
    """
    import numpy as np

    keys = list(token_series.keys())

    # Build substituted expression string (_v0, _v1, …) for ast.parse
    subst = expr
    for i, token in enumerate(keys):
        subst = subst.replace(token, f"_v{i}")

    # Align all series on (generation, abs_time) via inner join.
    # Deduplicate the join keys first: a lineage can carry a 1-tick birth-stub
    # agent (the "d?1" slot) that repeats a (generation, abs_time) key, which
    # would otherwise blow the inner join up many-to-many (e.g. 47240 -> 49322
    # rows) and inject misaligned/NaN values into the expression.
    def _dedup(df: pl.DataFrame) -> pl.DataFrame:
        return df.unique(subset=["generation", "abs_time"], keep="first", maintain_order=True)

    first = _dedup(token_series[keys[0]])
    joined = first.rename({"value": "_v0"})
    for i, token in enumerate(keys[1:], 1):
        other = _dedup(token_series[token]).select(
            ["generation", "abs_time", pl.col("value").alias(f"_v{i}")]
        )
        joined = joined.join(other, on=["generation", "abs_time"], how="inner")

    # Collect numpy arrays for each variable
    np_vars: dict[str, np.ndarray] = {
        f"_v{i}": joined[f"_v{i}"].to_numpy() for i in range(len(keys))
    }

    # Safe AST evaluation
    try:
        tree = ast.parse(subst, mode="eval")
        result_vals: Any = _eval_ast_node(tree.body, np_vars)
    except Exception as exc:
        raise ObservableNotFound(f"expression evaluation failed: {exc}") from exc

    if not isinstance(result_vals, np.ndarray):
        result_vals = np.full(len(joined), float(result_vals))

    return joined.select(["generation", "time", "abs_time"]).with_columns(
        pl.Series("value", result_vals, dtype=pl.Float64)
    )


def _eval_ast_node(node: ast.expr, names: dict) -> Any:
    """Safely evaluate an AST expression node using only arithmetic operations.

    Allowed node types: Constant, Name, BinOp (+, -, *, /), UnaryOp (+, -).
    All other node types raise :py:exc:`ValueError`.
    """
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        if node.id not in names:
            raise ValueError(f"unknown variable {node.id!r}")
        return names[node.id]
    if isinstance(node, ast.BinOp):
        left = _eval_ast_node(node.left, names)
        right = _eval_ast_node(node.right, names)
        op = node.op
        if isinstance(op, ast.Add):
            return left + right
        if isinstance(op, ast.Sub):
            return left - right
        if isinstance(op, ast.Mult):
            return left * right
        if isinstance(op, ast.Div):
            return left / right
        raise ValueError(f"unsupported operator: {type(op).__name__}")
    if isinstance(node, ast.UnaryOp):
        operand = _eval_ast_node(node.operand, names)
        if isinstance(node.op, ast.USub):
            return -operand
        if isinstance(node.op, ast.UAdd):
            return operand
        raise ValueError(f"unsupported unary operator: {type(node.op).__name__}")
    raise ValueError(f"unsupported AST node type: {type(node).__name__}")


# ---------------------------------------------------------------------------
# Window vocabulary
# ---------------------------------------------------------------------------

_FROM_GEN_RE = re.compile(r"^from_generation_(\d+)$")
_PEAK_FROM_GEN_RE = re.compile(r"^peak_of_each_cycle_from_gen_(\d+)$")

_KNOWN_WINDOWS = frozenset({
    "full_lineage_from_gen_0",
    "every_generation",
    "peak_of_each_cycle",
    "gen_steady_state",
    "per_minute_full_lineage",  # rate window: Δvalue/Δtime_seconds × 60
})


def _validate_window(window_spec: str) -> None:
    """Raise WindowNotSupported if the window is not in the closed vocabulary."""
    if window_spec in _KNOWN_WINDOWS:
        return
    if _FROM_GEN_RE.match(window_spec):
        return
    if _PEAK_FROM_GEN_RE.match(window_spec):
        return
    raise WindowNotSupported(f"unsupported window: {window_spec!r}")


# WindowResult type: tuple of (kind_str, data)
#   ("flat",           pl.DataFrame)          — flat series (all ticks)
#   ("per_gen_all",    dict[int, pl.DataFrame])— one DF per gen (all ticks of that gen)
#   ("per_gen_scalar", dict[int, float])       — one scalar per gen

def _apply_window(series: pl.DataFrame, window_spec: str) -> tuple:
    """Apply window to a series DataFrame.

    Returns a ``(kind, data)`` tuple for downstream consumption.

    Raises:
        WindowNotSupported: for unrecognised window specs.
    """
    if window_spec == "full_lineage_from_gen_0":
        return ("flat", series)

    if window_spec == "per_minute_full_lineage":
        # Rate window: per-step Δvalue/Δabs_time × 60 (converts seconds → per-minute).
        # abs_time is used (cumulative across generations) for a monotonic time axis.
        # The first row is dropped (no previous step); rows where Δtime=0 are also
        # dropped (guard against div-by-zero / inf).
        if len(series) < 2:
            return ("flat", series.head(0))
        sorted_s = series.sort("abs_time")
        with_rate = sorted_s.with_columns(
            pl.when(pl.col("abs_time").diff() == 0.0)
            .then(pl.lit(None).cast(pl.Float64))
            .otherwise(pl.col("value").diff() / pl.col("abs_time").diff() * 60.0)
            .alias("value")
        )
        # Drop the first row (diff gives null) and any zero-dt rows (set to null above)
        result = with_rate.slice(1).filter(pl.col("value").is_not_null())
        return ("flat", result)

    if window_spec == "every_generation":
        try:
            from viva_emitters import by_generation  # noqa: PLC0415
        except ImportError as _ie:
            raise ImportError(
                "by_generation requires the parquet reader: "
                "pip install 'viva-emitters[parquet]'"
            ) from _ie
        return ("per_gen_all", by_generation(series))

    m = _FROM_GEN_RE.match(window_spec)
    if m:
        n = int(m.group(1))
        return ("flat", series.filter(pl.col("generation") >= n))

    if window_spec == "gen_steady_state":
        return ("flat", series.filter(pl.col("generation") >= 3))

    if window_spec == "peak_of_each_cycle":
        peaks = (
            series
            .group_by("generation")
            .agg(pl.col("value").max().alias("value"))
            .sort("generation")
        )
        return ("per_gen_scalar", {int(g): float(v) for g, v in peaks.iter_rows()})

    m2 = _PEAK_FROM_GEN_RE.match(window_spec)
    if m2:
        n = int(m2.group(1))
        filtered = series.filter(pl.col("generation") >= n)
        peaks = (
            filtered
            .group_by("generation")
            .agg(pl.col("value").max().alias("value"))
            .sort("generation")
        )
        return ("per_gen_scalar", {int(g): float(v) for g, v in peaks.iter_rows()})

    raise WindowNotSupported(f"unsupported window: {window_spec!r}")


def _is_empty_window(windowed: tuple) -> bool:
    """Return True if the windowed result contains no data."""
    kind, data = windowed
    if kind == "flat":
        return len(data) == 0
    return len(data) == 0  # works for both dict types


# ---------------------------------------------------------------------------
# Closed pass_if operator set
# ---------------------------------------------------------------------------

_SUPPORTED_OPS: frozenset[str] = frozenset({
    "range",
    "in_range",
    "in_range_every_generation",
    "generation_average_in_range",
    "<=", ">=", "<", ">", "==", "!=", "eq", "equals",
    "in_set",
    "cv_below",
    "median_within_tolerance",
    "periodic_doubling_every_generation",
    "exactly_one_initiation_per_generation",
    "max_le", "max_lt", "min_ge", "min_gt",
    "rises_within_cycle",
})


def _op_supported(op: str) -> bool:
    return op in _SUPPORTED_OPS


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


# ---------------------------------------------------------------------------
# B2b: per-run store resolution
# ---------------------------------------------------------------------------

def _to_plain(obj: Any) -> Any:
    """Recursively convert ruamel.yaml objects to plain Python types."""
    if hasattr(obj, "items"):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_plain(v) for v in obj]
    return obj


def _resolve_run_store(
    run: dict,
    study_dir: Any,
    ws_root: Any = None,
) -> "str | None":
    """Resolve a run entry to a RunReader-openable path.

    Priority: ``emitter.store`` → ``store_path`` → ``store`` → ``run_dir`` → ``parquet``.
    Relative paths are resolved against *study_dir*, then *ws_root*, then cwd.
    For ``parquet`` candidates: if the resolved path lacks a ``history/``
    subdirectory, descend to the first child directory that has one (the
    experiment dir that RunReader can open).

    Args:
        run:       Run dict from study.yaml ``runs[]``.
        study_dir: Directory containing the study.yaml.
        ws_root:   Workspace root for resolving relative paths.

    Returns:
        Resolved absolute path string, or ``None`` if unresolvable.
    """
    import os
    from pathlib import Path as _Path

    study_dir = _Path(study_dir)
    ws_root = _Path(ws_root) if ws_root else None

    # Build (raw_path, is_parquet) candidates in priority order
    candidates: list[tuple[str, bool]] = []
    emitter = run.get("emitter") or {}
    if isinstance(emitter, dict) and emitter.get("store"):
        candidates.append((str(emitter["store"]), False))
    if run.get("store_path"):
        candidates.append((str(run["store_path"]), False))
    if run.get("store"):
        candidates.append((str(run["store"]), False))
    if run.get("run_dir"):
        candidates.append((str(run["run_dir"]), False))
    if run.get("parquet"):
        candidates.append((str(run["parquet"]), True))

    # (No early return on empty candidates: an entry with no explicit store pointer still
    # falls through to the shared-runs.db fallback below.)

    # Bases for relative-path resolution (tried in order)
    bases: list[_Path | None] = [None]  # None = treat path as absolute/cwd-relative
    bases.append(study_dir)
    if ws_root:
        bases.append(ws_root)
    bases.append(_Path(os.getcwd()))

    for raw_path, is_parquet in candidates:
        raw = _Path(raw_path)

        # Find an existing path
        resolved: _Path | None = None
        for base in bases:
            candidate = raw if base is None else base / raw
            if candidate.exists():
                resolved = candidate
                break

        if resolved is None:
            continue

        if is_parquet:
            # If the resolved dir already has history/, use it directly
            if (resolved / "history").exists():
                return str(resolved)
            # Descend to the first subdirectory that contains history/
            try:
                children = sorted(
                    c for c in resolved.iterdir() if c.is_dir()
                )
                for child in children:
                    if (child / "history").exists():
                        return str(child)
            except PermissionError:
                pass
            # No descendant with history/ found — skip this candidate
            continue
        else:
            return str(resolved)

    # Fallback: the study's shared multi-run store. `vivarium-workbench run study` writes a
    # run into <study_dir>/runs.db but does not always record a per-run store pointer
    # (emitter_path can be None in runs_meta), leaving the entry with no candidate above —
    # so the run's real data is present but unresolvable, and the gate it computes never
    # gets recorded. If this entry's run_id is in that runs.db, resolve to it (RunReader
    # opens the shared DB directly). Scoped to a present run_id so stale entries pointing
    # at a gone per-run store don't all collapse onto the shared DB. (A multi-run DB with
    # several present ids still resolves them to the same path; selecting by run_id inside
    # RunReader is a follow-up.)
    shared = study_dir / "runs.db"
    if shared.exists() and run.get("name"):
        try:
            from . import run_registry
            if any(r.get("run_id") == run["name"] for r in run_registry.list_runs(shared)):
                return str(shared)
        except Exception:  # noqa: BLE001 — a best-effort probe, never block resolution
            pass

    return None


# ---------------------------------------------------------------------------
# B2b: compute_outcomes — write parallel computed_outcomes block per run
# ---------------------------------------------------------------------------

def _select_run_entry(runs: list, selector: dict) -> "dict | None":
    """Map a run selector to a ``runs[]`` entry (cross-run convention, #98 2b).

    - ``{run: variant, variant: X}`` → the entry whose ``variant`` or ``name`` is X.
    - ``{run: baseline}`` (default) → the entry with ``canonical: true``, else the
      first with no ``variant``, else the first entry.
    """
    if (selector or {}).get("run") == "variant":
        want = selector.get("variant")
        for r in runs:
            if isinstance(r, dict) and (r.get("variant") == want or r.get("name") == want):
                return r
        return None
    for r in runs:
        if isinstance(r, dict) and r.get("canonical") is True:
            return r
    for r in runs:
        if isinstance(r, dict) and not r.get("variant"):
            return r
    return runs[0] if runs and isinstance(runs[0], dict) else None


def _build_run_opener(runs: list, study_dir: Any, ws_root: Any):
    """Return ``opener(selector) -> RunReader | None`` over a study's ``runs[]``.

    Resolves a selector to a run entry (``_select_run_entry``), then to a store
    (``_resolve_run_store``), then opens a reader (cached by store path). Returns
    None when the run/store can't be resolved or opened. This is the plugin-side
    resolver that lets cross-run (``run_delta``) tests load their compare run.
    """
    try:
        from viva_emitters import RunReader  # noqa: PLC0415
    except ImportError:
        return None
    cache: dict = {}

    def opener(selector):
        entry = _select_run_entry(runs, selector or {})
        if entry is None:
            return None
        store = _resolve_run_store(entry, study_dir, ws_root)
        if store is None:
            return None
        if store not in cache:
            try:
                cache[store] = RunReader.open(store)
            except Exception:  # noqa: BLE001
                cache[store] = None
        return cache[store]

    return opener


def compute_outcomes(
    study_dir: Any,
    ws_root: Any = None,
) -> dict:
    """Evaluate all study runs and write a parallel ``computed_outcomes`` block.

    Non-destructive: ``run["outcomes"]`` (authored) is **never** modified.
    Writes ``run["computed_outcomes"]`` per run using a ruamel.yaml round-trip
    to preserve comments.  Idempotent: writes the file only when content changed.

    For each test in ``computed_outcomes``, attaches a ``reconcile`` flag:
      - ``agree``       — code result matches authored result.
      - ``divergent``   — code result differs from authored result.
      - ``no_authored`` — no authored entry, or code produced no result
                          (agent / needs_rerun bucket).

    If a run's store cannot be resolved, writes
    ``computed_outcomes = {_status: store_unresolved}`` and never fabricates
    verdicts.

    Args:
        study_dir: Path to the directory containing ``study.yaml``.
        ws_root:   Workspace root for resolving relative paths in run entries.

    Returns:
        Summary dict: ``{"runs_evaluated": int, "tests_code": int, "tests_agent": int}``
    """
    import io
    from pathlib import Path as _Path

    import ruamel.yaml
    import yaml as _yaml

    study_dir = _Path(study_dir)
    ws_root = _Path(ws_root) if ws_root else None
    study_yaml_path = study_dir / "study.yaml"

    # Plain load for evaluate_study (reads tests / behavior_tests)
    spec = _yaml.safe_load(study_yaml_path.read_text(encoding="utf-8"))

    # Cross-run (run_delta) tests are study-level, not per-run: split them out of
    # the per-run pass and evaluate them once after the loop (#98 Stage 2b).
    _tests_key = "tests" if spec.get("tests") else "behavior_tests"
    _all_tests = spec.get(_tests_key) or []
    _cross_run_tests = [
        t for t in _all_tests
        if isinstance(t, dict) and (t.get("measure") or {}).get("kind") == "run_delta"
    ]
    per_run_spec = dict(spec)
    per_run_spec[_tests_key] = [t for t in _all_tests if t not in _cross_run_tests]

    # ruamel round-trip load: preserves comments + block style
    ryaml = ruamel.yaml.YAML()
    ryaml.preserve_quotes = True
    ryaml.width = 4096  # prevent unwanted line-wrapping
    with open(study_yaml_path, encoding="utf-8") as fh:
        doc = ryaml.load(fh)

    # Capture original serialised form for change detection
    _orig_sio = io.StringIO()
    ryaml.dump(doc, _orig_sio)
    original_serialised = _orig_sio.getvalue()

    runs_evaluated = 0
    tests_code = 0
    tests_agent = 0

    for run in doc.get("runs") or []:
        store_path = _resolve_run_store(run, study_dir, ws_root)

        store_status = None
        if store_path is None:
            # The run store is unresolved. We can still grade STORE-LESS tests — their
            # evaluator re-derives its result internally (e.g. a recovery search re-runs
            # SINDy+fit) and needs no reader. Only genuinely run-data tests stay ungraded
            # here, so don't blanket-skip when store-less tests are present.
            _sl = _storeless_kinds(ws_root)
            sl_tests = [t for t in (per_run_spec.get(_tests_key) or [])
                        if isinstance(t, dict) and (t.get("measure") or {}).get("kind") in _sl]
            if not sl_tests:
                run["computed_outcomes"] = ruamel.yaml.CommentedMap(
                    {"_status": "store_unresolved", "_grader": _grader_stamp()}
                )
                continue
            try:
                outcomes = evaluate_study({**per_run_spec, _tests_key: sl_tests}, None,
                                          ws_root=ws_root)
            except Exception as exc:  # noqa: BLE001
                run["computed_outcomes"] = ruamel.yaml.CommentedMap(
                    {"_status": f"evaluation_error: {exc}", "_grader": _grader_stamp()}
                )
                continue
            store_status = "store_less_graded"  # store unresolved; only store-less tests graded
        else:
            # Open reader and evaluate
            try:
                try:
                    from viva_emitters import RunReader  # noqa: PLC0415
                except ImportError as _ie:
                    raise ImportError(
                        "RunReader requires the parquet reader: "
                        "pip install 'viva-emitters[parquet]'"
                    ) from _ie
                reader = RunReader.open(store_path)
                outcomes = evaluate_study(per_run_spec, reader, ws_root=ws_root)
            except Exception as exc:  # noqa: BLE001
                run["computed_outcomes"] = ruamel.yaml.CommentedMap(
                    {"_status": f"evaluation_error: {exc}", "_grader": _grader_stamp()}
                )
                continue

        # Build computed_outcomes with reconcile flags
        authored = run.get("outcomes") or {}
        new_co = ruamel.yaml.CommentedMap()
        for test_name, outcome in outcomes.items():
            entry = ruamel.yaml.CommentedMap(outcome)

            authored_entry = authored.get(test_name) if isinstance(authored, dict) else None
            authored_result = (
                authored_entry.get("result")
                if isinstance(authored_entry, dict)
                else None
            )
            code_result = outcome.get("result")  # None for agent / needs_rerun
            if code_result == "ungraded":
                code_result = None  # a skip (couldn't grade), not a verdict — reconcile as no_authored, not divergent

            if code_result is not None and authored_result is not None:
                reconcile = "agree" if code_result == authored_result else "divergent"
            else:
                reconcile = "no_authored"

            entry["reconcile"] = reconcile
            new_co[test_name] = entry

            by = outcome.get("evaluated_by", "")
            if by == "code":
                tests_code += 1
            else:
                tests_agent += 1

        if store_status:
            new_co["_status"] = store_status
        new_co["_grader"] = ruamel.yaml.CommentedMap(_grader_stamp())
        run["computed_outcomes"] = new_co
        runs_evaluated += 1

    # Study-level cross-run pass: evaluate run_delta tests ONCE (not per run) and
    # attach each to its primary run's computed_outcomes (#98 Stage 2b).
    runs_list = doc.get("runs") or []
    if _cross_run_tests:
        opener = _build_run_opener(runs_list, study_dir, ws_root)
        for test in _cross_run_tests:
            name = test.get("name", "run_delta")
            given = test.get("given") or {}
            primary_sel = {k: v for k, v in given.items() if k != "compare_to"} or {"run": "baseline"}
            primary_reader = opener(primary_sel) if opener else None
            outcome = evaluate_test(
                test, primary_reader, ws_root=ws_root,
                config=_resolve_run_config(spec, test), run_opener=opener,
            )
            target = _select_run_entry(runs_list, primary_sel)
            if target is None:
                continue
            co = target.get("computed_outcomes")
            if not isinstance(co, ruamel.yaml.CommentedMap):
                co = ruamel.yaml.CommentedMap()
                target["computed_outcomes"] = co
            authored = target.get("outcomes") or {}
            ae = authored.get(name) if isinstance(authored, dict) else None
            ar = ae.get("result") if isinstance(ae, dict) else None
            cr = outcome.get("result")
            if cr == "ungraded":
                cr = None
            entry = ruamel.yaml.CommentedMap(outcome)
            entry["reconcile"] = (
                ("agree" if cr == ar else "divergent")
                if (cr is not None and ar is not None) else "no_authored"
            )
            co[name] = entry
            if outcome.get("evaluated_by") == "code":
                tests_code += 1
            else:
                tests_agent += 1

    # Write only when something changed (idempotency)
    _new_sio = io.StringIO()
    ryaml.dump(doc, _new_sio)
    new_serialised = _new_sio.getvalue()

    if new_serialised != original_serialised:
        study_io.atomic_write(study_yaml_path, new_serialised)

    return {
        "runs_evaluated": runs_evaluated,
        "tests_code": tests_code,
        "tests_agent": tests_agent,
    }


# ---------------------------------------------------------------------------
# B2b: CLI — viva-compute-outcomes
# ---------------------------------------------------------------------------

def _find_workspace_root(start: Any) -> "Any | None":
    """Walk up from *start* looking for a workspace.yaml sentinel file."""
    from pathlib import Path as _Path

    cur = _Path(start).resolve()
    for _ in range(12):
        if (cur / "workspace.yaml").exists():
            return cur
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    return None


import click as _click  # noqa: E402 — imported here to keep it optional at module top


@_click.command("viva-compute-outcomes")
@_click.option(
    "--workspace", "-w",
    type=_click.Path(file_okay=False),
    default=None,
    help="Workspace root (for resolving relative paths in run entries).",
)
@_click.option(
    "--study", "-s", "study_slug",
    default=None,
    help="Study name/slug to evaluate (looked up under workspace investigations/ or studies/).",
)
@_click.option(
    "--study-dir",
    "study_dir_arg",
    type=_click.Path(exists=True, file_okay=False),
    default=None,
    help="Explicit path to a study directory containing study.yaml.",
)
@_click.option(
    "--all", "all_studies",
    is_flag=True,
    default=False,
    help="Evaluate all studies in the workspace.",
)
def compute_outcomes_cli(
    workspace: "str | None",
    study_slug: "str | None",
    study_dir_arg: "str | None",
    all_studies: bool,
) -> None:
    """Evaluate behavior tests against run data and write computed_outcomes blocks."""
    import sys
    from pathlib import Path as _Path

    ws_root: "_Path | None" = (
        _Path(workspace) if workspace else _find_workspace_root(_Path.cwd())
    )

    targets: list[_Path] = []

    if study_dir_arg:
        targets.append(_Path(study_dir_arg))
    elif study_slug:
        if ws_root is None:
            _click.echo("Cannot find workspace root — pass --workspace.", err=True)
            sys.exit(1)
        for sub in ("investigations", "studies"):
            candidate = ws_root / sub / study_slug
            if (candidate / "study.yaml").exists():
                targets.append(candidate)
                break
        else:
            _click.echo(
                f"Study {study_slug!r} not found under {ws_root}", err=True
            )
            sys.exit(1)
    elif all_studies:
        if ws_root is None:
            _click.echo("Cannot find workspace root — pass --workspace.", err=True)
            sys.exit(1)
        for sub in ("investigations", "studies"):
            base = ws_root / sub
            if base.exists():
                for p in sorted(base.iterdir()):
                    if (p / "study.yaml").exists():
                        targets.append(p)
    else:
        _click.echo(
            "Specify --study-dir, --study <slug>, or --all (with --workspace).",
            err=True,
        )
        sys.exit(1)

    if not targets:
        _click.echo("No studies found.", err=True)
        sys.exit(1)

    total_runs = total_code = total_agent = 0
    for target in targets:
        try:
            summary = compute_outcomes(target, ws_root=ws_root)
            r = summary["runs_evaluated"]
            c = summary["tests_code"]
            a = summary["tests_agent"]
            _click.echo(
                f"  {target.name}: {r} runs evaluated, "
                f"{c} tests code, {a} tests agent"
            )
            total_runs += r
            total_code += c
            total_agent += a
        except Exception as exc:  # noqa: BLE001
            _click.echo(f"  {target.name}: ERROR — {exc}", err=True)

    _click.echo(
        f"\nTotal: {total_runs} runs evaluated, "
        f"{total_code} code, {total_agent} agent"
    )
