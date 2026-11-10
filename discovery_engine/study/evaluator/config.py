"""Config-selection (issue #98): read a run's DECLARED params from the study's
condition block — deterministic and backend-independent (no RunReader change).
"""
from __future__ import annotations

from typing import Any

from .common import _agent, _as_float, _code_outcome, _equals_ok, _op_supported


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


def _resolve_expected(pass_if: dict, config: dict | None) -> Any:
    """Expected value for an equals/comparison op: a `config:` reference (into
    the run's declared params) takes precedence over a literal `value:`."""
    if "config" in pass_if:
        return _config_lookup(config or {}, str(pass_if["config"]))
    return pass_if.get("value")


def _evaluate_config_value(test: dict, config: dict | None) -> dict:
    """Evaluate a ``kind: config_value`` measure against the run's declared params.

    No run data / windowing — the measured value is the config field itself, so
    this supports categorical (string) params that the numeric series path can't.
    """
    measure = test.get("measure")
    measure = measure if isinstance(measure, dict) else {}
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
