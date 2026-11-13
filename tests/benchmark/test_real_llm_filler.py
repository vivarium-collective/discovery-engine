"""The proposal benchmark can run against a real model via the $VIVA_FILLER_CMD seam.

These tests exercise the REAL ``generation_apply.subprocess_filler`` path (stdin JSON ->
subprocess -> stdout GenerationResult -> validate -> apply). To stay deterministic and
CI-safe they use a CANNED filler command — a small script that emits a fixed, valid
GenerationResult. That is an explicit test double for a real model: it proves the
plumbing, not a model's capability. The opt-in test at the bottom runs against an actual
$VIVA_FILLER_CMD when one is configured and asserts only that it runs (a real model may or
may not propose the concealed mechanism — that is the honest point).
"""
from __future__ import annotations

import os
import sys

import pytest

from discovery_engine.benchmark.ablation import CONFIGS
from discovery_engine.benchmark.metrics import score
from discovery_engine.benchmark.report import run_matrix
from discovery_engine.benchmark.runner import run_benchmark
from discovery_engine.benchmark.scenario import build_scenario
from discovery_engine.phases.generation_apply import subprocess_filler

# A deterministic stand-in for a real model: reads the GenerationRequest JSON on stdin and
# emits a valid GenerationResult JSON on stdout for the kinds the proposal scenario raises.
_CANNED = '''import sys, json
req = json.loads(sys.stdin.read())
kind, rid, target = req.get("kind"), req.get("id"), req.get("target")
band = {"op": "range", "low": 0.3, "high": 0.8}
if kind == "acquire_model":
    art = {"mechanism": "feedback_inhibition",
           "expected_behavioral_test": {"behavior_test": "enc_in_band", "pass_if": band},
           "provenance": {"trigger": "needs_author_mechanism", "source": target, "by": "canned"}}
elif kind == "critique":
    art = {"target": target,
           "unsupported_claims": [{"claim": "gate_status outruns the evidence",
                                   "why": "no verified run"}],
           "provenance": {"trigger": "verdict_divergence", "source": target, "by": "canned"}}
else:
    sys.exit(0)  # emit nothing -> the filler returns None (queued), never a bad apply
print(json.dumps({"request_id": rid, "artifact": art,
                  "filler": {"kind": "ai", "id": "canned", "model": "canned"}}))
'''


def _canned_cmd(tmp_path):
    script = tmp_path / "canned_filler.py"
    script.write_text(_CANNED, encoding="utf-8")
    return f"{sys.executable} {script}"


def _ws(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    return build_scenario(ws)


def test_subprocess_filler_drives_proposal_benchmark(tmp_path):
    # The real subprocess_filler seam, end to end, with a canned command.
    sc = _ws(tmp_path)
    m = score(run_benchmark(sc, CONFIGS["full"], oracle=subprocess_filler(_canned_cmd(tmp_path))),
              sc.ground_truth)
    assert m.mechanism_proposed is True and m.proposal_count >= 1


def test_run_matrix_fill_override(tmp_path_factory, tmp_path):
    # run_matrix(fill=...) uses the given filler for EVERY config; ablation still drops the skill.
    mat = run_matrix(tmp_path_factory=tmp_path_factory, fill=subprocess_filler(_canned_cmd(tmp_path)))
    assert mat["full"].mechanism_proposed is True
    assert mat["no_acquire_model"].mechanism_proposed is False


def test_subprocess_filler_no_command_fills_nothing(tmp_path, monkeypatch):
    # With no command configured, the filler returns None and nothing is proposed (no crash).
    monkeypatch.delenv("VIVA_FILLER_CMD", raising=False)
    sc = _ws(tmp_path)
    m = score(run_benchmark(sc, CONFIGS["full"], oracle=subprocess_filler()), sc.ground_truth)
    assert m.mechanism_proposed is False and m.proposal_count == 0


def test_cli_real_llm_without_command_errors(tmp_path, monkeypatch, capsys):
    from discovery_engine.discover import cli
    monkeypatch.delenv("VIVA_FILLER_CMD", raising=False)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["benchmark", "--real-llm", "--json"]) == 2
    assert "VIVA_FILLER_CMD" in capsys.readouterr().err


def test_cli_real_llm_with_recovery_errors(tmp_path, monkeypatch, capsys):
    from discovery_engine.discover import cli
    monkeypatch.chdir(tmp_path)
    assert cli.main(["benchmark", "--real-llm", "--recovery", "--filler-cmd", "x"]) == 2
    assert "recovery" in capsys.readouterr().err.lower()


def test_cli_filler_cmd_implies_real_llm(tmp_path, monkeypatch, capsys):
    # --filler-cmd alone (no --real-llm) drives the real subprocess seam.
    import json

    from discovery_engine.discover import cli
    monkeypatch.chdir(tmp_path)
    assert cli.main(["benchmark", "--filler-cmd", _canned_cmd(tmp_path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["full"]["mechanism_proposed"] is True


@pytest.mark.skipif(not os.environ.get("VIVA_FILLER_CMD"),
                    reason="no real $VIVA_FILLER_CMD configured")
def test_real_llm_filler_runs(tmp_path):
    # Opt-in: against an ACTUAL model we assert only that the run completes and scores an
    # int proposal_count — never a fixed pass (a real model may not propose the mechanism).
    sc = _ws(tmp_path)
    m = score(run_benchmark(sc, CONFIGS["full"], oracle=subprocess_filler()), sc.ground_truth)
    assert isinstance(m.proposal_count, int)
