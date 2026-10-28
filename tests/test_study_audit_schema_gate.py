"""Study-audit L0 schema-validation gate.

``_check_schema_valid`` validates each study spec against the workspace's
``.pbg/schemas/study.schema.json``. A malformed ``contract.face`` (the
formalized typed port contract) is a HARD failure that blocks ``--gate``;
any other schema drift is SOFT (surfaced, non-blocking); a missing schema file
abstains (appends nothing). The schema is inlined here so the test exercises
the gate's logic independent of the real template schema.
"""
from __future__ import annotations

import json

import yaml

from discovery_engine.study_audit import audit_workspace


_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": True,
    "properties": {
        "name": {"type": "string"},
        "widget": {"type": "integer"},
        "contract": {
            "type": "object",
            "additionalProperties": True,
            "properties": {
                "face": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "inputs": {"$ref": "#/$defs/portFace"},
                        "outputs": {"$ref": "#/$defs/portFace"},
                    },
                },
            },
        },
    },
    "$defs": {
        "portFace": {
            "type": "object",
            "additionalProperties": {
                "type": "object",
                "additionalProperties": True,
                "required": ["_type"],
                "properties": {
                    "_type": {"type": "string"},
                    "_units": {"type": "string"},
                    "_min": {"type": "number"},
                    "_max": {"type": "number"},
                },
            },
        },
    },
}


def _ws(tmp_path, schema=_SCHEMA):
    (tmp_path / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    (tmp_path / "studies").mkdir(exist_ok=True)
    if schema is not None:
        sd = tmp_path / ".pbg" / "schemas"
        sd.mkdir(parents=True, exist_ok=True)
        (sd / "study.schema.json").write_text(json.dumps(schema), encoding="utf-8")
    return tmp_path


def _study(tmp_path, slug, spec):
    d = tmp_path / "studies" / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "study.yaml").write_text(yaml.safe_dump({"name": slug, **spec}), encoding="utf-8")


def _checks(report, slug):
    (a,) = [x for x in report.studies if x.slug == slug]
    return {c.name: c for c in a.checks}


def test_malformed_contract_face_is_hard_fail(tmp_path):
    ws = _ws(tmp_path)
    _study(ws, "s1", {"contract": {"face": {"inputs": {"temperature": {"_units": "kelvin"}}}}})
    c = _checks(audit_workspace(ws), "s1")["schema-contract-valid"]
    assert c.status == "fail" and c.tier == "hard"


def test_good_contract_face_passes(tmp_path):
    ws = _ws(tmp_path)
    _study(ws, "s1", {"contract": {"face": {"inputs": {"temperature": {"_type": "float", "_units": "kelvin"}}}}})
    c = _checks(audit_workspace(ws), "s1")["schema-contract-valid"]
    assert c.status == "pass" and c.tier == "hard"


def test_non_contract_drift_is_soft_only(tmp_path):
    ws = _ws(tmp_path)
    _study(ws, "s1", {"widget": "not-an-int",
                      "contract": {"face": {"inputs": {"t": {"_type": "float"}}}}})
    chk = _checks(audit_workspace(ws), "s1")
    assert chk["schema-contract-valid"].status == "pass"              # contract is fine
    assert chk["schema-valid"].status == "warn"
    assert chk["schema-valid"].tier == "soft"                        # widget drift surfaced, non-blocking


def test_multi_arm_baseline_list_is_canonical(tmp_path):
    # A conditions.baseline that is a LIST of arms (each with a composite) is a
    # first-class multi-arm study — canonical-model-schema must pass it.
    ws = _ws(tmp_path)
    _study(ws, "s1", {"conditions": {"baseline": [
        {"name": "arm-a", "composite": "pkg.comp_a"},
        {"name": "arm-b", "composite": "pkg.comp_b"}]}})
    c = _checks(audit_workspace(ws), "s1")["canonical-model-schema"]
    assert c.status == "pass" and c.tier == "hard"


def test_multi_arm_with_armless_composite_fails(tmp_path):
    # Every arm must carry a composite; one that doesn't is a hard fail.
    ws = _ws(tmp_path)
    _study(ws, "s1", {"conditions": {"baseline": [
        {"name": "arm-a", "composite": "pkg.comp_a"},
        {"name": "arm-b"}]}})
    c = _checks(audit_workspace(ws), "s1")["canonical-model-schema"]
    assert c.status == "fail" and c.tier == "hard"


def test_missing_schema_abstains(tmp_path):
    ws = _ws(tmp_path, schema=None)
    _study(ws, "s1", {"contract": {"face": {"inputs": {"t": {"_units": "kelvin"}}}}})
    chk = _checks(audit_workspace(ws), "s1")
    assert "schema-contract-valid" not in chk and "schema-valid" not in chk
