"""A bounded generation drain: the budget halts the loop with a typed stop."""
import yaml

from discovery_engine.discover.budget import GENERATION_CALLS, Budget
from discovery_engine.phases.generation import GenerationRequest, GenerationResult
from discovery_engine.phases.generation_apply import run_requests


def _ws(tmp_path):
    (tmp_path / "investigations" / "demo").mkdir(parents=True)
    (tmp_path / "investigations" / "demo" / "investigation.yaml").write_text(
        yaml.safe_dump({"name": "demo", "studies": []}))
    return tmp_path


def _reqs(n):
    return [GenerationRequest(kind="resolve_decision", target=f"d{i}", produces="decision/v1",
                              gate="auto", provenance={"op": "steer"}) for i in range(n)]


def _fill(req):
    return GenerationResult(request_id=req.id, artifact={"rationale": "use the prior"})


def test_budget_halts_the_drain_with_a_stop(tmp_path):
    ws = _ws(tmp_path)
    b = Budget(generation_calls=2)
    outs = run_requests(_reqs(3), fill=_fill, ws_root=ws, inv_slug="demo", budget=b)
    assert [o["outcome"] for o in outs] == ["applied", "applied", "stop"]
    assert outs[-1]["reason"] == "budget_exhausted"
    assert b.spent[GENERATION_CALLS] == 2               # the third act was refused, not run
    inv = yaml.safe_load((ws / "investigations" / "demo" / "investigation.yaml").read_text())
    assert len(inv["resolutions"]) == 2                 # only two decisions were applied


def test_no_budget_is_unbounded(tmp_path):
    ws = _ws(tmp_path)
    outs = run_requests(_reqs(3), fill=_fill, ws_root=ws, inv_slug="demo")
    assert [o["outcome"] for o in outs] == ["applied", "applied", "applied"]
