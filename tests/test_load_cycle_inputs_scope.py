"""load_cycle_inputs must scope to an investigation's OWN member studies.

Root cause (v2ecoli, apply corrupted sibling investigations): load_cycle_inputs read the
pre-migration `studies:` field but not the post-migration `members:` field, and its disk fallback
returned the ENTIRE flat studies dir. So a members-based investigation pulled every workspace study
into its cycle — and `apply` then rewrote OTHER investigations' study.yaml files. The fix resolves
membership from `studies:` OR `members:` (dict/str shapes), then ac/at_a_glance, and only as a last
resort scans disk SCOPED by each study's `investigation:` back-ref — never the whole workspace.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import yaml

from discovery_engine.discover import load_cycle_inputs


def _ws(tmp_path: Path) -> Path:
    ws = tmp_path / "ws"
    (ws / "investigations").mkdir(parents=True)
    (ws / "studies").mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: scope-ws\n", encoding="utf-8")
    return ws


def _study(ws: Path, slug: str, investigation: str | None) -> None:
    d = ws / "studies" / slug
    d.mkdir(parents=True, exist_ok=True)
    spec: dict = {"schema_version": 4, "name": slug, "claim": f"{slug} claim"}
    if investigation is not None:
        spec["investigation"] = investigation
    (d / "study.yaml").write_text(yaml.safe_dump(spec, sort_keys=False), encoding="utf-8")


def _inv(ws: Path, slug: str, body: dict) -> None:
    d = ws / "investigations" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "investigation.yaml").write_text(yaml.safe_dump({"name": slug, **body}, sort_keys=False),
                                          encoding="utf-8")


def test_members_based_investigation_scopes_to_its_members(tmp_path):
    ws = _ws(tmp_path)
    for s in ("a1", "a2"):
        _study(ws, s, "inv-a")
    _study(ws, "b1", "inv-b")      # sibling investigation's study, flat
    _study(ws, "c1", "other")      # unrelated study, flat
    _inv(ws, "inv-a", {"members": ["a1", "a2"]})   # post-migration shape, NO top-level studies:

    _, _spec, studies = load_cycle_inputs(ws, "inv-a")
    assert set(studies) == {"a1", "a2"}            # NOT {a1,a2,b1,c1} — the bug


def test_studies_based_investigation_unchanged(tmp_path):
    ws = _ws(tmp_path)
    _study(ws, "b1", "inv-b")
    _study(ws, "c1", "other")
    _inv(ws, "inv-b", {"studies": ["b1"]})          # pre-migration shape still authoritative

    _, _spec, studies = load_cycle_inputs(ws, "inv-b")
    assert set(studies) == {"b1"}


def test_dict_shaped_members_are_normalized(tmp_path):
    ws = _ws(tmp_path)
    _study(ws, "a1", "inv-a")
    _study(ws, "c1", "other")
    _inv(ws, "inv-a", {"members": [{"study": "a1"}]})   # {study: ...} item shape

    _, _spec, studies = load_cycle_inputs(ws, "inv-a")
    assert set(studies) == {"a1"}


def test_undeclared_membership_falls_back_to_backref_not_whole_workspace(tmp_path):
    ws = _ws(tmp_path)
    _study(ws, "a1", "inv-a")       # back-refs inv-a
    _study(ws, "a2", "inv-a")       # back-refs inv-a
    _study(ws, "b1", "inv-b")       # belongs to a sibling — must be excluded
    _study(ws, "c1", None)          # no back-ref — must be excluded
    _inv(ws, "inv-a", {})           # neither studies: nor members:

    _, _spec, studies = load_cycle_inputs(ws, "inv-a")
    assert set(studies) == {"a1", "a2"}            # scoped by back-ref, NOT the whole workspace
