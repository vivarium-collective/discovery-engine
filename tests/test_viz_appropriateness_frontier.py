import pytest

from discovery_engine import discover as D
from discovery_engine import discovery_engine as de

CAT = [{"address": "local:Cross", "inputs": {"arms": "list[string]", "labels": "list[string]"}},
       {"address": "local:Per", "inputs": {"time": "list[float]", "species": "tree"}}]
INV = {"slug": "inv", "studies": ["a", "b", "syn"]}
KINDS = ("viz_misplaced", "viz_duplicated", "viz_untied")


def _viz(addr, name, covers=True):
    e = {"name": name, "address": addr}
    if covers:
        e["covers"] = ["obs"]
    return e


def _items(studies, catalog=CAT, inv=INV):
    res = D.run_cycle(inv, studies, viz_catalog=catalog)
    return res, [i for i in de.frontier(res, inv) if i["kind"] in KINDS]


def test_cross_arm_under_plain_study_is_misplaced_nonblocking():
    res, items = _items({"a": {"visualizations": [_viz("local:Cross", "x")]}})
    assert [(i["kind"], i["op"]) for i in items] == [("viz_misplaced", "relocate_viz")]
    assert items[0]["target"] == "fig:a/x"
    assert not set(KINDS) & set(de._CLOSURE_BLOCKING)
    assert D.RESOLUTION_KINDS and all(k in D.RESOLUTION_KINDS for k in KINDS)


def test_same_under_synthesis_study_is_not_misplaced():
    syn = {"visualizations": [_viz("local:Cross", "x")],
           "parent_relations": [{"study": "a", "relation": "builds-on"},
                                {"study": "b", "relation": "builds-on"}]}
    _, items = _items({"a": {}, "b": {}, "syn": syn})
    assert items == []


def test_duplicated_cross_arm_surfaces_once():
    st = {"a": {"visualizations": [_viz("local:Cross", "x")]},
          "b": {"visualizations": [_viz("local:Cross", "x")]}}
    _, items = _items(st)
    assert [i["kind"] for i in items].count("viz_duplicated") == 1
    assert [i["kind"] for i in items].count("viz_misplaced") == 2


def test_untied_only_with_rollout_flag():
    st = {"a": {"visualizations": [_viz("local:Per", "p", covers=False)]}}
    res, items = _items(st)
    assert items == []
    inv = dict(INV, cycle_flags={"viz_untied": True})
    _, items = _items(st, inv=inv)
    assert [i["kind"] for i in items] == ["viz_untied"]
    # census counts it regardless of the flag
    cs = D.build_cycle_state(res, INV, studies=st)
    assert cs["viz_appropriateness"]["untied"] == 1


@pytest.mark.parametrize("catalog", [None, []])
def test_headless_surfaces_nothing(catalog):
    st = {"a": {"visualizations": [_viz("local:Cross", "x", covers=False)]}}
    inv = dict(INV, cycle_flags={"viz_untied": True})
    res, items = _items(st, catalog=catalog, inv=inv)
    assert items == []


def test_unresolved_address_is_not_assessed_inert():
    st = {"a": {"visualizations": [_viz("local:Unknown", "u")]}}
    res, items = _items(st)
    assert items == []
    assert res["viz_appropriateness"]["not_assessed"] == 1


def test_closure_unaffected_and_advance_never_dispatches():
    st = {"a": {"visualizations": [_viz("local:Cross", "x")]}}
    with_v, _ = _items(st)
    without = D.run_cycle(INV, st)
    assert de.closure(with_v, INV)["closed"] == de.closure(without, INV)["closed"]
    assert "relocate_viz" not in D._ADVANCE_OPS


def test_cycle_state_validates_against_schema():
    st = {"a": {"visualizations": [_viz("local:Cross", "x")]}}
    res, _ = _items(st)
    cs = D.build_cycle_state(res, INV, studies=st)
    D._validate_extensions({"cycle_state": cs})
    with pytest.raises(Exception):
        D._validate_extensions({"cycle_state": {"viz_appropriateness": {"misplaced": "x"}}})
