"""Workspace bibliography (BibTeX) key extraction — single source of truth.

Before this, three call sites disagreed (framework streamlining screening,
Theme 3 / the one correctness finding):

- ``study_findings.load_bib_keys`` and ``report_linter._bib_keys_for_workspace``
  read ``references/papers.bib`` with a tolerant line parser.
- ``study_verify._load_bib_keys`` read a *different* file
  (``references/references.bib`` or ``references.bib``) with a *different*
  regex.

So a cite key could pass ``/viva-study verify`` and fail ``/viva-report`` lint
(or vice versa). This module gives all three one filename precedence and one
parser, so the verify gate and the publish gate agree.
"""
from __future__ import annotations

from pathlib import Path

# Canonical precedence. The workspace convention is ``references/papers.bib``;
# ``references/references.bib`` and a bare ``references.bib`` are accepted as
# fallbacks (study verify historically read these), so a workspace using any
# of them resolves consistently across verify and lint.
_BIB_CANDIDATES = (
    ("references", "papers.bib"),
    ("references", "references.bib"),
    ("references.bib",),
)


def bib_file(ws_root: Path) -> Path | None:
    """The workspace's bibliography file by precedence, or None if none exist.

    Honors the workspace.yaml ``layout.references`` map first — a nested-layout
    workspace keeps its bib at e.g. ``workspace/references/papers.bib`` (the flat
    default for ``layout`` is ``<ws_root>/references``, so this also covers the
    flat convention). Falls back to the historical flat candidates for
    back-compat / robustness if the layout can't be resolved.
    """
    # 1) layout-aware references dir (covers both nested and flat layouts).
    try:
        from .paths import workspace_dir
        ref_dir = Path(workspace_dir("references", root=ws_root))
        for fname in ("papers.bib", "references.bib"):
            p = ref_dir / fname
            if p.is_file():
                return p
    except Exception:  # noqa: BLE001 — layout unresolvable → flat fallback below
        pass
    # 2) flat-layout back-compat candidates.
    for parts in _BIB_CANDIDATES:
        p = ws_root.joinpath(*parts)
        if p.is_file():
            return p
    return None


def _parse_keys(text: str) -> set[str]:
    """Extract every ``@entry`` key with a tolerant line parser.

    Matches the long-standing ``load_bib_keys`` behavior: for a line like
    ``@article{smith2024, ...`` the key runs from ``{`` to the first ``,``
    (or whitespace / closing brace when there's no comma on the line).
    """
    keys: set[str] = set()
    for line in text.splitlines():
        s = line.strip()
        if not s.startswith("@") or "{" not in s:
            continue
        try:
            after_brace = s.split("{", 1)[1]
            key = after_brace.split(",", 1)[0].split()[0].rstrip("}").strip()
        except (IndexError, ValueError):
            continue
        if key:
            keys.add(key)
    return keys


def bib_keys(ws_root: Path, *, missing_ok: bool = False) -> set[str] | None:
    """Every cite key declared in the workspace's bibliography file.

    Returns an empty set when no bib file exists, unless ``missing_ok=True``,
    in which case it returns ``None`` — study verify's soft-skip contract
    ("no bib file at all → don't validate cites", vs the linter's "no file →
    every cite is unknown").
    """
    p = bib_file(ws_root)
    if p is None:
        return None if missing_ok else set()
    try:
        text = p.read_text(errors="ignore")
    except OSError:
        return None if missing_ok else set()
    return _parse_keys(text)
