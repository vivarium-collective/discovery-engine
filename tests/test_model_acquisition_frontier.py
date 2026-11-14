from discovery_engine import discover as D
from discovery_engine.projection import engine as de


def _inv(status):
    return {"slug": "inv", "studies": [],
            "model_acquisition_proposals": [
                {"id": "ma:ab12cd34ef56", "mechanism": "dispersal kernel", "status": status}]}


def _ma(rows):
    return [r for r in rows if r["kind"] == "model_acquisition"]


def test_proposed_proposal_emitted_as_frontier_item():
    inv = _inv("proposed")
    rows = _ma(de.frontier(D.run_cycle(inv, {}), inv))
    assert len(rows) == 1 and rows[0]["op"] == "accept_model_acquisition"
    assert rows[0]["target"] == "ma:ab12cd34ef56" and rows[0]["priority"] == 43


def test_accepted_or_declined_proposal_not_emitted():
    for st in ("accepted", "declined"):
        inv = _inv(st)
        assert not _ma(de.frontier(D.run_cycle(inv, {}), inv))


def test_opportunity_is_review_tier():
    inv = _inv("proposed")
    opp = next(o for o in de.opportunities(D.run_cycle(inv, {}), inv) if o["kind"] == "model_acquisition")
    assert opp["action_type"] == "accept_model_acquisition"
    assert opp["id"] == "model_acquisition:ma:ab12cd34ef56" and opp["source"] == "ma:ab12cd34ef56"
    ex = de._executor_for("accept_model_acquisition", opp["source"]) if hasattr(de, "_executor_for") else {}
    assert ex.get("consequence") == "review" and ex.get("auto_safe") is False
