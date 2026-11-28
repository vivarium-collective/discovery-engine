"""Content-addressed claim identity — the seam for cross-investigation sharing.

Today the argument projection keys a Claim by its study slug (``claim:<slug>``),
which is investigation-local: the *same* proposition raised in two investigations
gets two different ids and is never recognized as the same claim. That is the
concrete reason the ecosystem has no cross-investigation knowledge today.

This module adds a stable, **workspace-scoped** key derived from the claim's
proposition (and scope), so identical propositions land on the same key wherever
they appear. It is **additive and read-only**: it does not touch the slug-keyed
``claim:<slug>`` id the argument graph already uses — it stamps an extra
``content_key`` field.

Matching is text-normalized and is a **candidate signal for review**, never an
automatic merge. Per the identity policy in the update plan: a proposition or
scope change is a *new* claim, and nothing is deduplicated by text alone — a human
confirms that two studies assert the same claim.
"""
from __future__ import annotations

import hashlib
import re

_WS = re.compile(r"\s+")
_STRIP = " \t\n\"'.,;:!?—–-"


def normalize_proposition(text: str | None) -> str:
    """The normal form two authors of the same claim would still share: lowercased,
    whitespace collapsed, surrounding quotes and trailing punctuation stripped."""
    if not text:
        return ""
    t = _WS.sub(" ", str(text).strip().lower())
    return t.strip(_STRIP)


def claim_key(proposition: str | None, scope: str | None = None) -> str | None:
    """A content-addressed key ``clm-<sha1[:12]>`` over the normalized proposition
    (plus normalized scope, so the same text at a different scope is a different
    claim). Returns ``None`` for an empty / stub proposition — never a key over
    nothing."""
    norm = normalize_proposition(proposition)
    if not norm:
        return None
    basis = norm + "\x1f" + normalize_proposition(scope)
    return "clm-" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:12]


def stamp_claim_keys(argument_graph: dict) -> dict:
    """Return a copy of an argument graph (``{"claims": {...}, "edges": [...]}``)
    with a ``content_key`` on every claim — ``None`` for a stub. The ``claims`` dict
    keys (``claim:<slug>``) and every other field are left untouched."""
    claims = argument_graph.get("claims") or {}
    stamped: dict = {}
    for cid, claim in claims.items():
        c = dict(claim)
        c["content_key"] = (
            None
            if claim.get("proposition_stub")
            else claim_key(claim.get("proposition"), claim.get("scope"))
        )
        stamped[cid] = c
    out = dict(argument_graph)
    out["claims"] = stamped
    return out
