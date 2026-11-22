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
