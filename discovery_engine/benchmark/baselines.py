"""The baseline roster for the benchmark comparison.

The plan compares the full engine against baselines under equal budgets. Each baseline is a set
of allowed skills (a Manifest/registry subset). The baselines expressible against the engine's
current three registry kinds (``hypothesize`` / ``critique`` / ``acquire_model``) are defined
here; the rest await Wave-B skills:

- ``full``                — all generative skills.
- ``deterministic_only``  — baseline **A**: no generative skills (pure projection).
- ``no_critique``         — baseline **C**: the engine without the critique skill.
- ``no_build``            — baseline **E**-adjacent: no mechanism acquisition / model build.

Not yet expressible (:data:`PENDING_BASELINES`): **D** (no-design) needs a ``design`` skill,
and **B** (general-purpose agent) is a *filler* swap, not a skill subset. Pure; AI-free.
"""
from __future__ import annotations

FULL = ("hypothesize", "critique", "acquire_model")

BASELINES = {
    "full": FULL,
    "deterministic_only": (),
    "no_critique": ("hypothesize", "acquire_model"),
    "no_build": ("hypothesize", "critique"),
}

PENDING_BASELINES = ("no_design", "general_agent")


def manifest_for(name: str):
    """A :class:`~discovery_engine.benchmark.manifest.Manifest` for baseline ``name`` (its
    ``allowed_skills`` restrict the registry). Usable directly as a ``run_benchmark`` config."""
    from discovery_engine.benchmark.manifest import from_dict
    if name not in BASELINES:
        raise ValueError(f"unknown baseline {name!r}; expected one of {tuple(BASELINES)}")
    return from_dict({"id": f"baseline-{name}", "question": "baseline comparison",
                      "fixture": {}, "budget": {}, "allowed_skills": list(BASELINES[name])})
