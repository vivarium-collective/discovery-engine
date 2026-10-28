"""Cross-iteration diff of two card-maps ({card_name: verdict_doc}).

The iteration signal a model-building agent reads: for each keyed axis
(card, group, id) it reports fixed/broke/improved/regressed/new/gone/unchanged
plus the signed margin delta. Pure stdlib.
"""
from __future__ import annotations

from discovery_engine.test_vocab import normalize_verdict

_CHANGES = ("fixed", "broke", "improved", "regressed", "new", "gone", "unchanged")


def _index(card_map):
    """(card, group, id) -> {'verdict','margin'} over a {card: verdict_doc} map."""
    out = {}
    for card, doc in (card_map or {}).items():
        for gslug, grp in (doc.get("groups") or {}).items():
            for ax in grp.get("axes") or []:
                out[(card, gslug, ax.get("id"))] = {
                    "verdict": normalize_verdict(ax.get("verdict")),
                    "margin": ax.get("margin"),
                }
    return out


def _classify(prev, curr):
    if prev is None:
        return "new"
    if curr is None:
        return "gone"
    pv, cv = prev["verdict"], curr["verdict"]
    if pv == "mismatch" and cv == "within_tol":
        return "fixed"
    if pv == "within_tol" and cv == "mismatch":
        return "broke"
    pm, cm = prev["margin"], curr["margin"]
    if isinstance(pm, (int, float)) and isinstance(cm, (int, float)):
        if cm > pm:
            return "improved"
        if cm < pm:
            return "regressed"
    return "unchanged"


def diff_reports(prev: dict, curr: dict) -> dict:
    pi, ci = _index(prev), _index(curr)
    rollup = {k: 0 for k in _CHANGES}
    per = []
    for key in sorted(set(pi) | set(ci),
                      key=lambda k: tuple("" if x is None else str(x) for x in k)):
        p, c = pi.get(key), ci.get(key)
        change = _classify(p, c)
        rollup[change] += 1
        md = None
        if p and c and isinstance(p["margin"], (int, float)) and isinstance(c["margin"], (int, float)):
            md = c["margin"] - p["margin"]
        card, group, aid = key
        per.append({"card": card, "group": group, "id": aid,
                    "prev": p["verdict"] if p else None,
                    "curr": c["verdict"] if c else None,
                    "change": change, "margin_delta": md})
    return {"schema": "test_diff/v1", "per": per, "rollup": rollup}
