"""readout_resolver.py — Normalize study readout dicts to canonical selectors.

Normalizes on-read; never rewrites user study.yaml files.

Three source dialects recognised (priority order):
  1. ``index_by`` dict (canonical) — passed through directly.
  2. ``identifier`` string — dnaa-style free-text: path[int], bare dotted
     path, ``bulk <id>`` or ``bulk.<id>``, arithmetic expression over bulk ids.
  3. ``store_path`` string — legacy: ``bulk.ID``, listener path, ``derived``.

Ambiguous/prose readouts are flagged as ``UnresolvedReadout`` (never-guess).

Public API:
    resolve_readout(readout: dict) -> ResolvedReadout | UnresolvedReadout
    resolve_study_readouts(spec: dict) -> dict[str, ResolvedReadout | UnresolvedReadout]

The id-tokenizer is shared with ``study_evaluator`` (same grammar, no fork):
    _BRACKET_ID, _DOTTED_IDENT, _extract_observable_tokens
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# Reuse the id-tokenizer from study_evaluator — same grammar, no fork.
# study_evaluator._extract_observable_tokens finds all identifier tokens in
# an expression string (BRACKET_ID first, then dotted idents, unique ordered).
# ---------------------------------------------------------------------------
from discovery_engine.study_evaluator import (
    _BRACKET_ID,               # [A-Z][A-Z0-9_]*(?:-[A-Z0-9]+)*\[[a-z]+\]
    _DOTTED_IDENT,             # [A-Za-z_]\w*(?:\.\w+)*
    _extract_observable_tokens,  # -> list[str]  unique tokens in order
)


# ---------------------------------------------------------------------------
# Output dataclasses
# ---------------------------------------------------------------------------

@dataclass
class ResolvedReadout:
    """Canonical selector for a single study readout."""
    name: str
    kind: str                     # "scalar" | "element" | "expression"
    observable: str | None        # for scalar/element (e.g. "listeners.mass.cell_mass", "bulk")
    index_by: dict | None         # {type, value} for element kind
    expression: str | None        # arithmetic expression string (expression kind)
    operand_ids: list[dict] | None  # [{token, index_by}] for expression (enables #6 to select each)
    aggregate: dict | None        # only when explicitly authored; never inferred
    units: str | None
    source_dialect: str           # "index_by" | "identifier" | "store_path"
    notes: str | None = None      # stripped qualifier text or parse notes

    def to_select_dict(self) -> dict | None:
        """Return a dict suitable to pass directly to ``RunReader.select(index_by=...)``.

        ``RunReader.select`` requires ``"observable"`` inside *index_by* for
        ``literal_index`` and ``listener_id`` types (and it is harmless for
        catalog-backed types like ``bulk_id``).  This method merges
        ``self.observable`` into a copy of ``self.index_by`` so the caller can
        pass the result straight to ``select`` without manually composing the
        dict.

        Returns:
            ``{**self.index_by, "observable": self.observable}`` when
            ``kind == "element"`` and ``index_by`` is present; ``None``
            otherwise (scalar and expression kinds are not element selectors).
        """
        if self.kind != "element" or self.index_by is None:
            return None
        if self.observable is None:
            return dict(self.index_by)
        return {**self.index_by, "observable": self.observable}


@dataclass
class UnresolvedReadout:
    """A readout that could not be normalised without guessing."""
    name: str
    raw: str
    reason: str                   # human-readable explanation


# ---------------------------------------------------------------------------
# Internal: arithmetic detection helper
# ---------------------------------------------------------------------------

def _has_arithmetic(s: str) -> bool:
    """Return True if *s* contains arithmetic operators outside identifier tokens.

    Strategy: remove all BRACKET_ID spans, then all DOTTED_IDENT spans, then
    check for +, -, *, / in the remainder.  IDs' internal hyphens (e.g.
    MONOMER0-160) are consumed by _BRACKET_ID so any remaining '-' is
    arithmetic subtraction.

    This uses the same id grammar as study_evaluator (_BRACKET_ID + _DOTTED_IDENT)
    so that evaluator #6 can process a resolved expression identically to how
    it processes a behavior_test measure.path — both go through
    _extract_observable_tokens.
    """
    reduced = _BRACKET_ID.sub("", s)
    reduced = _DOTTED_IDENT.sub("", reduced)
    return bool(re.search(r"[+\-*/]", reduced))


def _build_bulk_operand_ids(tokens: list[str]) -> list[dict]:
    """Build operand_ids for a bulk expression.

    Bracket-style IDs (e.g. MONOMER0-160[c]) are tagged ``bulk_id``.
    Dotted-path tokens (e.g. ``listeners.mass.cell_mass``) are tagged
    ``scalar`` so evaluator #6 resolves them via ``series(token)`` rather
    than the bulk catalog — matching how the non-bulk expression path tags
    dotted operands.  Nothing is silently dropped.
    """
    result = []
    for t in tokens:
        if _BRACKET_ID.fullmatch(t):
            result.append({"token": t, "index_by": {"type": "bulk_id", "value": t}})
        else:
            result.append({"token": t, "index_by": {"type": "scalar", "value": t}})
    return result


# ---------------------------------------------------------------------------
# Internal: parse a bare `identifier:` string
# ---------------------------------------------------------------------------

_LITERAL_INDEX_RE = re.compile(
    r"^([A-Za-z_]\w*(?:\.\w+)*)\[(\d+)\]$"
)
_TRAILING_PROSE_RE = re.compile(
    r"^([A-Za-z_]\w*(?:\.\w+)*)\s+\(([^)]+)\)$"
)


def _parse_identifier(
    name: str,
    raw: str,
    units: str | None,
    aggregate: dict | None,
) -> ResolvedReadout | UnresolvedReadout:
    """Parse an ``identifier:`` string into a canonical selector."""
    s = raw.strip()

    # Guard: empty
    if not s:
        return UnresolvedReadout(name=name, raw=raw, reason="empty identifier")

    # Guard: derived
    if s == "derived":
        return UnresolvedReadout(
            name=name, raw=raw,
            reason="derived: not a runtime-observable path; needs a bespoke computation"
        )

    # Guard: middle-dot separator → ambiguous multi-id group
    if "·" in s or " · " in s:
        return UnresolvedReadout(
            name=name, raw=raw,
            reason=(
                "multi-id · group: ambiguous aggregation — multiple bulk IDs "
                "separated by · cannot be resolved to a single selector without "
                "fabricating an aggregation operator"
            )
        )

    # --- Literal index: <dotted.path>[<integer>] -------------------------
    m = _LITERAL_INDEX_RE.match(s)
    if m:
        observable = m.group(1)
        index_val = int(m.group(2))
        return ResolvedReadout(
            name=name, kind="element", observable=observable,
            index_by={"type": "literal_index", "value": index_val},
            expression=None, operand_ids=None, aggregate=aggregate,
            units=units, source_dialect="identifier",
        )

    # --- Trailing prose qualifier: <dotted.path> (prose) -----------------
    m = _TRAILING_PROSE_RE.match(s)
    if m:
        path = m.group(1)
        qualifier = m.group(2)
        # Strip only if qualifier has no commas and no bracket-style IDs
        # (those would imply the qualifier is a selector, not an annotation).
        if "," not in qualifier and not _BRACKET_ID.search(qualifier):
            return ResolvedReadout(
                name=name, kind="scalar", observable=path,
                index_by=None, expression=None, operand_ids=None,
                aggregate=aggregate, units=units, source_dialect="identifier",
                notes=f"stripped qualifier: ({qualifier})",
            )
        # Qualifier is load-bearing or complex → Unresolved
        return UnresolvedReadout(
            name=name, raw=raw,
            reason=(
                f"ambiguous or load-bearing parenthetical: ({qualifier!r}) — "
                "contains commas or bracket IDs that suggest an element selector; "
                "resolve by providing an explicit index_by block"
            )
        )

    # --- Detect leading `bulk ` prefix -----------------------------------
    is_bulk = s.startswith("bulk ")
    remaining = s[5:].strip() if is_bulk else s

    # --- Guard: comma-separated multi-id (e.g. bulk__count[A[c], B[c]]) -
    reduced_after_brackets = _BRACKET_ID.sub("", remaining)
    if "," in reduced_after_brackets:
        return UnresolvedReadout(
            name=name, raw=raw,
            reason=(
                "comma-separated multi-id form: contains multiple IDs "
                "separated by commas — cannot resolve to a single selector"
            )
        )

    # --- Arithmetic expression? -----------------------------------------
    # Detected via _has_arithmetic which uses the same id grammar as
    # study_evaluator, ensuring expression tokens are evaluated identically
    # by evaluator #6 (via _extract_observable_tokens / _eval_expression).
    if _has_arithmetic(remaining):
        tokens = _extract_observable_tokens(remaining)
        if is_bulk:
            operand_ids = _build_bulk_operand_ids(tokens)
        else:
            # Non-bulk expression: tag bracket-IDs as bulk_id, dotted paths as scalar
            operand_ids = []
            for t in tokens:
                if _BRACKET_ID.fullmatch(t):
                    operand_ids.append(
                        {"token": t, "index_by": {"type": "bulk_id", "value": t}}
                    )
                else:
                    operand_ids.append(
                        {"token": t, "index_by": {"type": "scalar", "value": t}}
                    )

        return ResolvedReadout(
            name=name, kind="expression",
            observable="bulk" if is_bulk else None,
            index_by=None, expression=remaining,
            operand_ids=operand_ids or None,
            aggregate=aggregate, units=units, source_dialect="identifier",
        )

    # --- Single bulk ID (no arithmetic) -----------------------------------
    if is_bulk:
        m2 = _BRACKET_ID.fullmatch(remaining)
        if m2:
            return ResolvedReadout(
                name=name, kind="element", observable="bulk",
                index_by={"type": "bulk_id", "value": remaining},
                expression=None, operand_ids=None, aggregate=aggregate,
                units=units, source_dialect="identifier",
            )
        return UnresolvedReadout(
            name=name, raw=raw,
            reason=(
                f"bulk prefix but remaining {remaining!r} is not a single "
                "bracket-style bulk ID (e.g. MONOMER0-160[c])"
            )
        )

    # --- Bare dotted path → scalar ----------------------------------------
    if re.match(r"^[A-Za-z_]\w*(?:\.\w+)*$", s):
        return ResolvedReadout(
            name=name, kind="scalar", observable=s,
            index_by=None, expression=None, operand_ids=None,
            aggregate=aggregate, units=units, source_dialect="identifier",
        )

    # --- Fallback: unresolved --------------------------------------------
    return UnresolvedReadout(
        name=name, raw=raw,
        reason=f"could not parse identifier {s!r} into a recognised dialect"
    )


# ---------------------------------------------------------------------------
# Internal: parse a bare `store_path:` string
# ---------------------------------------------------------------------------

def _parse_store_path(
    name: str,
    raw: str,
    units: str | None,
    aggregate: dict | None,
) -> ResolvedReadout | UnresolvedReadout:
    """Parse a ``store_path:`` string into a canonical selector."""
    s = raw.strip()

    if not s:
        return UnresolvedReadout(name=name, raw=raw, reason="empty store_path")

    if s == "derived":
        return UnresolvedReadout(
            name=name, raw=raw,
            reason="derived: not a runtime-observable path; needs a bespoke computation"
        )

    # bulk.ID → element, bulk_id
    if s.startswith("bulk."):
        bulk_id_val = s[5:]
        if bulk_id_val:
            return ResolvedReadout(
                name=name, kind="element", observable="bulk",
                index_by={"type": "bulk_id", "value": bulk_id_val},
                expression=None, operand_ids=None, aggregate=aggregate,
                units=units, source_dialect="store_path",
            )
        return UnresolvedReadout(
            name=name, raw=raw,
            reason="store_path 'bulk.' prefix with no ID after it"
        )

    # Bare dotted path → scalar
    if re.match(r"^[A-Za-z_]\w*(?:\.\w+)*$", s):
        return ResolvedReadout(
            name=name, kind="scalar", observable=s,
            index_by=None, expression=None, operand_ids=None,
            aggregate=aggregate, units=units, source_dialect="store_path",
        )

    return UnresolvedReadout(
        name=name, raw=raw,
        reason=f"could not parse store_path {s!r} into a recognised dialect"
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_readout(readout: dict) -> ResolvedReadout | UnresolvedReadout:
    """Normalise a single readout dict to a canonical selector.

    Priority:
      1. ``index_by`` present → canonical passthrough (element).
      2. ``identifier`` present → parse as free-text identifier string.
      3. ``store_path`` present → parse as store-path string.
      4. Neither → UnresolvedReadout.

    Does NOT modify ``readout``; always returns a new object.
    """
    name: str = readout.get("name", "")
    units: str | None = readout.get("units")
    aggregate: dict | None = readout.get("aggregate")  # only from authored dict

    # 1. Canonical index_by passthrough
    if "index_by" in readout:
        store_path = readout.get("store_path")
        return ResolvedReadout(
            name=name, kind="element",
            observable=store_path,
            index_by=readout["index_by"],
            expression=None, operand_ids=None,
            aggregate=aggregate, units=units,
            source_dialect="index_by",
        )

    # 2. identifier string
    if "identifier" in readout:
        return _parse_identifier(name, str(readout["identifier"]), units, aggregate)

    # 3. store_path string
    if "store_path" in readout:
        return _parse_store_path(name, str(readout["store_path"]), units, aggregate)

    # 4. No recognised field
    return UnresolvedReadout(
        name=name,
        raw=str(readout),
        reason="no identifier, store_path, or index_by field found in readout dict",
    )


def resolve_study_readouts(
    spec: dict,
) -> dict[str, ResolvedReadout | UnresolvedReadout]:
    """Normalise all readouts in a study spec dict.

    Never-guess policy: readouts that are ambiguous, prose-laden, use the
    middle-dot (·) multi-id group syntax, or contain comma-separated IDs
    inside brackets are returned as ``UnresolvedReadout`` with a clear reason.
    The evaluator (#6) and RunReader (#2) must treat UnresolvedReadout entries
    as opaque and skip automated evaluation.

    This function is the primary entry point for study-level normalisation;
    it does NOT rewrite the study.yaml file.

    Args:
        spec: Parsed study.yaml dict; reads ``spec["readouts"]`` (list).

    Returns:
        Mapping from readout name → ResolvedReadout or UnresolvedReadout.
        Empty dict when the spec has no ``readouts`` key or an empty list.
    """
    readouts: list[dict] = spec.get("readouts") or []
    result: dict[str, ResolvedReadout | UnresolvedReadout] = {}
    for i, ro in enumerate(readouts):
        # Studies may author readouts as bare prose strings rather than
        # structured dicts. Per the never-guess policy above, treat those as
        # UnresolvedReadout instead of crashing on ``str.get`` — a bare string
        # has no identifier/store_path/index_by field to resolve.
        if isinstance(ro, str):
            result[ro] = UnresolvedReadout(
                name=ro,
                raw=ro,
                reason="prose readout authored as a string, not a structured readout dict",
            )
            continue
        name = ro.get("name", f"readout_{i}")
        result[name] = resolve_readout(ro)
    return result
