"""Tests for the peer-review rigor lint checks (framework-rigor G1/G4/G5).

One tripping spec + one passing spec per check, proving each check is
sensitive (fires on the violation) and specific (silent on the fix):

- ``gate_class_missing`` / ``gate_class_unknown`` (G1-lint) — every
  behavior_tests[] entry declares gate_class: regression_pin |
  acceptance_criterion; never inferred.
- ``config_consumption`` (G4a, structural) — supplied composite config keys
  must be accepted by the process's declared config_schema literal.
- ``stochastic_unseeded`` (G4b) — stochastic composite processes must pin a
  seed in their config.
- ``unearned_unit_labels`` (G5) — physical unit labels in claim/finding text
  require a units_and_time declaration.

Uses the direct ``_LintContext`` call style of test_report_linter_viz_gate.py
(``WorkspacePaths.load`` tolerates a missing workspace.yaml), plus tmp-dir
workspaces with real composite spec + package source files for the G4 checks.
"""
from __future__ import annotations

import textwrap
from pathlib import Path

import yaml

from discovery_engine.report_linter import (
    CHECKS,
    _CHECK_FUNCTIONS,
    _LintContext,
    _check_config_consumption,
    _check_gate_class_declared,
    _check_stochastic_unseeded,
    _check_unearned_unit_labels,
    _check_verdict_divergence,
    lint_workspace_report,
)


def _run(check_fn, ws_root: Path, spec: dict, slug: str = "s1"):
    ctx = _LintContext(ws_root=ws_root, slug=slug, spec=spec)
    check_fn(ctx)
    return ctx.findings


def _by_check(findings, name):
    return [f for f in findings if f.check == name]


# ---------------------------------------------------------------------------
# Registration — the new checks are actually wired into the linter
# ---------------------------------------------------------------------------


def test_new_checks_registered_in_checks_and_functions():
    for name in ("gate_class_missing", "gate_class_unknown",
                 "config_consumption", "stochastic_unseeded",
                 "unearned_unit_labels", "verdict_divergence"):
        assert name in CHECKS, f"{name} missing from CHECKS registry"
    for fn in (_check_gate_class_declared, _check_config_consumption,
               _check_stochastic_unseeded, _check_unearned_unit_labels,
               _check_verdict_divergence):
        assert fn in _CHECK_FUNCTIONS, f"{fn.__name__} not in _CHECK_FUNCTIONS"


# ---------------------------------------------------------------------------
# G1-lint — gate_class declared on every behavior_tests[] entry
# ---------------------------------------------------------------------------


def test_gate_class_missing_on_gated_test_is_warning(tmp_path):
    spec = {"behavior_tests": [
        {"name": "yield-band", "pass_if": {"op": "between", "low": 0.2, "high": 0.5}},
    ]}
    found = _by_check(_run(_check_gate_class_declared, tmp_path, spec),
                      "gate_class_missing")
    assert len(found) == 1
    f = found[0]
    assert f.level == "warning"
    assert f.field_path == "behavior_tests[0].gate_class"
    assert "regression_pin" in f.message and "acceptance_criterion" in f.message


def test_gate_class_missing_on_descriptive_test_is_info(tmp_path):
    # No machine-readable gate yet (name + description only) → nudge, not WARN,
    # so pre-gate design-stage studies (and the clean-baseline fixture) don't
    # grow a blocking finding.
    spec = {"behavior_tests": [
        {"name": "dnaa-steady", "description": "DnaA settles in a plausible range."},
    ]}
    found = _by_check(_run(_check_gate_class_declared, tmp_path, spec),
                      "gate_class_missing")
    assert len(found) == 1
    assert found[0].level == "info"


def test_gate_class_unknown_value_is_warning(tmp_path):
    spec = {"behavior_tests": [
        {"name": "t", "pass_if": {"op": "<", "value": 0.05},
         "gate_class": "post-hoc-pin"},
    ]}
    findings = _run(_check_gate_class_declared, tmp_path, spec)
    unknown = _by_check(findings, "gate_class_unknown")
    assert len(unknown) == 1
    assert unknown[0].level == "warning"
    assert "post-hoc-pin" in unknown[0].message
    # A declared-but-unknown value is not ALSO flagged as missing.
    assert _by_check(findings, "gate_class_missing") == []


