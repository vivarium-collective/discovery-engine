"""R4 live wire: the model-adequacy gate holds an overreaching FindingCandidate at reviewed
promotion — a Discovery is never minted for a claim whose required mechanism is unestablished.
Opt-in: fires only for candidates whose claim declares `requires`."""
from __future__ import annotations

from discovery_engine.discover.actions import review_findings
from discovery_engine.phases.discovery_record import build_finding_candidate
from discovery_engine.study.io import load_yaml, save_yaml_atomic


def _candidate(requires=None):
    spec = {"claim": "a strong claim", "findings": [{"evidence": {"status": "verified"}}]}
    if requires is not None:
        spec["requires"] = requires
    c = build_finding_candidate("s", "unassessed", "supported", spec, fingerprint="fp")
    c["status"] = "candidate"
    return c


def _ws(tmp_path, cand, *, established=None):
    inv = tmp_path / "investigations" / "i"
    inv.mkdir(parents=True)
    spec = {"name": "i", "finding_candidates": [cand]}
    if established is not None:
        spec["established_mechanisms"] = established
    ip = inv / "investigation.yaml"
    save_yaml_atomic(ip, spec)
    return ip


def test_build_candidate_carries_requires_opt_in():
    assert _candidate(["m1", "m2"])["requires"] == ["m1", "m2"]
    assert "requires" not in _candidate()


def test_overreaching_candidate_is_held_not_promoted(tmp_path):
    c = _candidate(["m1", "m2"])                       # claim needs m1 AND m2
    ip = _ws(tmp_path, c, established=["m1"])          # only m1 is established
    out = review_findings(ip, promote=[c["id"]])

    assert c["id"] in out["held"] and c["id"] not in out["promoted"]
    spec = load_yaml(ip)
    assert not spec.get("discoveries")                # NO Discovery minted for the overreach
    held = spec["finding_candidates"][0]
    assert held["assessment"]["review_decision"] == "held_inadequate"
    assert held["assessment"]["adequacy"]["missing_mechanisms"] == ["m2"]


def test_adequate_candidate_is_promoted(tmp_path):
    c = _candidate(["m1"])
    ip = _ws(tmp_path, c, established=["m1", "m2"])    # m1 established
    out = review_findings(ip, promote=[c["id"]])
    assert c["id"] in out["promoted"]
    assert load_yaml(ip).get("discoveries")           # Discovery minted


def test_candidate_without_requires_promotes_as_before(tmp_path):
    c = _candidate()                                  # declares no required mechanisms
    ip = _ws(tmp_path, c)
    out = review_findings(ip, promote=[c["id"]])
    assert c["id"] in out["promoted"]
    assert load_yaml(ip).get("discoveries")
