"""Study-level contract.face audit + reconciliation, on the shared
bigraph-schema audit kernel — the viva-superpowers half of unifying the
composite contract audit and the L0-L5 study audit."""
from discovery_engine.study_contract import study_face_findings, contract_face_diff


def test_study_face_findings_uses_the_kernel_to_flag_bad_range_and_units():
    spec = {"contract": {"face": {
        "inputs": {"t": {"_type": "float", "_min": 10, "_max": 0},
                   "g": {"_type": "float", "_units": "not_a_unit_xyz"}},
        "outputs": {}}}}
    codes = {f.code for f in study_face_findings(spec)}
    assert "bad_range" in codes and "bad_units" in codes


def test_study_face_findings_clean_face_has_no_errors():
    spec = {"contract": {"face": {
        "inputs": {"mass": {"_type": "float", "_min": 0, "_units": "mg"}},
        "outputs": {}}}}
    assert not [f for f in study_face_findings(spec) if f.severity == "error"]


def test_study_face_findings_absent_contract_is_empty():
    assert study_face_findings({}) == []
    assert study_face_findings({"contract": {}}) == []
    assert study_face_findings({"contract": {"face": {}}}) == []


def test_contract_face_diff_missing_type_mismatch_and_clean():
    declared = {"inputs": {"mass": {"_type": "float"}, "flux": {"_type": "float"}},
                "outputs": {"y": {"_type": "float"}}}
    resolved = {"inputs": {"mass": {"_type": "float"}},   # flux absent
                "outputs": {"y": {"_type": "int"}}}        # y type differs
    diff = contract_face_diff(declared, resolved)
    kinds = {(d["direction"], d["port"], d["kind"]) for d in diff}
    assert ("inputs", "flux", "missing") in kinds
    assert ("outputs", "y", "type_mismatch") in kinds
    # clean when the resolved face covers the declared ports with matching types
    assert contract_face_diff(
        {"inputs": {"mass": {"_type": "float"}}, "outputs": {}},
        {"inputs": {"mass": {"_type": "float"}}, "outputs": {}}) == []


def test_contract_face_diff_string_and_dict_types_compare_on_base():
    # a string port-type ('float[mM]') and a dict port-type compare on base type
    assert contract_face_diff(
        {"inputs": {"c": "float[mM]"}, "outputs": {}},
        {"inputs": {"c": {"_type": "float"}}, "outputs": {}}) == []
