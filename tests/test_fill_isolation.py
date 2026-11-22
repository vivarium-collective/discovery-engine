"""The isolation tool (discovery_engine/discover/fill_isolation.py) — reusable grounding +
recording wrappers that any driver composes around its filler seam. These pin the three
properties the engine relies on: resources ride in context (id unchanged), the transcript
records the FULLY-injected request, and every wrapper is identity when there is nothing to
inject (so a run without them is byte-identical)."""
from __future__ import annotations

import dataclasses
import json

from discovery_engine.discover.fill_isolation import (
    isolate_fill, load_resources, recording_fill, resource_fill,
)


@dataclasses.dataclass
class _Req:
    id: str
    kind: str
    target: str
    context: dict | None = None


RES = [{"name": "bg.md", "content": "known background"}]


# --- resource_fill: grounding rides in context, request id is unchanged ---------------------

def test_resource_fill_injects_resources_into_context():
    seen = {}
    def fill(req):
        seen["ctx"] = req.context
        return "ok"
    out = resource_fill(fill, RES)(_Req("i1", "explain", "t"))
    assert out == "ok"
    assert seen["ctx"]["resources"] == RES


def test_resource_fill_preserves_existing_context_and_request_id():
    captured = {}
    def fill(req):
        captured["req"] = req
        return None
    req = _Req("stable-id", "explain", "t", context={"goal": "G"})
    resource_fill(fill, RES)(req)
    # the goal survives, resources are added, and the content-addressed id is untouched
    assert captured["req"].context == {"goal": "G", "resources": RES}
    assert captured["req"].id == "stable-id"


def test_resource_fill_is_identity_without_resources():
    def fill(req):
        return req.context
    assert resource_fill(fill, []) is fill
    assert resource_fill(fill, None) is fill


# --- recording_fill: an auditable JSONL transcript of (request, result) ----------------------

def test_recording_fill_appends_request_and_result(tmp_path):
    t = tmp_path / "nested" / "transcript.jsonl"
    def fill(req):
        return {"request_id": req.id, "artifact": {"v": 1}}
    rec = recording_fill(fill, str(t))
    rec(_Req("i1", "explain", "t1"))
    rec(_Req("i2", "visualize", "t2"))
    lines = [json.loads(ln) for ln in t.read_text().splitlines() if ln.strip()]
    assert [e["request"]["id"] for e in lines] == ["i1", "i2"]
    assert lines[0]["result"]["artifact"] == {"v": 1}
    assert lines[0]["request"]["kind"] == "explain"


def test_recording_fill_is_identity_without_a_path():
    def fill(req):
        return 1
    assert recording_fill(fill, None) is fill
    assert recording_fill(fill, "") is fill


def test_recording_write_failure_never_blocks_the_fill(tmp_path):
    # point the transcript at a path whose parent is a FILE, so mkdir/open raise -> swallowed.
    clash = tmp_path / "afile"
    clash.write_text("x")
    rec = recording_fill(lambda req: "result", str(clash / "sub" / "t.jsonl"))
    assert rec(_Req("i1", "explain", "t")) == "result"   # fill result still returned


# --- isolate_fill: the transcript captures the fully-injected request ------------------------

def test_isolate_fill_records_the_resource_injected_request(tmp_path):
    t = tmp_path / "t.jsonl"
    seam = isolate_fill(lambda req: None, resources=RES, transcript=str(t))
    seam(_Req("i1", "explain", "t1"))
    entry = json.loads(t.read_text().splitlines()[0])
    # recording is innermost, so the request it logs already carries the injected resources
    assert entry["request"]["context"]["resources"] == RES


def test_isolate_fill_is_identity_when_both_empty():
    def fill(req):
        return req
    assert isolate_fill(fill) is fill
    assert isolate_fill(fill, resources=[], transcript=None) is fill


# --- load_resources: *.md from a dir, no-op on a bad path ------------------------------------

def test_load_resources_reads_markdown_sorted(tmp_path):
    (tmp_path / "b.md").write_text("B")
    (tmp_path / "a.md").write_text("A")
    (tmp_path / "ignore.txt").write_text("nope")
    got = load_resources(str(tmp_path))
    assert got == [{"name": "a.md", "content": "A"}, {"name": "b.md", "content": "B"}]


def test_load_resources_is_empty_on_missing_or_none(tmp_path):
    assert load_resources(str(tmp_path / "nope")) == []
    assert load_resources(None) == []
