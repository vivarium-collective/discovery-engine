"""study.evaluator — pure run-data evaluator + computed-outcomes write-back (B2).

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

This module was split into a package (``common``, ``config``, ``resolver``,
``grading``, ``workspace_plugins``, ``measures``, ``outcomes``, ``cli``) with no
behavior change. This ``__init__`` re-exports the former flat module's entire
public surface so ``from discovery_engine.study.evaluator import X`` keeps
working for every X.
"""
from __future__ import annotations

# --- Incidental module globals re-exported for a backward-compatible dir() ---
# (These were top-level imports of the former flat module; code and tests may
#  reference them as attributes of this module, so keep them bound here.)
import ast
import hashlib
import json
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

import polars as pl

from discovery_engine.reporting.test_contract import Expected, band, check, predicate, value
from discovery_engine.reporting.test_vocab import RANK
from discovery_engine.study import io as study_io

# --- Public API & internals, re-exported from the concern submodules ---------
from .common import (
    RUN_DATA_KINDS,
    ObservableNotFound,
    WindowNotSupported,
    _agent,
    _as_float,
    _code_outcome,
    _equals_ok,
    _needs_rerun,
    _op_supported,
    _sig6,
    _SUPPORTED_OPS,
)
from .config import (
    _config_lookup,
    _evaluate_config_value,
    _resolve_expected,
    _resolve_run_config,
)
from .resolver import (
    _apply_window,
    _BRACKET_ID,
    _DOTTED_IDENT,
    _eval_ast_node,
    _eval_expression,
    _extract_observable_tokens,
    _FROM_GEN_RE,
    _is_empty_window,
    _KNOWN_WINDOWS,
    _PEAK_FROM_GEN_RE,
    _resolve_series,
    _scalar_series,
    _try_select_fallback,
    _validate_window,
)
from .grading import (
    _apply_op,
    _clean_floats,
    _complete_generations,
    _expected_from_pass_if,
    _flat_values,
    _grade_axis_from_outcome,
    _num,
    _scalar_compare,
)
from .workspace_plugins import (
    GRADER_VERSION,
    _evaluator_fingerprint,
    _grader_stamp,
    _inputs_hash,
    _run_provenance_id,
    _sha16,
    _storeless_kinds,
    _workspace_evaluator_packages,
    _workspace_package_slug,
    _WS_DERIVED_SCALAR_CACHE,
    _WS_EVALUATOR_CACHE,
    clear_workspace_evaluator_cache,
    declares_sources,
    load_workspace_derived_scalars,
    load_workspace_evaluators,
    storeless,
)
from .measures import (
    _evaluate_run_delta,
    _grade_bare_string,
    _import_composite_module,
    _run_delta,
    _series_time_value,
    _study_composite_id,
)
from .outcomes import (
    _build_run_opener,
    _resolve_run_store,
    _select_run_entry,
    _to_plain,
    compute_outcomes,
)
from .cli import _click, _find_workspace_root, compute_outcomes_cli


# ---------------------------------------------------------------------------
# Public API + the run-readiness observable mapping.
#
# These are DEFINED here (not in a submodule) on purpose: their bodies reference
# the evaluator helpers as module globals, exactly as the former flat module
# did. That keeps ``monkeypatch.setattr(discovery_engine.study.evaluator, ...)``
# working — a test can stub ``_apply_op`` / ``_resolve_series`` /
# ``load_workspace_derived_scalars`` / ``_import_composite_module`` on this
# package and the orchestration picks up the patched version at call time.
# ---------------------------------------------------------------------------

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


def evaluate_study(spec: dict, reader: "RunReader", ws_root=None, run_opener=None) -> dict[str, dict]:
    """Evaluate all behavior tests in a study spec.

    ws_root enables workspace-pluggable evaluators (load_workspace_evaluators);
    omit it to evaluate with native kinds only. ``run_opener`` — an optional
    ``callable(selector) -> RunReader | None`` — enables cross-run measures
    (``kind: run_delta``); omit it and those tests report ``needs_rerun``.
    """
    tests = spec.get("tests") or spec.get("behavior_tests") or []
    # Resolve the study's composite module ONCE, only when a bare-string measure is present
    # (the function-on-the-composite convention) — otherwise no import cost.
    composite_module = None
    if any(isinstance(t, dict) and isinstance(t.get("measure"), str) for t in tests):
        composite_module = _import_composite_module(_study_composite_id(spec), ws_root)
    results: dict[str, dict] = {}
    for i, test in enumerate(tests):
        name = test.get("name", f"test_{i}")
        config = _resolve_run_config(spec, test)
        results[name] = evaluate_test(test, reader, ws_root=ws_root, config=config,
                                      run_opener=run_opener, composite_module=composite_module)
    return results


def evaluate_test(test: dict, reader: "RunReader", ws_root=None, config=None,
                  run_opener=None, composite_module=None) -> dict:
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
    # 1a. A bare-STRING measure (a composite-observable function `name(rows)->float`, the
    # M-track/workbench convention) grades via the study's composite module + the shared
    # viva_emitters resolver (G2); degrades to agent/needs_rerun when unavailable, never crashes.
    if not isinstance(measure, dict):
        return _grade_bare_string(str(measure), test, reader, composite_module)

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
