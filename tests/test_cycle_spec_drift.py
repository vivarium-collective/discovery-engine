"""Drift-check for the canonical cycle map data (``docs/cycle-spec.json``).

The interactive map (``docs/cycle-map.html``) and the PDF map
(``docs/discovery-engine-cycle-map.tex``) render from this single source of
truth. These tests are the "connected to the engine" guarantee: they fail when
the map names a function the engine no longer exports, when its internal
structure is inconsistent, or when the copy embedded in the HTML drifts from the
canonical JSON. A green suite means the published map cannot silently go stale.
"""
from __future__ import annotations

import importlib
import json
import re
from pathlib import Path

import pytest

_DOCS = Path(__file__).resolve().parent.parent / "docs"
_SPEC_PATH = _DOCS / "cycle-spec.json"
_HTML_PATH = _DOCS / "cycle-map.html"

# Current viva plugin skills (viva-superpowers/skills/, 0.29 — Phase-2 renames:
# investigation→investigate, fill→reason, expert→model,
# harden-investigation→validate, navigate→explore, drive-engine→discover). Kept
# here because the engine CI cannot import the sibling plugin repo. UPDATE IN
# LOCKSTEP when a skill is added/removed/renamed.
_KNOWN_SKILLS = {
    "apply-cli-verb", "benchmark", "catalog", "consolidate", "discover",
    "model", "reason", "validate", "init", "investigate",
    "model-build", "explore", "orient", "remote-run", "report", "run",
    "study", "suggest", "tests", "viz", "workbench", "workspace",
}


@pytest.fixture(scope="module")
def spec() -> dict:
    return json.loads(_SPEC_PATH.read_text(encoding="utf-8"))


def _dotted(module_path: str) -> str:
    """``discover/cycle.py`` -> ``discovery_engine.discover.cycle``."""
    return "discovery_engine." + module_path[:-3].replace("/", ".")


def test_spec_is_valid_json(spec):
    assert spec["version"]
    assert len(spec["nodes"]) >= 1
    assert spec["public_cycle"] == ["Question", "Hypothesize", "Model",
                                    "Experiment", "Evaluate", "Synthesize"]


def test_every_referenced_function_exists_in_the_engine(spec):
    """The core engine-connection: each node fn resolves to a real symbol."""
    missing = []
    for node in spec["nodes"]:
        for fn, module_path in node["fns"]:
            dotted = _dotted(module_path)
            try:
                mod = importlib.import_module(dotted)
            except Exception as exc:  # noqa: BLE001
                missing.append(f"{node['id']}: cannot import {dotted} ({exc!r})")
                continue
            if not hasattr(mod, fn):
                missing.append(f"{node['id']}: {fn}() not found in {dotted}")
    assert not missing, "cycle-spec references functions the engine no longer has:\n" + "\n".join(missing)


def test_every_referenced_skill_is_a_real_plugin_skill(spec):
    unknown = sorted({sk for node in spec["nodes"] for sk in node.get("skills", [])
                      if sk not in _KNOWN_SKILLS})
    assert not unknown, (
        f"cycle-spec references skills not in the viva plugin: {unknown}. "
        "Fix the spec, or update _KNOWN_SKILLS if the plugin changed.")


def test_node_ids_cover_cycle_order_exactly(spec):
    node_ids = sorted(n["id"] for n in spec["nodes"])
    assert node_ids == sorted(spec["cycle_order"]), "cycle_order must list every node exactly once"
    assert len(set(spec["cycle_order"])) == len(spec["cycle_order"]), "cycle_order has duplicates"


def test_stage_membership_is_consistent(spec):
    node_ids = {n["id"] for n in spec["nodes"]}
    stage_nodes = [nid for s in spec["stages"] for nid in s["nodes"]]
    assert sorted(stage_nodes) == sorted(node_ids), (
        f"stages[].nodes must partition the node set; symmetric diff: "
        f"{node_ids ^ set(stage_nodes)}")
    assert len(stage_nodes) == len(set(stage_nodes)), "a node is listed under two stages"
    by_stage = {s["id"]: set(s["nodes"]) for s in spec["stages"]}
    for node in spec["nodes"]:
        assert node["id"] in by_stage.get(node["stage"], set()), (
            f"{node['id']}.stage={node['stage']!r} disagrees with stages[]")


def test_six_public_stages(spec):
    names = [s["name"] for s in spec["stages"]]
    assert names == spec["public_cycle"], "the six stages must match public_cycle in order"


def test_services_reference_real_nodes(spec):
    node_ids = {n["id"] for n in spec["nodes"]}
    for svc in spec["services"]:
        bad = set(svc["nodes"]) - node_ids
        assert not bad, f"service {svc['name']} references unknown nodes {bad}"


def test_html_embeds_the_canonical_spec(spec):
    """The HTML must carry the canonical JSON verbatim in its data block, so the
    published map cannot drift from docs/cycle-spec.json. Regenerate with
    docs/build_cycle_map.py if this fails."""
    html = _HTML_PATH.read_text(encoding="utf-8")
    m = re.search(r'<script[^>]*id="cycle-spec"[^>]*>(.*?)</script>', html, re.DOTALL)
    assert m, "cycle-map.html is missing its <script id=\"cycle-spec\"> data block"
    embedded = json.loads(m.group(1))
    assert embedded == spec, (
        "the spec embedded in cycle-map.html is stale — run docs/build_cycle_map.py")
