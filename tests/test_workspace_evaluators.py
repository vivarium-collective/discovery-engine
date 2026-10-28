import inspect
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

import polars as pl
import pytest
import yaml

from discovery_engine import study_evaluator as se


@pytest.fixture(autouse=True)
def _evict_fixture_packages():
    yield
    for mod_name in list(sys.modules):
        if mod_name.startswith(("pbg_toyws", "pbg_brokenws", "toy_pkg")):
            del sys.modules[mod_name]


def _make_fixture_ws(tmp_path: Path, kind: str = "toy_kind") -> Path:
    """A throwaway workspace whose pbg_<name>.evaluators registers one evaluator."""
    ws = tmp_path / "ws"
    pkg = ws / "pbg_toyws"
    pkg.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: toyws\n", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "evaluators.py").write_text(textwrap.dedent(f"""
        def _toy(test, reader, ws_root):
            return {{"result": "PASS", "evaluated_by": "toy",
                    "detail": "from " + str(ws_root)}}
        def register_evaluators(registry):
            registry["{kind}"] = _toy
    """), encoding="utf-8")
    return ws


def test_loader_finds_and_calls_hook(tmp_path):
    ws = _make_fixture_ws(tmp_path)
    se.clear_workspace_evaluator_cache()
    reg = se.load_workspace_evaluators(ws)
    assert "toy_kind" in reg
    out = reg["toy_kind"]({}, None, ws)
    assert out["evaluated_by"] == "toy"


def test_loader_absent_hook_returns_empty(tmp_path):
    ws = tmp_path / "bare"
    ws.mkdir()
    (ws / "workspace.yaml").write_text("name: bare\n", encoding="utf-8")
    se.clear_workspace_evaluator_cache()
    assert se.load_workspace_evaluators(ws) == {}


def test_loader_broken_hook_degrades(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    pkg = ws / "pbg_brokenws"
    pkg.mkdir(parents=True)
    (ws / "workspace.yaml").write_text("name: brokenws\n", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "evaluators.py").write_text("raise RuntimeError('boom')\n", encoding="utf-8")
    se.clear_workspace_evaluator_cache()
    assert se.load_workspace_evaluators(ws) == {}  # never raises


def test_evaluate_test_dispatches_to_registered_evaluator(tmp_path):
    ws = _make_fixture_ws(tmp_path, kind="toy_kind")
    se.clear_workspace_evaluator_cache()
    test = {"name": "t", "measure": {"kind": "toy_kind"}, "pass_if": {"op": "x"}}
    out = se.evaluate_test(test, reader=None, ws_root=ws)
    assert out["evaluated_by"] == "toy"


def test_evaluate_test_unknown_kind_no_ws_still_agent():
    test = {"name": "t", "measure": {"kind": "nope"}, "pass_if": {"op": "x"}}
    out = se.evaluate_test(test, reader=None, ws_root=None)
    assert out["evaluated_by"] == "agent"


def test_compute_outcomes_threads_ws_root_into_evaluate_study():
    src = inspect.getsource(se.compute_outcomes)
    # ws_root must be threaded into evaluate_study so workspace evaluators are
    # reachable. (The per-run pass evaluates `per_run_spec` — cross-run tests are
    # split out into a study-level pass — so match on the ws_root kwarg, not the
    # exact spec variable name.)
    assert "evaluate_study(per_run_spec, reader, ws_root=ws_root)" in src, (
        "compute_outcomes must pass ws_root to evaluate_study so workspace "
        "evaluators are reachable"
    )


def test_compute_outcomes_ungraded_reconciles_as_no_authored(tmp_path: Path):
    """An 'ungraded' outcome (workspace evaluator skip) must reconcile as
    no_authored, NOT divergent, even when the authored result is PASS/FAIL."""
    # Build a minimal store dir (just needs to exist and be resolvable)
    store_dir = tmp_path / "store"
    store_dir.mkdir()
    (store_dir / "history").mkdir()

    # Study: one test t1 with an authored PASS; run pointing at the store
    study_dir = tmp_path / "study"
    study_dir.mkdir()
    (study_dir / "study.yaml").write_text(
        textwrap.dedent(f"""\
            schema_version: 4
            name: ungraded-test-study
            tests:
            - name: t1
              measure:
                kind: report_card_axis
              pass_if:
                op: eq
                value: 1
            runs:
            - name: run-a
              emitter:
                store: {store_dir}
              outcomes:
                t1:
                  result: PASS
                  detail: authored pass
        """),
        encoding="utf-8",
    )

    # Fake reader (content irrelevant — evaluate_study is monkeypatched)
    fake_reader = object()

    # evaluate_study returns an ungraded skip for t1
    def _fake_evaluate_study(spec, reader, ws_root=None):
        return {"t1": {"result": "ungraded", "evaluated_by": "report_card",
                        "detail": "no verdict file found"}}

    with (
        patch("viva_emitters.RunReader") as mock_cls,
        patch.object(se, "evaluate_study", side_effect=_fake_evaluate_study),
    ):
        mock_cls.open.return_value = fake_reader
        se.compute_outcomes(study_dir)

    doc = yaml.safe_load((study_dir / "study.yaml").read_text())
    reconcile = doc["runs"][0]["computed_outcomes"]["t1"]["reconcile"]
    assert reconcile == "no_authored", (
        f"Expected 'no_authored' for ungraded skip, got {reconcile!r}"
    )


def test_loader_finds_hook_via_package_path(tmp_path):
    """When the workspace package IS its `package_path` (not viva_<name>) — e.g.
    multiscale-BATS whose name is `multiscale-BATS` but package is
    `multiscale_bats` — the evaluators hook is still discovered."""
    ws = tmp_path / "ws"
    pkg = ws / "toy_pkg"
    pkg.mkdir(parents=True)
    # name-derived slug would be viva_My_Toy_WS (absent); package_path is the
    # only place the hook lives.
    (ws / "workspace.yaml").write_text(
        "name: My-Toy-WS\npackage_path: toy_pkg\n", encoding="utf-8")
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "evaluators.py").write_text(textwrap.dedent("""
        def _pp(test, reader, ws_root):
            return {"result": "PASS", "evaluated_by": "pkgpath"}
        def register_evaluators(registry):
            registry["pkgpath_kind"] = _pp
    """), encoding="utf-8")

    se.clear_workspace_evaluator_cache()
    cands = se._workspace_evaluator_packages(ws)
    assert "toy_pkg" in cands  # package_path is a candidate
    reg = se.load_workspace_evaluators(ws)
    assert "pkgpath_kind" in reg
    assert reg["pkgpath_kind"]({}, None, ws)["evaluated_by"] == "pkgpath"
