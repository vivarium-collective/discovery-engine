"""Shared YAML IO for study / investigation spec files.

One canonical load / dump / atomic-write, so the study_* and
investigation_close modules don't each carry a private copy (which had
silently drifted on ``safe_dump`` kwargs — ``width=1000`` in one writer,
``allow_unicode=True`` in another, for the *same* on-disk files). See the
framework streamlining screening, Theme 3.

- :func:`load_yaml` — lenient load (blank file -> ``{}``).
- :func:`load_yaml_mapping` — load + assert the top level is a mapping.
- :func:`dump_yaml` — the canonical serialization (insertion order, block
  style, unicode preserved, no line-wrapping).
- :func:`atomic_write` / :func:`save_yaml_atomic` — crash-safe overwrite.
"""
from __future__ import annotations

import copy
import os
from pathlib import Path

import yaml


# A per-process cache of parsed YAML, keyed by (absolute path, mtime_ns). A single
# investigation roll-up (e.g. acceptance via investigation_status) loads each member
# study.yaml many times; without this the dashboard re-parsed the same spec dozens of
# times per request. Safe because every writer in this module uses atomic_write (atomic
# rename → reliable mtime). A DEEP COPY is returned so callers may mutate freely, and an
# edit (mtime bump) re-parses automatically.
_YAML_CACHE: "dict[str, tuple[int, object]]" = {}


def _parse_yaml_cached(path: Path | str) -> object:
    """Parse ``path`` (cached by path+mtime); return a deep copy of the parsed value
    (dict / list / scalar / ``None``). Propagates the read error for a missing file,
    exactly as ``read_text`` did, so callers' error handling is unchanged."""
    p = Path(path)
    mtime = p.stat().st_mtime_ns            # raises (FileNotFoundError, …) like read_text did
    key = str(p)
    ent = _YAML_CACHE.get(key)
    if ent is None or ent[0] != mtime:
        ent = (mtime, yaml.safe_load(p.read_text(encoding="utf-8")))
        _YAML_CACHE[key] = ent
    return copy.deepcopy(ent[1])


def load_yaml(path: Path | str) -> dict:
    """Load a YAML file as a dict; an empty/blank file yields ``{}``.

    Lenient: does not assert the top level is a mapping (matches the old
    ``study_findings.load_study`` / ``study_verify._load_yaml`` behavior).
    """
    return _parse_yaml_cached(path) or {}


def load_yaml_mapping(path: Path | str) -> dict:
    """Like :func:`load_yaml`, but raise ``ValueError`` if the top level is
    not a mapping (matches the old ``study_narrative._load`` /
    ``investigation_close._load_yaml`` guard)."""
    data = _parse_yaml_cached(path) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path}: top-level YAML is not a mapping")
    return data


def dump_yaml(spec: dict) -> str:
    """Canonical spec serialization.

    ``sort_keys=False`` preserves authoring order; ``default_flow_style=False``
    keeps block style; ``allow_unicode=True`` writes characters verbatim
    instead of escaping; ``width=1000`` effectively disables line-wrapping so
    long values (paths, prose) stay on one line.
    """
    return yaml.safe_dump(
        spec,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=1000,
    )


def atomic_write(path: Path | str, text: str) -> None:
    """Write ``text`` to ``path`` via a tmp file + ``os.replace`` (atomic).

    Always UTF-8: specs carry em dashes etc., and the write must not depend on the
    process locale (an ASCII one raised UnicodeEncodeError mid-write). The tmp file
    is removed if the write fails, so a failure never leaves a stray file behind.
    """
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)
        _YAML_CACHE.pop(str(path), None)   # the parse cache is stale after a write
    except BaseException:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass  # never let cleanup mask the original error
        raise


def save_yaml_atomic(path: Path | str, data: dict) -> None:
    """:func:`dump_yaml` + :func:`atomic_write` — the common spec-write path."""
    atomic_write(path, dump_yaml(data))
