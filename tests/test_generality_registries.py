"""Neutral defaults + registration APIs (no workspace/domain vocabulary in the framework)."""
import re

import pytest

from discovery_engine import hypotheses, model_architect as ma, seed_from_followup as sff

BIO = re.compile(r"syntroph|bioenerg|membrane|enclosure|atp|heritab|endosymb|v2ecoli|genesis", re.I)


@pytest.fixture(autouse=True)
def _restore():
    syn = dict(ma._SYNONYMS); fid = dict(hypotheses._FIDELITY_RANK)
    yield
    ma._SYNONYMS.clear(); ma._SYNONYMS.update(syn)
    hypotheses._FIDELITY_RANK.clear(); hypotheses._FIDELITY_RANK.update(fid)


def test_synonym_default_is_neutral():
    assert ma._SYNONYMS == {}
    assert not BIO.search(repr(ma._SYNONYMS))
    assert ma._provides({"capability": ["syntroph"]}, "exchange") == 0
    assert ma._provides({"capability": ["exchange"]}, "exchange") == 1


def test_register_synonyms_used_and_merges():
    ma.register_synonyms({"exchange": ("syntroph", "flux")})
    assert ma._provides({"capability": ["syntroph"]}, "exchange") >= 1
    ma.register_synonyms({"exchange": ("metabolite",)})
    assert set(ma._SYNONYMS["exchange"]) == {"syntroph", "flux", "metabolite"}
    ma.register_synonyms({"x": ("y",)}, replace=True)
    assert set(ma._SYNONYMS) == {"x"}
    ma.clear_synonyms()
    assert ma._SYNONYMS == {}


def test_fidelity_default_is_neutral_and_registrable():
    assert not BIO.search(repr(hypotheses._FIDELITY_RANK))
    assert hypotheses._fidelity_rank("continuum_membrane_energetics") == 0
    hypotheses.register_fidelity_ranks({"Continuum_Membrane_Energetics": 3})
    assert hypotheses._fidelity_rank("continuum_membrane_energetics") == 3
    hypotheses.register_fidelity_ranks({"a": 9}, replace=True)
    assert hypotheses._fidelity_rank("heuristic") == 0 and hypotheses._fidelity_rank("a") == 9


def test_child_scaffold_has_no_workspace_default():
    sc = sff._child_scaffold("p", {}, "n")
    assert sc["baseline"][0]["composite"] is None
    assert sff._child_scaffold("p", {"baseline": [{"composite": "pkg.c"}]}, "n")["baseline"][0]["composite"] == "pkg.c"
    assert sff._child_scaffold("p", {}, "n", "pkg.d")["baseline"][0]["composite"] == "pkg.d"
