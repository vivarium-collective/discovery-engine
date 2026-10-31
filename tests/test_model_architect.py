"""Model architect (DE 2.0 Wave 2 / Cycle N+1 Phase D).

Gate: capability-driven reuse/wrap/build — a mechanism with a catalog component is
reused, a missing one is built, and a hybrid coupling is flagged when it mixes
formalisms. ModelVersion is immutable and content-addressed.
"""
from __future__ import annotations

from discovery_engine import model_architect as M


def _studies():
    return {
        "s1": {"topic": "metabolism", "fidelity": "real",
               "baseline": [{"name": "b", "composite": "viva_genesis.composites.metabolite_exchange_community.metabolite_exchange_community"}],
               "observables": ["settled_cross_lineage_flux"]},
        "s3": {"topic": "membrane-mechanics", "fidelity": "spatial_dynamics",
               "baseline": [{"name": "b", "composite": "viva_genesis.composites.protrusion_enclosure_cpm.protrusion_enclosure_cpm"}],
               "observables": ["settled_enclosure_probability"]},
    }


def test_catalog_is_built_from_study_arms():
    cat = M.build_catalog(_studies())
    comps = {c["composite"] for c in cat}
    assert any("metabolite_exchange_community" in c for c in comps)
    assert any("protrusion_enclosure_cpm" in c for c in comps)


def test_reuse_for_catalog_mechanism_build_for_missing():
    req = M.ModelBuildRequest(question_id="A", required_mechanisms=["exchange", "adhesion", "growth"])
    routes = M.choose_acquisition_route(req, M.build_catalog(_studies()))
    assert routes["exchange"]["route"] == M.REUSE
    assert routes["adhesion"]["route"] in (M.REUSE, M.WRAP)   # CPM provides adhesion
    assert routes["growth"]["route"] == M.BUILD               # nothing provides growth here


def test_coupling_flags_mixed_fidelity_and_build():
    req = M.ModelBuildRequest(question_id="A", required_mechanisms=["exchange", "adhesion", "growth"])
    cat = M.build_catalog(_studies())
    routes = M.choose_acquisition_route(req, cat)
    issues = M.validate_coupling(routes, cat)
    assert any("mixed fidelities" in i for i in issues)   # metabolic (real) + CPM (spatial_dynamics)
    assert any("build" in i.lower() for i in issues)      # growth must be built


def test_model_version_is_content_addressed_and_stable():
    req = M.ModelBuildRequest(question_id="A", required_mechanisms=["exchange"])
    routes = M.choose_acquisition_route(req, M.build_catalog(_studies()))
    mv1 = M.model_version(req, routes)
    mv2 = M.model_version(req, routes)
    assert mv1["id"] == mv2["id"] and mv1["executable_digest"] == mv2["executable_digest"]
    # a different parameterization yields a different immutable version
    mv3 = M.model_version(req, routes, parameterization={"k_wash": 0.02})
    assert mv3["id"] != mv1["id"]


def test_search_ranks_providers():
    found = M.search_components(
        M.ModelBuildRequest(question_id="A", required_mechanisms=["exchange"]), M.build_catalog(_studies()))
    assert found["exchange"] and found["exchange"][0]["_match"] >= 1


def test_resolve_composite_true_and_false(tmp_path):
    ok = M.resolve_composite("discovery_engine.model_architect.build_catalog", tmp_path)
    assert ok["resolves"] is True
    assert ok["module"] == "discovery_engine.model_architect"
    assert ok["spec_path"] and ok["spec_path"].endswith("model_architect.py")
    bad = M.resolve_composite("discovery_engine.no_such_mod.thing", tmp_path)
    assert bad["resolves"] is False
    assert bad["spec_path"] is None
    assert M.resolve_composite("nonexistent_pkg_xyz.mod.f", tmp_path)["resolves"] is False


def test_build_catalog_with_ws_root_marks_components(tmp_path):
    studies = {"s": {"baseline": [{"composite": "discovery_engine.model_architect.build_catalog"}],
                     "variants": [{"composite": "nonexistent_pkg_xyz.mod.f"}]}}
    cat = {c["composite"]: c for c in M.build_catalog(studies, tmp_path)}
    assert cat["discovery_engine.model_architect.build_catalog"]["resolves"] is True
    assert cat["discovery_engine.model_architect.build_catalog"]["module"] == "discovery_engine.model_architect"
    assert cat["nonexistent_pkg_xyz.mod.f"]["resolves"] is False


def test_build_catalog_without_ws_root_unchanged():
    for c in M.build_catalog(_studies()):
        assert not ({"resolves", "module", "spec_path"} & set(c))
