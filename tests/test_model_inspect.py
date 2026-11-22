"""R2 §2/§10 — inspect_component + validate_composite over a real process-bigraph Process.

The Process is passed as a class (not a dotted path), so the test imports no `tests.*` module
and reads the component's actual declared interface — nothing stubbed about the introspection."""
from __future__ import annotations

from process_bigraph import Process

from discovery_engine.phases.model_inspect import (
    ModelContract,
    inspect_component,
    validate_composite,
)


class _Metabolic(Process):
    """A minimal metabolic-exchange process with declared ports + a unit-annotated parameter."""
    config_schema = {"uptake_rate": {"_type": "float", "_default": 0.1, "_units": "mmol/L/s"}}

    def inputs(self):
        return {"substrate": "float", "biomass": "float"}

    def outputs(self):
        return {"biomass": "float",
                "exchange_flux": {"_type": "float", "_units": "mmol/s"}}

    def update(self, state, interval):
        return {}


def test_inspect_reads_params_and_ports():
    c = inspect_component(_Metabolic)
    assert isinstance(c, ModelContract)
    assert c.importable and c.is_process
    assert c.parameters["uptake_rate"]["units"] == "mmol/L/s"
    assert c.parameters["uptake_rate"]["default"] == 0.1
    assert set(c.input_ports) == {"substrate", "biomass"}
    assert "exchange_flux" in c.output_ports


def test_validate_detects_missing_exchange_port():
    # a spatial process expects an o2 exchange port the metabolic process does not declare
    expected = {"output_ports": {"exchange_flux": "float", "o2_exchange": "float"}}
    res = validate_composite(_Metabolic, expected=expected)
    assert not res["valid"]
    assert any(i["kind"] == "missing_port" and i["port"] == "o2_exchange" for i in res["issues"])


def test_validate_detects_unit_mismatch_on_port():
    expected = {"output_ports": {"exchange_flux": {"_type": "float", "_units": "g/s"}}}
    res = validate_composite(_Metabolic, expected=expected)
    assert not res["valid"]
    assert any(i["kind"] == "unit_mismatch" and i["port"] == "exchange_flux" for i in res["issues"])


def test_validate_detects_unit_mismatch_on_param():
    expected = {"parameters": {"uptake_rate": {"units": "g/L/s"}}}
    res = validate_composite(_Metabolic, expected=expected)
    assert any(i["kind"] == "unit_mismatch" and i.get("param") == "uptake_rate"
               for i in res["issues"])


def test_validate_passes_a_matching_contract():
    expected = {"input_ports": {"substrate": "float"},
                "output_ports": {"exchange_flux": {"_type": "float", "_units": "mmol/s"}},
                "parameters": {"uptake_rate": {"units": "mmol/L/s"}}}
    res = validate_composite(_Metabolic, expected=expected)
    assert res["valid"] and res["issues"] == []


def test_inspect_non_importable_component():
    c = inspect_component("nonexistent_pkg.module.Thing")
    assert not c.importable and not c.is_process
    res = validate_composite("nonexistent_pkg.module.Thing")
    assert not res["valid"]
    assert any(i["kind"] == "not_importable" for i in res["issues"])


# --- §2 acceptance: compose independently-authored metabolic + spatial processes ---

from discovery_engine.phases.model_inspect import compose_and_validate_model  # noqa: E402


class _Spatial(Process):
    """A reaction-diffusion process consuming a metabolic exchange flux."""
    config_schema = {"diffusion": {"_type": "float", "_units": "um2/s"}}

    def inputs(self):
        return {"flux_in": {"_type": "float", "_units": "mmol/s"}, "field": "float"}

    def outputs(self):
        return {"field": "float"}

    def update(self, state, interval):
        return {}


def test_compose_valid_coupling_is_runnable():
    comps = {"metab": _Metabolic, "space": _Spatial}
    wiring = [{"source": ["metab", "exchange_flux"], "target": ["space", "flux_in"]}]
    res = compose_and_validate_model(comps, wiring)
    assert res["runnable"] and res["issues"] == []
    assert set(res["model"]["components"]) == {"metab", "space"}


def test_compose_detects_missing_exchange_port():
    comps = {"metab": _Metabolic, "space": _Spatial}
    wiring = [{"source": ["metab", "o2_flux"], "target": ["space", "flux_in"]}]  # metab has no o2_flux
    res = compose_and_validate_model(comps, wiring)
    assert not res["runnable"]
    assert any(i["kind"] == "missing_port" and i["port"] == "o2_flux" for i in res["issues"])


def test_compose_detects_unit_mismatch_across_the_wire():
    class _SpaceBadUnits(Process):
        def inputs(self):
            return {"flux_in": {"_type": "float", "_units": "g/s"}}   # metab emits mmol/s

        def outputs(self):
            return {"field": "float"}

        def update(self, state, interval):
            return {}

    comps = {"metab": _Metabolic, "space": _SpaceBadUnits}
    wiring = [{"source": ["metab", "exchange_flux"], "target": ["space", "flux_in"]}]
    res = compose_and_validate_model(comps, wiring)
    assert not res["runnable"]
    assert any(i["kind"] == "unit_mismatch" for i in res["issues"])


def test_compose_flags_unknown_component_and_malformed_wire():
    comps = {"metab": _Metabolic}
    res = compose_and_validate_model(comps, [{"source": ["metab", "exchange_flux"],
                                              "target": ["ghost", "flux_in"]}])
    assert any(i["kind"] == "unknown_component" for i in res["issues"])
    res2 = compose_and_validate_model(comps, [{"source": ["metab"], "target": ["metab", "x"]}])
    assert any(i["kind"] == "malformed_wire" for i in res2["issues"])


# --- §2: register_model_version — content-addressed, with validation + lineage ---