def test_gate_class_declared_valid_values_are_silent(tmp_path):
    spec = {"behavior_tests": [
        {"name": "pin", "pass_if": {"op": "between", "low": 1.0, "high": 2.0},
         "gate_class": "regression_pin"},
        {"name": "ac", "pass_if": {"op": ">", "value": 0.0},
         "gate_class": "acceptance_criterion"},
    ]}
    assert _run(_check_gate_class_declared, tmp_path, spec) == []


# ---------------------------------------------------------------------------
# G4 shared fixture builder — a tmp workspace with package source + composite
# ---------------------------------------------------------------------------


def _make_ws(tmp_path: Path, *, class_src: str, composite: dict,
             composite_name: str = "growth") -> Path:
    ws = tmp_path / "ws"
    pkg = ws / "viva_demo"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "processes.py").write_text(textwrap.dedent(class_src))
    comp_dir = ws / "composites"
    comp_dir.mkdir()
    (comp_dir / f"{composite_name}.composite.yaml").write_text(
        yaml.safe_dump(composite)
    )
    (ws / "workspace.yaml").write_text(
        "schema_version: 2\nname: review-checks\npackage_path: viva_demo\n"
    )
    return ws


_GUARDED_CLASS = """
    class GrowthProcess:
        config_schema = {
            "rate": {"_type": "float", "_default": 1.0},
            "carrying_capacity": {"_type": "float", "_default": 100.0},
        }

        def update(self, state, interval):
            return {}
"""


def _growth_composite(config: dict) -> dict:
    return {
        "name": "growth-demo",
        "state": {
            "growth": {
                "_type": "process",
                "address": "local:GrowthProcess",
                "config": config,
                "inputs": {"level": ["stores", "level"]},
                "outputs": {"level": ["stores", "level"]},
            },
            "stores": {"level": 1.0},
        },
    }


_STUDY_SPEC = {
    "name": "s1",
    "baseline": [{"name": "b1", "composite": "growth"}],
}


# ---------------------------------------------------------------------------
# G4a — config_consumption (structural)
# ---------------------------------------------------------------------------


def test_config_consumption_flags_dropped_key(tmp_path):
    # The mut_sigma disease, structurally: the composite pins a key the
    # process's declared config_schema does not accept → silently dropped.
    ws = _make_ws(
        tmp_path,
        class_src=_GUARDED_CLASS,
        composite=_growth_composite({"rate": 2.0, "mut_sigma": 0.0}),
    )
    found = _by_check(_run(_check_config_consumption, ws, _STUDY_SPEC),
                      "config_consumption")
    assert len(found) == 1
    f = found[0]
    assert f.level == "warning"
    # The finding names (composite, process, dropped key).
    assert "growth-demo" in f.message
    assert "GrowthProcess" in f.message
    assert "mut_sigma" in f.message
    assert f.field_path.endswith(".config.mut_sigma")


def test_config_consumption_silent_when_all_keys_accepted(tmp_path):
    ws = _make_ws(
        tmp_path,
        class_src=_GUARDED_CLASS,
        composite=_growth_composite({"rate": 2.0, "carrying_capacity": 50.0}),
    )
    assert _run(_check_config_consumption, ws, _STUDY_SPEC) == []


def test_config_consumption_skips_dynamic_schema(tmp_path):
    # A class whose config_schema isn't a plain dict literal can't be audited
    # statically → conservatively silent, never a guess.
    ws = _make_ws(
        tmp_path,
        class_src="""
            def _make_schema():
                return {"rate": {"_type": "float"}}

            class GrowthProcess:
                config_schema = _make_schema()
        """,
        composite=_growth_composite({"rate": 2.0, "mut_sigma": 0.0}),
    )
    assert _run(_check_config_consumption, ws, _STUDY_SPEC) == []


