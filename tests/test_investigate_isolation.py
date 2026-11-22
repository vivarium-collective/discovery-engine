"""`discover investigate` grounds the filler with resources and records a transcript.

The reusable half of a bias-isolated run: the engine surfaces declared knowledge resources into
every request's ``context["resources"]`` (like ``--goal`` surfaces the steering goal), and a
transcript records every ``(request, result)`` at the filler seam so the run's prompts and
responses are an auditable, reproducible artifact.
"""
import json

from discovery_engine.benchmark.oracle import oracle_filler
from discovery_engine.benchmark.scenario import build_scenario
from discovery_engine.discover.budget import Budget
from discovery_engine.discover.investigate import investigate, load_resources
from discovery_engine.phases.generation import GateConfig

RES = [{"name": "bg.md", "content": "candidate mechanisms and how interventions discriminate them"}]


def _scenario(tmp_path, name):
    (tmp_path / name).mkdir()
    return build_scenario(tmp_path / name, name=name)


def _spy(inner, seen):
    """Wrap a filler, recording each request's injected ``context['resources']``."""
    def fill(request):
        seen.append((request.context or {}).get("resources"))
        return inner(request)
    return fill


def test_resources_reach_every_filler_request(tmp_path):
    sc = _scenario(tmp_path, "res1")
    seen: list = []
    summary = investigate(
        sc.ws_root, sc.inv_slug, fill=_spy(oracle_filler(sc.ground_truth), seen),
        budget=Budget(generation_calls=4), gates=GateConfig.preset("free_run"),
        max_iterations=5, resources=RES)
    assert seen, "expected at least one generative act"
    assert all(r == RES for r in seen)
    assert summary["resources"] == 1


def test_no_resources_leaves_context_clean(tmp_path):
    sc = _scenario(tmp_path, "res2")
    seen: list = []
    investigate(sc.ws_root, sc.inv_slug, fill=_spy(oracle_filler(sc.ground_truth), seen),
                budget=Budget(generation_calls=4), gates=GateConfig.preset("free_run"), max_iterations=5)
    assert seen and all(r is None for r in seen)


def test_transcript_records_request_and_result(tmp_path):
    sc = _scenario(tmp_path, "tr1")
    tpath = tmp_path / "transcript.jsonl"
    summary = investigate(
        sc.ws_root, sc.inv_slug, fill=oracle_filler(sc.ground_truth),
        budget=Budget(generation_calls=4), gates=GateConfig.preset("free_run"),
        max_iterations=5, transcript=str(tpath))
    assert tpath.is_file()
    entries = [json.loads(line) for line in tpath.read_text().splitlines() if line.strip()]
    assert entries, "transcript should record at least one (request, result)"
    assert "request" in entries[0] and "result" in entries[0]
    assert entries[0]["request"].get("kind")
    assert summary["transcript"] == str(tpath)


def test_transcript_captures_the_injected_resources(tmp_path):
    # The transcript records the FULLY-INJECTED request, so the resources must appear in its context.
    sc = _scenario(tmp_path, "tr2")
    tpath = tmp_path / "t.jsonl"
    investigate(sc.ws_root, sc.inv_slug, fill=oracle_filler(sc.ground_truth),
                budget=Budget(generation_calls=4), gates=GateConfig.preset("free_run"),
                max_iterations=5, resources=RES, transcript=str(tpath))
    entries = [json.loads(line) for line in tpath.read_text().splitlines() if line.strip()]
    assert entries and all(e["request"]["context"].get("resources") == RES for e in entries)


def test_load_resources_reads_md_only(tmp_path):
    d = tmp_path / "res"; d.mkdir()
    (d / "01.md").write_text("alpha")
    (d / "02.md").write_text("beta")
    (d / "note.txt").write_text("ignored")
    got = load_resources(str(d))
    assert [r["name"] for r in got] == ["01.md", "02.md"]
    assert got[0]["content"] == "alpha"
    assert load_resources(None) == []
    assert load_resources(str(tmp_path / "missing")) == []