from discovery_engine.phases.model_inspect import register_model_version  # noqa: E402


def _composed():
    comps = {"metab": _Metabolic, "space": _Spatial}
    wiring = [{"source": ["metab", "exchange_flux"], "target": ["space", "flux_in"]}]
    return compose_and_validate_model(comps, wiring)


def test_register_version_is_content_addressed_and_records_validation_and_lineage():
    mv = register_model_version(_composed(), config={"uptake_rate": 0.2}, parent_ids=["mv:parent"])
    assert mv["id"].startswith("mv:")
    assert mv["validation"]["runnable"] is True          # validation recorded
    assert set(mv["interface"]) == {"metab", "space"}    # interface contracts recorded
    assert mv["dependencies"]                            # component modules
    assert mv["parent_ids"] == ["mv:parent"]             # lineage recorded

    # content-addressed: the same model + config reproduces the same id (bind runs to it)
    mv_again = register_model_version(_composed(), config={"uptake_rate": 0.2})
    assert mv_again["id"] == mv["id"]


def test_register_version_id_changes_with_config_or_wiring():
    base = register_model_version(_composed(), config={"uptake_rate": 0.2})

    diff_cfg = register_model_version(_composed(), config={"uptake_rate": 0.9})
    assert diff_cfg["id"] != base["id"]

    other_wiring = compose_and_validate_model(
        {"metab": _Metabolic, "space": _Spatial},
        [{"source": ["metab", "biomass"], "target": ["space", "flux_in"]}])
    diff_wire = register_model_version(other_wiring, config={"uptake_rate": 0.2})
    assert diff_wire["id"] != base["id"]


def test_register_version_of_a_single_component():
    mv = register_model_version(_Metabolic, config={"uptake_rate": 0.1})
    assert mv["id"].startswith("mv:")
    assert "_Metabolic" in mv["interface"]
    assert mv["validation"]["valid"] is True


# --- §2: assess_mechanism_coverage — lexical may suggest, only backed declarations certify ---

from discovery_engine.phases.model_inspect import assess_mechanism_coverage  # noqa: E402


def test_coverage_certified_only_when_declaration_is_backed():
    for kind, ref in [("equation", "dS/dt = -v*X"), ("test", "test_exchange_flux"),
                      ("citation", "Beulig2018"), ("implementation", "viva_copasi.Exchange")]:
        decls = [{"mechanism": "metabolite_exchange", "backed_by": {"kind": kind, "ref": ref}}]
        r = assess_mechanism_coverage("metabolite_exchange", decls)
        assert r["status"] == "certified" and r["backing"]["kind"] == kind


def test_coverage_lexical_match_only_suggests_never_certifies():
    # the exact failure mode §2 calls out: a token overlap must NOT certify coverage
    r = assess_mechanism_coverage("membrane_mechanics", [],
                                  capability_tokens={"membrane", "mechanics", "lipid"})
    assert r["status"] == "suggested"
    assert r["status"] != "certified"


def test_coverage_unbacked_declaration_is_only_suggested():
    r = assess_mechanism_coverage("metabolite_exchange", [{"mechanism": "metabolite_exchange"}])
    assert r["status"] == "suggested" and r["backing"] is None


def test_coverage_not_covered_without_declaration_or_tokens():
    decls = [{"mechanism": "metabolite_exchange", "backed_by": {"kind": "test", "ref": "t"}}]
    r = assess_mechanism_coverage("quorum_sensing", decls,
                                  capability_tokens={"metabolite", "exchange"})
    assert r["status"] == "not_covered"


# --- §2 2nd acceptance: a capability gap closes only when the built model passes its tests ---

from discovery_engine.phases.model_inspect import assess_gap_closure  # noqa: E402

_CERTIFIED = {"mechanism": "metabolite_exchange", "status": "certified",
              "backing": {"kind": "test", "ref": "t"}}
_RUNNABLE = {"runnable": True, "issues": []}
_PASSING = [{"name": "exchange_in_band", "result": "PASS"}]


def test_gap_closes_only_when_all_three_gates_pass():
    r = assess_gap_closure("metabolite_exchange", coverage=_CERTIFIED,
                           model_validation=_RUNNABLE, behavioral_tests=_PASSING)
    assert r["status"] == "closed" and r["reasons"] == []


def test_gap_stays_open_when_model_interface_invalid():
    bad = {"runnable": False, "issues": [{"kind": "unit_mismatch"}]}
    r = assess_gap_closure("metabolite_exchange", coverage=_CERTIFIED,
                           model_validation=bad, behavioral_tests=_PASSING)
    assert r["status"] == "open"
    assert any(x["gate"] == "interface" for x in r["reasons"])


def test_gap_stays_open_when_coverage_only_suggested():
    suggested = {"mechanism": "metabolite_exchange", "status": "suggested", "backing": None}
    r = assess_gap_closure("metabolite_exchange", coverage=suggested,
                           model_validation=_RUNNABLE, behavioral_tests=_PASSING)
    assert r["status"] == "open"
    assert any(x["gate"] == "coverage" for x in r["reasons"])


def test_gap_stays_open_when_a_behavioral_test_fails_or_is_absent():
    failing = [{"name": "exchange_in_band", "result": "FAIL"}]
    r = assess_gap_closure("metabolite_exchange", coverage=_CERTIFIED,
                           model_validation=_RUNNABLE, behavioral_tests=failing)
    assert r["status"] == "open" and any(x["gate"] == "behavioral" for x in r["reasons"])

    r2 = assess_gap_closure("metabolite_exchange", coverage=_CERTIFIED,
                            model_validation=_RUNNABLE, behavioral_tests=[])
    assert r2["status"] == "open" and any(x["gate"] == "behavioral" for x in r2["reasons"])