def test_config_consumption_silent_when_composite_unresolvable(tmp_path):
    # Registry-id / dotted refs with no file on disk → out of scope, silent.
    ws = _make_ws(tmp_path, class_src=_GUARDED_CLASS,
                  composite=_growth_composite({"rate": 1.0}))
    spec = {"name": "s1",
            "baseline": [{"name": "b1", "composite": "pkg.composites.x"}]}
    assert _run(_check_config_consumption, ws, spec) == []


# ---------------------------------------------------------------------------
# G4b — stochastic_unseeded
# ---------------------------------------------------------------------------


_RNG_CLASS = """
    import numpy as np

    class NoiseProcess:
        config_schema = {
            "sigma": {"_type": "float", "_default": 0.1},
            "seed": {"_type": "integer", "_default": 0},
        }

        def update(self, state, interval):
            rng = np.random.default_rng()
            return {"level": rng.normal(0.0, self.config["sigma"])}
"""


def _noise_composite(config: dict) -> dict:
    return {
        "name": "noise-demo",
        "state": {
            "noise": {
                "_type": "process",
                "address": "local:NoiseProcess",
                "config": config,
            },
        },
    }


def test_stochastic_unseeded_flags_rng_process_without_seed(tmp_path):
    ws = _make_ws(tmp_path, class_src=_RNG_CLASS,
                  composite=_noise_composite({"sigma": 0.2}),
                  composite_name="growth")
    found = _by_check(_run(_check_stochastic_unseeded, ws, _STUDY_SPEC),
                      "stochastic_unseeded")
    assert len(found) == 1
    f = found[0]
    assert f.level == "warning"
    assert "NoiseProcess" in f.message
    assert "noise-demo" in f.message
    assert "seed" in f.message


def test_stochastic_unseeded_silent_when_seed_pinned(tmp_path):
    ws = _make_ws(tmp_path, class_src=_RNG_CLASS,
                  composite=_noise_composite({"sigma": 0.2, "seed": 42}),
                  composite_name="growth")
    assert _run(_check_stochastic_unseeded, ws, _STUDY_SPEC) == []


def test_stochastic_flag_and_name_signals_fire_without_source(tmp_path):
    # No class source anywhere: the declared flag and the class-name signal
    # still detect stochasticity (conservative signals that need no import).
    ws = _make_ws(tmp_path, class_src="class Unrelated:\n    pass\n",
                  composite={
                      "name": "flags-demo",
                      "state": {
                          "declared": {
                              "_type": "process",
                              "address": "local:OpaqueProcess",
                              "config": {"stochastic": True},
                          },
                          "named": {
                              "_type": "process",
                              "address": "local:GillespieReactions",
                              "config": {"volume": 1.0},
                          },
                      },
                  })
    found = _by_check(_run(_check_stochastic_unseeded, ws, _STUDY_SPEC),
                      "stochastic_unseeded")
    assert {("OpaqueProcess" in f.message, "GillespieReactions" in f.message)
            for f in found} == {(True, False), (False, True)}
    assert len(found) == 2


def test_deterministic_process_without_seed_is_silent(tmp_path):
    # Conservative: no declared flag, benign name, no RNG in source → silent
    # even though no seed is pinned (better to under-flag).
    ws = _make_ws(tmp_path, class_src=_GUARDED_CLASS,
                  composite=_growth_composite({"rate": 2.0}))
    assert _run(_check_stochastic_unseeded, ws, _STUDY_SPEC) == []


# ---------------------------------------------------------------------------
# G5 — unearned_unit_labels
# ---------------------------------------------------------------------------


def test_unit_labels_without_units_and_time_warn(tmp_path):
    spec = {
        "findings": [{
            "id": "F-01",
            "statement": "External glucose stabilizes at 4.2 mM by hour ten.",
        }],
        "conclusion_logic": {
            "if_primary_tests_pass": {
                "summary": "Uptake saturates within 30 seconds of the shift.",
            },
        },
    }
    found = _by_check(_run(_check_unearned_unit_labels, tmp_path, spec),
                      "unearned_unit_labels")
    assert len(found) == 1
    f = found[0]
    assert f.level == "warning"
    assert f.field_path == "units_and_time"
    assert "units_and_time" in f.message
    assert "mM" in f.message  # names the offending label
    assert "findings[0].statement" in f.message  # and where it appears


