"""investigation_reconcile.py — detect + mechanically apply investigation drift
after a structural edit (studies removed / added / merged).

Deterministic / AI-free, mirroring the ``finding_observations.py`` split:
``detect_drift`` is pure (no writes); ``apply_mechanical`` drops STRUCTURED
references to removed studies via the comment-preserving ruamel round-trip and
leaves PROSE for the host agent to rewrite (guided by the ``/viva-investigation
reconcile`` skill section). Idempotent.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from discovery_engine.investigation_import import (
    investigations_dir,
    studies_dir,
    investigation_members,
)


@dataclass
class DriftItem:
    kind: str          # member_missing_on_disk | orphan_on_disk | prose_ref_to_removed | structured_ref_to_removed
    where: str         # dotted path / location
    detail: str
    suggested_action: str


def _inv_yaml(ws_root: Path, inv_slug: str) -> Path:
    return investigations_dir(ws_root) / inv_slug / "investigation.yaml"


def _declared_members(spec: dict, inv_yaml: Path) -> list[str]:
    """Union of every place a member is named: `members:` AND `studies:` (pre-canonical)
    AND the investigation_members() resolver (acceptance_criteria / at_a_glance)."""
    out: list[str] = []
    for key in ("members", "studies"):
        for x in (spec.get(key) or []):
            if isinstance(x, str):
                out.append(x)
            elif isinstance(x, dict) and x.get("study"):
                out.append(x["study"])
    try:
        out += investigation_members(inv_yaml)
    except Exception:  # noqa: BLE001 — resolver is best-effort; never fail detection
        pass
    seen, res = set(), []
    for s in out:
        if s not in seen:
            seen.add(s)
            res.append(s)
    return res


def _study_dir_exists(ws_root: Path, inv_slug: str, slug: str) -> bool:
    """A declared member is present if its study.yaml exists on disk — flat
    studies/<slug>/ or nested <inv>/studies/<slug>/ — regardless of any
    ``investigation:`` back-ref (older workspaces often omit it)."""
    flat = studies_dir(ws_root) / slug / "study.yaml"
    nested = investigations_dir(ws_root) / inv_slug / "studies" / slug / "study.yaml"
    return flat.is_file() or nested.is_file()


def _backref_members(ws_root: Path, inv_slug: str) -> set[str]:
    """Studies that CLAIM this investigation: nested <inv>/studies/<slug>/ or flat
    studies/<slug>/ whose study.yaml back-refs the inv. Used for ORPHAN detection
    (a study claiming the inv but not listed) + pruning surviving studies."""
    found: set[str] = set()
    nested = investigations_dir(ws_root) / inv_slug / "studies"
    if nested.is_dir():
        for p in nested.iterdir():
            if (p / "study.yaml").is_file():
                found.add(p.name)
    flat = studies_dir(ws_root)
    if flat.is_dir():
        for p in flat.iterdir():
            sy = p / "study.yaml"
            if not sy.is_file():
                continue
            try:
                d = yaml.safe_load(sy.read_text()) or {}
            except Exception:  # noqa: BLE001
                d = {}
            if d.get("investigation") == inv_slug:
                found.add(p.name)
    return found


def _texts(val):
    if isinstance(val, str):
        yield val
    elif isinstance(val, list):
        for x in val:
            if isinstance(x, str):
                yield x


def _at_a_glance_studies(aag) -> list[str]:
    if isinstance(aag, dict):
        return [x if isinstance(x, str) else (x or {}).get("study")
                for x in (aag.get("studies") or [])]
    if isinstance(aag, list):
        return [(x or {}).get("study") if isinstance(x, dict) else x for x in aag]
    return []


def detect_drift(ws_root, inv_slug: str) -> list[DriftItem]:
    """Deterministically detect drift between an investigation's declared members
    and what exists on disk, plus narrative references to removed studies. Pure."""
    ws_root = Path(ws_root)
    inv_yaml = _inv_yaml(ws_root, inv_slug)
    spec = yaml.safe_load(inv_yaml.read_text()) or {}

    declared = _declared_members(spec, inv_yaml)
    present = {s for s in declared if _study_dir_exists(ws_root, inv_slug, s)}
    removed = [s for s in declared if s not in present]
    orphan = sorted(_backref_members(ws_root, inv_slug) - set(declared))

    items: list[DriftItem] = []
    for s in removed:
        items.append(DriftItem("member_missing_on_disk", f"{inv_slug}.members",
                               f"declared member '{s}' has no study on disk (removed?)",
                               f"drop '{s}' from the member list, or restore the study"))
    for s in orphan:
        items.append(DriftItem("orphan_on_disk", f"studies/{s}",
                               f"study '{s}' exists on disk but is not a declared member",
                               f"add '{s}' to the member list, or remove the study"))

    sa = spec.get("scientific_argument") or {}
    ex = spec.get("executive") or {}
    prose_sources = {
        "executive.what_is_this": ex.get("what_is_this"),
        "executive.verdict": ex.get("verdict"),
        "biological_story": spec.get("biological_story"),
        "scientific_argument.evidence_for": sa.get("evidence_for"),
        "scientific_argument.evidence_against": sa.get("evidence_against"),
        "scientific_argument.key_figures": sa.get("key_figures"),
    }
    aag_studies = _at_a_glance_studies(spec.get("at_a_glance"))
    for s in removed:
        pat = re.compile(r"\b" + re.escape(s) + r"\b")
        for where, val in prose_sources.items():
            hit = any(pat.search(t) or (f"studies/{s}/" in t) for t in _texts(val))
            if hit:
                items.append(DriftItem("prose_ref_to_removed", where,
                                       f"text references removed study '{s}'",
                                       "confirm, then rewrite the prose to the surviving/new studies"))
        if s in aag_studies:
            items.append(DriftItem("structured_ref_to_removed", "at_a_glance",
                                   f"at_a_glance lists removed study '{s}'",
                                   f"drop '{s}' from at_a_glance"))
        for ac in (spec.get("acceptance_criteria") or []):
            if isinstance(ac, dict) and ac.get("study") == s:
                items.append(DriftItem("structured_ref_to_removed", "acceptance_criteria",
                                       f"acceptance_criteria references removed study '{s}'",
                                       "drop or repoint the criterion"))
    return items


def _study_yaml(ws_root: Path, inv_slug: str, slug: str) -> Path | None:
    flat = studies_dir(ws_root) / slug / "study.yaml"
    if flat.is_file():
        return flat
    nested = investigations_dir(ws_root) / inv_slug / "studies" / slug / "study.yaml"
    if nested.is_file():
        return nested
    return None


def _drop_from_list(container, key, removed) -> int:
    lst = container.get(key)
    if not isinstance(lst, list):
        return 0
    keep = [x for x in lst
            if not ((isinstance(x, str) and x in removed)
                    or (isinstance(x, dict) and x.get("study") in removed))]
    n = len(lst) - len(keep)
    if n:
        lst[:] = keep  # mutate in place to keep the ruamel container + comments
    return n


def apply_mechanical(ws_root, inv_slug: str, *, confirm: bool = True) -> dict:
    """Drop STRUCTURED references to removed studies (member lists, at_a_glance,
    acceptance_criteria, surviving studies' pipeline_gate.prerequisites) via a
    comment-preserving ruamel round-trip. Prose is left for the agent. Idempotent."""
    from discovery_engine.investigation_canonicalize import _ruamel

    ws_root = Path(ws_root)
    inv_yaml = _inv_yaml(ws_root, inv_slug)
    spec_plain = yaml.safe_load(inv_yaml.read_text()) or {}
    declared = _declared_members(spec_plain, inv_yaml)
    surviving = {s for s in declared if _study_dir_exists(ws_root, inv_slug, s)}
    removed = {s for s in declared if s not in surviving}
    skipped_prose = sum(1 for d in detect_drift(ws_root, inv_slug)
                        if d.kind == "prose_ref_to_removed")
    if not removed:
        return {"applied": 0, "skipped_prose": skipped_prose}

    y = _ruamel()
    with open(inv_yaml) as f:
        doc = y.load(f)
    applied = 0
    for key in ("members", "studies"):
        applied += _drop_from_list(doc, key, removed)
    aag = doc.get("at_a_glance")
    if isinstance(aag, dict):
        applied += _drop_from_list(aag, "studies", removed)
    elif isinstance(aag, list):
        keep = [x for x in aag
                if not ((isinstance(x, dict) and x.get("study") in removed)
                        or (isinstance(x, str) and x in removed))]
        if len(keep) != len(aag):
            applied += len(aag) - len(keep)
            aag[:] = keep
    ac = doc.get("acceptance_criteria")
    if isinstance(ac, list):
        keep = [c for c in ac if not (isinstance(c, dict) and c.get("study") in removed)]
        if len(keep) != len(ac):
            applied += len(ac) - len(keep)
            ac[:] = keep
    if applied:
        with open(inv_yaml, "w") as f:
            y.dump(doc, f)

    # prune dangling prerequisites in surviving study.yamls (comment-preserving)
    for slug in surviving:
        sy = _study_yaml(ws_root, inv_slug, slug)
        if not sy:
            continue
        with open(sy) as f:
            sd = y.load(f)
        pg = (sd or {}).get("pipeline_gate")
        if isinstance(pg, dict) and isinstance(pg.get("prerequisites"), list):
            pr = pg["prerequisites"]
            keep = [p for p in pr if p not in removed]
            if len(keep) != len(pr):
                applied += len(pr) - len(keep)
                pr[:] = keep
                with open(sy, "w") as f:
                    y.dump(sd, f)
    return {"applied": applied, "skipped_prose": skipped_prose}


def main(argv=None) -> int:
    import argparse
    import json as _json
    from collections import defaultdict
    from dataclasses import asdict

    ap = argparse.ArgumentParser(prog="python -m discovery_engine.investigation_reconcile",
                                 description="Detect (and optionally mechanically apply) investigation "
                                             "drift after a structural edit.")
    ap.add_argument("inv", help="investigation slug")
    ap.add_argument("--ws", default=".", help="workspace root (default: cwd)")
    ap.add_argument("--apply", action="store_true", help="drop structured refs to removed studies")
    ap.add_argument("--json", action="store_true", dest="as_json", help="emit the drift report as JSON")
    a = ap.parse_args(argv)

    drift = detect_drift(a.ws, a.inv)
    if a.as_json:
        print(_json.dumps([asdict(d) for d in drift], indent=2))
        return 0
    if not drift:
        print(f"No drift in investigation '{a.inv}'.")
    else:
        groups = defaultdict(list)
        for d in drift:
            groups[d.kind].append(d)
        for kind, items in groups.items():
            print(f"[{kind}] {len(items)}")
            for d in items:
                print(f"  - {d.where}: {d.detail}  -> {d.suggested_action}")
    if a.apply:
        rep = apply_mechanical(a.ws, a.inv)
        print(f"apply_mechanical: dropped {rep['applied']} structured ref(s); "
              f"{rep['skipped_prose']} prose ref(s) left for you to rewrite.")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
