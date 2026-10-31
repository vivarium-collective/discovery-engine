"""In-place scaffold must propagate the template's `requires-python` floor.

Regression for #357: `_inplace_merge_pyproject` merges the template's
dependencies (bigraph-viz>=2.0.3 etc. need py>=3.11) into an existing host
pyproject, but it left the host's `requires-python` untouched. On a host that
declares `>=3.10`, `uv lock`/`uv sync` then can't resolve. The scaffolder must
raise the host's floor to the template's when the template's is higher.
"""
from __future__ import annotations

import tomllib

from discovery_engine.integration.scaffold import (
    _inplace_merge_requires_python,
    _requires_python_floor,
    _template_requires_python,
)


def test_floor_parse():
    assert _requires_python_floor(">=3.11") == (3, 11)
    assert _requires_python_floor(">=3.10,<4") == (3, 10)
    assert _requires_python_floor("~=3.11") == (3, 11)
    assert _requires_python_floor(None) is None
    assert _requires_python_floor("") is None


def test_template_requires_python_extracted():
    tpl = '[project]\nname = "t"\nrequires-python = ">=3.11"\ndependencies = []\n'
    assert _template_requires_python(tpl) == ">=3.11"


def test_merge_raises_host_floor_to_template(tmp_path):
    host = tmp_path / "pyproject.toml"
    host.write_text(
        "[project]\n"
        'name = "viva_demo"\n'
        'requires-python = ">=3.10"\n'
        "dependencies = []\n"
    )
    changed = _inplace_merge_requires_python(host, ">=3.11")
    assert changed == ">=3.11"
    merged = tomllib.loads(host.read_text(encoding="utf-8"))
    assert merged["project"]["requires-python"] == ">=3.11"


def test_merge_leaves_higher_host_floor_untouched(tmp_path):
    host = tmp_path / "pyproject.toml"
    host.write_text(
        "[project]\n"
        'name = "viva_demo"\n'
        'requires-python = ">=3.12"\n'
        "dependencies = []\n"
    )
    changed = _inplace_merge_requires_python(host, ">=3.11")
    assert changed is None
    merged = tomllib.loads(host.read_text(encoding="utf-8"))
    assert merged["project"]["requires-python"] == ">=3.12"


def test_merge_sets_floor_when_host_declares_none(tmp_path):
    host = tmp_path / "pyproject.toml"
    host.write_text(
        "[project]\n"
        'name = "viva_demo"\n'
        "dependencies = []\n"
    )
    changed = _inplace_merge_requires_python(host, ">=3.11")
    assert changed == ">=3.11"
    merged = tomllib.loads(host.read_text(encoding="utf-8"))
    assert merged["project"]["requires-python"] == ">=3.11"