def test_unit_labels_with_units_and_time_declared_are_silent(tmp_path):
    spec = {
        "units_and_time": {
            "tick": "1 tick = 1 s (calibrated to Boesen 2024 flux units)",
            "fields": {"glucose": "mM"},
        },
        "findings": [{
            "id": "F-01",
            "statement": "External glucose stabilizes at 4.2 mM by hour ten.",
        }],
    }
    assert _run(_check_unearned_unit_labels, tmp_path, spec) == []


def test_unitless_claims_are_silent(tmp_path):
    spec = {
        "findings": [{
            "id": "F-01",
            "statement": "The mutant population collapses within 350-500 ticks.",
        }],
        "conclusion_logic": {
            "if_primary_tests_pass": {"summary": "Ratio holds at 0.42."},
        },
    }
    assert _run(_check_unearned_unit_labels, tmp_path, spec) == []


def test_unit_regex_requires_numeric_context(tmp_path):
    # Prose mentioning units without a number ("the mM scale", the word "um")
    # must not fire — the label only counts when it annotates a value.
    spec = {
        "findings": [{
            "id": "F-01",
            "statement": "We discuss the mM scale qualitatively, um, later.",
        }],
    }
    assert _run(_check_unearned_unit_labels, tmp_path, spec) == []


# ---------------------------------------------------------------------------
# Integration — the checks run (and fire) through lint_workspace_report
# ---------------------------------------------------------------------------


def test_checks_surface_through_lint_workspace_report(tmp_path):
    ws = _make_ws(
        tmp_path,
        class_src=_GUARDED_CLASS,
        composite=_growth_composite({"rate": 2.0, "mut_sigma": 0.0}),
    )
    study_dir = ws / "studies" / "s1"
    study_dir.mkdir(parents=True)
    (study_dir / "study.yaml").write_text(yaml.safe_dump({
        "name": "s1",
        "baseline": [{"name": "b1", "composite": "growth"}],
        "behavior_tests": [
            {"name": "yield-band",
             "pass_if": {"op": "between", "low": 0.2, "high": 0.5}},
        ],
        "findings": [{
            "id": "F-01",
            "statement": "Yield settles at 3.1 mM under the baseline.",
        }],
    }))
    checks = {f.check for f in lint_workspace_report(ws)}
    assert "config_consumption" in checks
    assert "gate_class_missing" in checks
    assert "unearned_unit_labels" in checks


# ---------------------------------------------------------------------------
# finding_without_evidence escalation (shared predicate with study-audit gate)
# ---------------------------------------------------------------------------


def _fwe_spec(verdict, status="confirms"):
    spec = {"name": "s1", "findings": [
        {"id": "F-01", "kind": "biological", "status": status,
         "statement": "x", "evidence": {}}]}
    if verdict:
        spec["report"] = {"verdict": verdict}
    return spec


def test_finding_without_evidence_errors_for_supporting_of_accepted(tmp_path):
    from discovery_engine.report_linter import _check_finding_without_evidence
    got = _by_check(_run(_check_finding_without_evidence, tmp_path,
                         _fwe_spec("accepted")), "finding_without_evidence")
    assert [f.level for f in got] == ["error"]


def test_finding_without_evidence_warns_when_not_accepted(tmp_path):
    from discovery_engine.report_linter import _check_finding_without_evidence
    got = _by_check(_run(_check_finding_without_evidence, tmp_path,
                         _fwe_spec(None)), "finding_without_evidence")
    assert [f.level for f in got] == ["warning"]


def test_finding_without_evidence_warns_for_caveat_in_accepted(tmp_path):
    from discovery_engine.report_linter import _check_finding_without_evidence
    got = _by_check(_run(_check_finding_without_evidence, tmp_path,
                         _fwe_spec("accepted", status="partial")),
                    "finding_without_evidence")
    assert [f.level for f in got] == ["warning"]
