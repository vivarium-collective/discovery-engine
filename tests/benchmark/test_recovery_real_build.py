"""The recovery track can let a REAL model author the model via $VIVA_FILLER_CMD.

``recovery_build.real_build_hook`` asks a command which mechanism to implement, then builds
it through the normal toy builder — so recovery is earned by the model's CHOICE, graded by
the engine. These tests drive the real subprocess seam with a CANNED command (a deterministic
test double for a model): a command that chooses ``feedback_inhibition`` recovers (in band); a
wrong choice lands out of band; no command runs nothing. An opt-in test runs against an actual
$VIVA_FILLER_CMD when configured and asserts only that it completes.
"""
from __future__ import annotations

import json
import os
import sys

import pytest

from discovery_engine.benchmark.recovery_build import real_build_hook
from discovery_engine.benchmark.recovery_metrics import score_recovery
from discovery_engine.benchmark.recovery_runner import RECOVERY_CONFIGS, run_recovery_benchmark
from discovery_engine.benchmark.recovery_scenario import build_recovery_scenario
from discovery_engine.discover import cli


def _canned_build(tmp_path, mechanism, *, shape="implements"):
    """A command that reads the gap on stdin and names ``mechanism`` to implement."""
    if shape == "implements":
        payload = {"implements": mechanism}
    else:  # ModelAcquisitionProposal-shaped reply
        payload = {"artifact": {"mechanism": mechanism}}
    script = tmp_path / f"build_{shape}_{mechanism}.py"
    script.write_text(f"import sys, json; sys.stdin.read(); "
                      f"print(json.dumps({payload!r}))", encoding="utf-8")
    return f"{sys.executable} {script}"


def _rec_ws(tmp_path, name="ws"):
    ws = tmp_path / name
    ws.mkdir()
    return build_recovery_scenario(ws)


def test_real_build_hook_faithful_recovers(tmp_path):
    sc = _rec_ws(tmp_path)
    r = run_recovery_benchmark(sc, RECOVERY_CONFIGS["full"],
                               build_hook=real_build_hook(_canned_build(tmp_path, "feedback_inhibition")))
    m = score_recovery(r, sc.ground_truth)
    assert r.gap_status == "built" and m.ran is True and m.mechanism_recovered is True


def test_real_build_hook_wrong_choice_not_recovered(tmp_path):
    sc = _rec_ws(tmp_path)
    r = run_recovery_benchmark(sc, RECOVERY_CONFIGS["full"],
                               build_hook=real_build_hook(_canned_build(tmp_path, "substrate_depletion")))
    m = score_recovery(r, sc.ground_truth)
    # the model built the wrong mechanism — the behavioral run catches it, out of band
    assert m.ran is True and m.mechanism_recovered is False


def test_real_build_hook_accepts_proposal_shaped_reply(tmp_path):
    sc = _rec_ws(tmp_path)
    hook = real_build_hook(_canned_build(tmp_path, "feedback_inhibition", shape="artifact"))
    m = score_recovery(run_recovery_benchmark(sc, RECOVERY_CONFIGS["full"], build_hook=hook),
                       sc.ground_truth)
    assert m.mechanism_recovered is True


def test_real_build_hook_no_command_does_not_run(tmp_path, monkeypatch):
    monkeypatch.delenv("VIVA_FILLER_CMD", raising=False)
    sc = _rec_ws(tmp_path)
    m = score_recovery(run_recovery_benchmark(sc, RECOVERY_CONFIGS["full"], build_hook=real_build_hook()),
                       sc.ground_truth)
    assert m.ran is False and m.mechanism_recovered is False and m.held_out_error is None


def test_real_build_hook_bad_reply_does_not_run(tmp_path):
    # a command that names no mechanism → needs_implementation → nothing runs (honest)
    script = tmp_path / "noop.py"
    script.write_text("import sys; sys.stdin.read(); print('{}')", encoding="utf-8")
    sc = _rec_ws(tmp_path)
    hook = real_build_hook(f"{sys.executable} {script}")
    m = score_recovery(run_recovery_benchmark(sc, RECOVERY_CONFIGS["full"], build_hook=hook),
                       sc.ground_truth)
    assert m.ran is False and m.mechanism_recovered is False


def test_cli_recovery_real_llm_json(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    cmd = _canned_build(tmp_path, "feedback_inhibition")
    assert cli.main(["benchmark", "--recovery", "--filler-cmd", cmd, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["full"]["mechanism_recovered"] is True
    # read-only: the benchmark leaks no workspace/temp litter into cwd (only our canned scripts remain)
    assert not any(n.startswith("de-benchmark") or n in ("investigations", "studies")
                   for n in os.listdir(tmp_path))


@pytest.mark.skipif(not os.environ.get("VIVA_FILLER_CMD"),
                    reason="no real $VIVA_FILLER_CMD configured")
def test_real_model_build_hook_runs(tmp_path):
    sc = _rec_ws(tmp_path)
    m = score_recovery(run_recovery_benchmark(sc, RECOVERY_CONFIGS["full"], build_hook=real_build_hook()),
                       sc.ground_truth)
    assert isinstance(m.ran, bool)
