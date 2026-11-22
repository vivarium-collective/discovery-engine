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
