"""Small shared text helpers.

Extracted from the copies in ``report.py`` and ``seed_from_followup.py``
(framework streamlining screening, Theme 3).
"""
from __future__ import annotations

import re


def first_sentence(text: str, *, max_chars: int | None = None) -> str:
    """Return the first sentence (or first line) of ``text`` as a one-liner.

    Collapses internal whitespace, then splits on the first sentence
    terminator (``. ! ?``) followed by whitespace or end-of-string. When
    ``max_chars`` is given and the result is longer (e.g. terse prose with no
    terminator), it is truncated with an ellipsis. ``max_chars=None`` (the
    default) never truncates.
    """
    if not text:
        return ""
    flat = re.sub(r"\s+", " ", text.strip())
    m = re.search(r"(?<=[.!?])\s", flat)
    s = (flat[: m.start() + 1] if m else flat).rstrip()
    if max_chars is not None and len(s) > max_chars:
        s = s[: max_chars - 1].rstrip() + "…"
    return s
