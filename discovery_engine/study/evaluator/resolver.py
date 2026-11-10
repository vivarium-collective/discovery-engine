"""Path / expression resolver and window vocabulary.

Resolve an observable path or arithmetic expression to a series (via a
RunReader), evaluate multi-observable expressions over aligned series with a
safe AST evaluator, and apply the closed window vocabulary to a series.
"""
from __future__ import annotations

import ast
import re
from typing import TYPE_CHECKING, Any

import polars as pl

from .common import ObservableNotFound, WindowNotSupported

if TYPE_CHECKING:
    from viva_emitters import RunReader


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
    from discovery_engine.study.readout_resolver import ResolvedReadout, resolve_readout

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
