"""Content-addressed claim identity + the cross-investigation shared-claim audit."""
from discovery_engine.projection import claim_identity as ci
from discovery_engine.projection.shared_claims import (
    shared_claim_candidates,
    audit_workspace,
)
from discovery_engine.projection.argument import derive_argument


# --- claim_key -------------------------------------------------------------

def test_claim_key_is_stable_and_normalized():
    a = ci.claim_key("Growth is limited by glucose uptake.")
    b = ci.claim_key("  growth is limited   by GLUCOSE uptake  ")
    assert a == b and a.startswith("clm-")


def test_stub_proposition_has_no_key():
    assert ci.claim_key("") is None
    assert ci.claim_key(None) is None
    assert ci.claim_key("   ...  ") is None


def test_scope_separates_same_text():
    assert ci.claim_key("X holds", scope="in batch") != ci.claim_key("X holds", scope="in chemostat")
    assert ci.claim_key("X holds") == ci.claim_key("X holds", scope=None)


def test_stamp_claim_keys_is_additive():
    graph = {"claims": {
        "claim:a": {"proposition": "Glucose limits growth", "proposition_stub": False, "scope": None},
        "claim:b": {"proposition": "", "proposition_stub": True, "scope": None},
    }, "edges": []}
    out = ci.stamp_claim_keys(graph)
    assert set(out["claims"]) == {"claim:a", "claim:b"}          # slug keys untouched
    assert out["claims"]["claim:a"]["content_key"].startswith("clm-")
    assert out["claims"]["claim:b"]["content_key"] is None        # stub
    assert "content_key" not in graph["claims"]["claim:a"]        # original not mutated


# --- shared_claim_candidates ----------------------------------------------

def _g(*props):
    return {"claims": {f"claim:s{i}": {"proposition": p, "proposition_stub": not bool(p),
                                       "scope": None, "verdict": "supported"}
                       for i, p in enumerate(props)}, "edges": []}


def test_same_proposition_across_two_investigations_is_a_candidate():
    by_inv = {"inv-A": _g("Glucose limits growth"),
              "inv-B": _g("glucose   LIMITS growth.", "unrelated claim")}
    cands = shared_claim_candidates(by_inv)
    assert len(cands) == 1
    assert cands[0]["n_investigations"] == 2
    assert {o["investigation"] for o in cands[0]["occurrences"]} == {"inv-A", "inv-B"}


def test_same_proposition_within_one_investigation_is_not_a_candidate():
    # two studies in ONE investigation asserting the same claim is not cross-sharing
    by_inv = {"inv-A": _g("Glucose limits growth", "Glucose limits growth")}
    assert shared_claim_candidates(by_inv) == []


def test_distinct_propositions_yield_no_candidates():
    by_inv = {"inv-A": _g("A limits growth"), "inv-B": _g("B limits growth")}
    assert shared_claim_candidates(by_inv) == []


def test_stub_claims_are_skipped():
    by_inv = {"inv-A": _g(""), "inv-B": _g("")}
    assert shared_claim_candidates(by_inv) == []


def test_candidates_sorted_most_shared_first():
    shared = "Oxygen is the limiting exchange"
    by_inv = {"inv-A": _g(shared), "inv-B": _g(shared), "inv-C": _g(shared, "only here")}
    cands = shared_claim_candidates(by_inv)
    assert cands[0]["n_investigations"] == 3


# --- end to end through the real argument projection -----------------------

def test_audit_through_real_derive_argument(tmp_path):
    """Two investigations whose studies carry the same `claim:` text are surfaced
    by the audit built on the actual argument projection + workspace loader."""
    ws = tmp_path
    (ws / "workspace.yaml").write_text("name: t\n", encoding="utf-8")
    for inv in ("alpha", "beta"):
        sd = ws / "investigations" / inv / "studies" / f"{inv}-growth"
        sd.mkdir(parents=True)
        (ws / "investigations" / inv / "investigation.yaml").write_text(
            f"name: {inv}\nmembers: [{inv}-growth]\n", encoding="utf-8")
        (sd / "study.yaml").write_text(
            "claim: Growth is limited by the oxygen exchange rate\nverdict: supported\n",
            encoding="utf-8")
    result = audit_workspace(ws)
    assert result["investigations"] == ["alpha", "beta"]
    assert result["n_claims"] == 2
    assert len(result["candidates"]) == 1
    assert result["candidates"][0]["n_investigations"] == 2

    # sanity: the same proposition really does key the same through derive_argument
    ga = derive_argument({"members": ["alpha-growth"]},
                         {"alpha-growth": {"claim": "Growth is limited by the oxygen exchange rate"}})
    gb = derive_argument({"members": ["beta-growth"]},
                         {"beta-growth": {"claim": "growth is limited by the OXYGEN exchange rate."}})
    ka = ci.claim_key(next(iter(ga["claims"].values()))["proposition"])
    kb = ci.claim_key(next(iter(gb["claims"].values()))["proposition"])
    assert ka == kb
