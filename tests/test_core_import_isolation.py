"""The CORE discovery modules must import without the workspace/server
plumbing (scaffold, server_preflight, the ~/.pbg registry)."""
import subprocess
import sys
import textwrap

CORE = (
    "discovery_engine", "discovery_engine.discover", "discovery_engine.evaluation",
    "discovery_engine.model_architect", "discovery_engine.hypotheses",
    "discovery_engine.knowledge_store", "discovery_engine.argument_store",
    "discovery_engine.narrative", "discovery_engine.evidence_integrity",
    "discovery_engine.report_linter", "discovery_engine.study_evaluator",
)
PLUMBING = (
    "discovery_engine.integration", "discovery_engine.scaffold",
    "discovery_engine.server_preflight", "discovery_engine.workspace_catalog",
)


def test_core_imports_pull_in_no_plumbing():
    code = textwrap.dedent(f"""
        import importlib, sys
        for m in {CORE!r}:
            importlib.import_module(m)
        bad = [m for m in {PLUMBING!r} if m in sys.modules]
        assert not bad, bad
        import discovery_engine.report_linter as rl
        assert rl._server_url.__name__  # lazy hook only; not imported above
    """)
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_core_sources_do_not_reference_plumbing_eagerly():
    import pathlib, re
    pkg = pathlib.Path(__file__).resolve().parents[1] / "discovery_engine"
    pat = re.compile(r"^(from|import)\s+(discovery_engine(\.integration)?\.|\.)?(scaffold|server_preflight|workspace_catalog|integration)\b", re.M)
    for f in pkg.glob("*.py"):
        if f.name in ("scaffold.py", "server_preflight.py", "workspace_catalog.py"):
            continue
        assert not pat.search(f.read_text(encoding="utf-8")), f.name  # module-level only
