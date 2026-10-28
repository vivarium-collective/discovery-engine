import sys
import textwrap
from pathlib import Path

import discovery_engine.study_evaluator as se


def _make_ws(tmp_path: Path, pkg: str, body: str) -> Path:
    (tmp_path / "workspace.yaml").write_text(f"name: {pkg}\n", encoding="utf-8")
    p = tmp_path / f"viva_{pkg}"
    p.mkdir()
    (p / "__init__.py").write_text("", encoding="utf-8")
    (p / "evaluators.py").write_text(textwrap.dedent(body), encoding="utf-8")
    return tmp_path


def test_loads_register_derived_scalars(tmp_path):
    se.clear_workspace_evaluator_cache()
    ws = _make_ws(tmp_path, "demo", """
        def register_derived_scalars(reg):
            reg["my_scalar"] = lambda reader, test, ws_root: 1.0
    """)
    try:
        reg = se.load_workspace_derived_scalars(ws)
        assert "my_scalar" in reg
        assert reg["my_scalar"](None, {}, ws) == 1.0
    finally:
        sys.path[:] = [p for p in sys.path if p != str(ws.resolve())]
        se.clear_workspace_evaluator_cache()


def test_missing_hook_returns_empty(tmp_path):
    se.clear_workspace_evaluator_cache()
    ws = _make_ws(tmp_path, "empty", "x = 1\n")  # no register_derived_scalars
    reg = se.load_workspace_derived_scalars(ws)
    assert reg == {}


def test_broken_hook_is_skipped(tmp_path):
    se.clear_workspace_evaluator_cache()
    ws = _make_ws(tmp_path, "broken", """
        def register_derived_scalars(reg):
            raise RuntimeError("boom")
    """)
    assert se.load_workspace_derived_scalars(ws) == {}


def test_none_ws_root_returns_empty():
    assert se.load_workspace_derived_scalars(None) == {}
