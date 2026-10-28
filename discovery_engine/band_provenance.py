"""Helper for identifying band-bearing study entries that lack structured provenance.

``bands_missing_provenance(spec)`` is the primary entry-point for stage 3b's
agent skill and the report renderer — it surfaces every acceptance band that
carries no machine-linked ``cites`` bib_key, so 3b can prompt the author to
fill them in.

``set_band_provenance(study_dir, test_name, cites, calibration_anchor=None)``
is the deterministic write helper the /viva-tests cite-bands skill calls after the
agent judges the correct source.  It uses a ruamel comment-preserving
round-trip (mirrors ``simulation_set._write_simset_preserving_comments``).

Covered entry kinds:
- ``behavior_tests[]`` — detect via numeric ``pass_if.low``/``high``/``threshold``
  or ``calibration_anchor.literature_target``
- ``tests[]`` — same detection (the v4 per-test array shape)
- ``readouts[]`` — detect via a prose band notation ``[N, M]`` in ``notes``
  (deterministic regex, no AI)

A test/readout is "band-bearing AND missing cites" when:
1. It carries a quantitative acceptance criterion (band or literature_target), AND
2. It has no ``cites: [...]`` field (or the list is empty).
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

# Regex for prose band notation in readout notes: "[0.2, 0.5]" or "[300, 800]".
_PROSE_BAND_RE = re.compile(r"\[(\d+\.?\d*),\s*(\d+\.?\d*)\]")


def _band_from_pass_if(pass_if: Any) -> dict | None:
    """Extract a structured band dict from a pass_if block.

    Returns ``{"low": N, "high": M}`` / ``{"threshold": T}`` when the block
    carries numeric bounds; ``None`` when no band is detectable.

    Note: does NOT include ``pass_if.value`` (the comparator-op threshold,
    e.g. ``{op: "<", value: 0.05}``) — callers that need the divergence-
    arithmetic band keep using ``low``/``high``/``threshold`` only, since
    ``value`` sits on a different (unsigned comparator) shape. Use
    :func:`has_numeric_band` for the presence check, which DOES count a
    numeric ``value`` as a band (see its docstring for why).
    """
    if not isinstance(pass_if, dict):
        return None
    band: dict = {}
    if isinstance(pass_if.get("low"), (int, float)):
        band["low"] = pass_if["low"]
    if isinstance(pass_if.get("high"), (int, float)):
        band["high"] = pass_if["high"]
    if isinstance(pass_if.get("threshold"), (int, float)):
        band["threshold"] = pass_if["threshold"]
    return band if band else None


def _is_numeric_scalar(value: Any) -> bool:
    """True for plain int/float, excluding bool (bool is an int subclass)."""
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ---------------------------------------------------------------------------
# Canonical band detector — single source of truth (was reimplemented 3x:
# here, rigor.py, and report_linter.py, and they disagreed on pass_if.value).
# ---------------------------------------------------------------------------

def has_numeric_band(test: Any) -> bool:
    """True when *test* carries a quantitative acceptance band.

    A test "has a band" when its ``pass_if`` block contains a numeric
    ``low`` / ``high`` / ``threshold`` / ``value`` key, OR its
    ``calibration_anchor`` block carries a ``literature_target``.

    ``pass_if.value`` reconciliation: the closed ``pass_if`` op set (see
    ``docs/concepts/expected-behavior-grammar.md``) uses ``value`` two ways —
    (1) a numeric threshold for the scalar comparator ops (``<=``, ``>=``,
    ``<``, ``>``, ``==``, ``!=``, ``eq``), e.g. ``{op: "<", value: 0.05}``,
    which IS a quantitative acceptance band; and (2) a non-numeric
    config-selector, e.g. ``{op: equals, value: "wells-riley"}``, which is
    not. Gating on "is ``value`` a plain number" (mirroring the existing
    ``low``/``high``/``threshold`` numeric checks) separates the two without
    needing to enumerate the comparator op set — a non-numeric ``value`` is
    never a quantitative band regardless of ``op``.
    """
    if not isinstance(test, dict):
        return False
    pass_if = test.get("pass_if")
    if isinstance(pass_if, dict):
        for k in ("low", "high", "threshold", "value"):
            if _is_numeric_scalar(pass_if.get(k)):
                return True
    anch = test.get("calibration_anchor")
    if isinstance(anch, dict) and anch.get("literature_target") is not None:
        return True
    return False


def numeric_band_of(test: Any) -> dict | None:
    """Return the full band dict backing :func:`has_numeric_band`, or ``None``.

    Superset of :func:`_band_from_pass_if` — also folds in a numeric
    ``pass_if.value`` (as ``{"value": V}``) and, when no ``pass_if`` band is
    present, ``calibration_anchor.literature_target`` (as
    ``{"literature_target": L}``). Priority mirrors ``has_numeric_band``:
    pass_if bounds first, literature_target as the fallback.
    """
    if not has_numeric_band(test):
        return None
    pass_if = test.get("pass_if") if isinstance(test, dict) else None
    band = _band_from_pass_if(pass_if) or {}
    if isinstance(pass_if, dict):
        v = pass_if.get("value")
        if _is_numeric_scalar(v):
            band = {**band, "value": v}
    if not band:
        anch = test.get("calibration_anchor") if isinstance(test, dict) else None
        if isinstance(anch, dict) and anch.get("literature_target") is not None:
            band = {"literature_target": anch["literature_target"]}
    return band or None


def _has_cites(entry: dict) -> bool:
    """True iff the entry carries at least one cite key."""
    cites = entry.get("cites")
    return bool(cites) and isinstance(cites, list)


def bands_missing_provenance(spec: dict) -> list[dict]:
    """Return a list of ``{name, kind, band, field_path}`` dicts.

    Each dict describes a band-bearing entry (behavior_test, test, or readout)
    that has no ``cites`` field — i.e. its acceptance band has no machine-linked
    source in ``references/papers.bib``.

    Parameters
    ----------
    spec:
        Parsed study.yaml dict.

    Returns
    -------
    list[dict]
        Each entry has:
        - ``name`` (str): test/readout name.
        - ``kind`` (str): ``"behavior_test"``, ``"test"``, or ``"readout"``.
        - ``band`` (dict): numeric bounds extracted from ``pass_if`` or prose
          (e.g. ``{"low": 0.2, "high": 0.5}``).
        - ``field_path`` (str): stable YAML path (e.g. ``"behavior_tests[0]"``).
    """
    if not isinstance(spec, dict):
        return []

    result: list[dict] = []

    # --- behavior_tests[] and tests[] ----------------------------------------
    section_kinds = [
        ("behavior_tests", "behavior_test"),
        ("tests", "test"),
    ]
    for section, kind in section_kinds:
        items = spec.get(section) or []
        if not isinstance(items, list):
            continue
        for idx, test in enumerate(items):
            if not isinstance(test, dict):
                continue

            # 1. Detect the band (canonical detector — also catches a numeric
            #    pass_if.value comparator band, e.g. {op: "<", value: 0.05}).
            band = numeric_band_of(test)

            if not band:
                continue  # not a band test — skip

            # 3. Check cites.
            if _has_cites(test):
                continue  # has at least one cite — not missing provenance

            result.append({
                "name": test.get("name", f"<index-{idx}>"),
                "kind": kind,
                "band": band,
                "field_path": f"{section}[{idx}]",
            })

    # --- readouts[] ----------------------------------------------------------
    readouts = spec.get("readouts") or []
    if isinstance(readouts, list):
        for idx, ro in enumerate(readouts):
            if not isinstance(ro, dict):
                continue

            # Detect a prose band notation in the readout's notes string.
            notes = ro.get("notes") or ""
            if not isinstance(notes, str):
                notes = str(notes)
            m = _PROSE_BAND_RE.search(notes)
            if not m:
                continue  # no prose band detected — skip

            band = {"low": float(m.group(1)), "high": float(m.group(2))}

            # Check cites.
            if _has_cites(ro):
                continue  # already cited

            result.append({
                "name": ro.get("name", f"<index-{idx}>"),
                "kind": "readout",
                "band": band,
                "field_path": f"readouts[{idx}]",
            })

    return result


# ---------------------------------------------------------------------------
# set_band_provenance — deterministic write helper (stage 3b)
# ---------------------------------------------------------------------------


def set_band_provenance(
    study_dir: Path | str,
    test_name: str,
    cites: list[str],
    calibration_anchor: dict | None = None,
) -> bool:
    """Write ``cites`` (and optionally ``calibration_anchor``) onto a named test entry.

    Finds the entry by ``name`` in ``behavior_tests[]`` then ``tests[]``.  Never
    fabricates an entry — returns ``False`` immediately if the name is not found.

    Uses a ruamel comment-preserving round-trip (``YAML(); preserve_quotes=True;
    width=4096``), so comments on untouched entries survive byte-identical.
    Only the targeted entry is modified; all other entries and the rest of the
    document are left untouched.

    Idempotent: a second call with identical arguments returns ``False`` and
    does NOT write the file again.

    Parameters
    ----------
    study_dir:
        Directory containing ``study.yaml``.
    test_name:
        The ``name`` field of the behavior_test / test to update.
    cites:
        List of bib_keys to set.  Merged (dedup, order-preserved) into any
        existing ``cites`` list — never clobbers it.
    calibration_anchor:
        Optional dict to set as the ``calibration_anchor`` key.  Only written
        when provided; existing value is compared and only replaced when different.

    Returns
    -------
    bool
        ``True`` if the file was written; ``False`` if the entry was not found
        or nothing changed (idempotent).
    """
    from io import StringIO

    from ruamel.yaml import YAML

    from . import study_io

    study_dir = Path(study_dir)
    study_yaml = study_dir / "study.yaml"

    ryaml = YAML()
    ryaml.preserve_quotes = True
    ryaml.width = 4096  # avoid line-wrap reflow on long prose values

    original_text = study_yaml.read_text(encoding="utf-8")
    rt_spec = ryaml.load(original_text)
    if rt_spec is None:
        return False

    # Find the target entry: behavior_tests first, then tests.
    target = None
    for section in ("behavior_tests", "tests"):
        items = rt_spec.get(section) or []
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("name") == test_name:
                target = item
                break
        if target is not None:
            break

    if target is None:
        return False  # Never fabricate an entry.

    changed = False

    # --- Merge cites (dedup, preserve order) ---------------------------------
    # Convert existing CommentedSeq (if any) to a plain list for comparison.
    existing_cites: list[str] = list(target.get("cites") or [])
    merged = list(existing_cites)
    for key in cites:
        if key not in merged:
            merged.append(key)

    if merged != existing_cites:
        target["cites"] = merged
        changed = True

    # --- Set calibration_anchor when provided --------------------------------
    if calibration_anchor is not None:
        existing_anchor = target.get("calibration_anchor")
        # CommentedMap inherits from OrderedDict; equality with plain dict works.
        if existing_anchor != calibration_anchor:
            target["calibration_anchor"] = calibration_anchor
            changed = True

    if not changed:
        return False

    buf = StringIO()
    ryaml.dump(rt_spec, buf)
    study_io.atomic_write(study_yaml, buf.getvalue())
    return True
