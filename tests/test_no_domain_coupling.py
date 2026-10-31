"""The framework's CODE (docstrings and comments included) must not name any
specific workspace's domain. Domain examples belong in workspaces, not in the
engine. ``docs/extracted/`` is excluded; tests are outside the package."""
from __future__ import annotations

import pathlib
import re

DOMAIN_TOKENS = (
    "genesis", "eukaryo", "endosymb", "syntroph",
    "v2ecoli", "vecoli", "parca", "continuum_membrane",
)
# {relative path: {tokens permitted there}} - only for tokens unavoidable in
# framework logic. Intentionally empty.
ALLOWLIST: dict[str, set[str]] = {}

_PATTERN = re.compile("|".join(DOMAIN_TOKENS), re.IGNORECASE)


def _offenders() -> list[str]:
    pkg = pathlib.Path(__file__).resolve().parents[1] / "discovery_engine"
    out = []
    for f in sorted(pkg.rglob("*.py")):
        rel = f.relative_to(pkg.parent).as_posix()
        if "docs/extracted" in rel:
            continue
        allowed = ALLOWLIST.get(rel, set())
        for n, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            for m in _PATTERN.finditer(line):
                if m.group(0).lower() not in allowed:
                    out.append(f"{rel}:{n}: {m.group(0)}")
    return out


def test_framework_code_has_no_domain_tokens():
    off = _offenders()
    assert not off, "domain terms in framework code:\n" + "\n".join(off)


def test_allowlist_is_empty():
    assert ALLOWLIST == {}
