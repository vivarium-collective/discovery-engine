"""Read-only legacy lift + dual-projection parity (the WP1 migration gate)."""
from pathlib import Path

import pytest
import yaml

from discovery_engine.projection.knowledge_lift import (
    lift_knowledge,
    project_claims_from_records,
    parity,
)


def _studies():
    return {
        "s0": {"claim": "Glucose limits growth", "verdict": "supported",
               "primary_limitation": "batch only", "hypothesis": "monod"},
        "s1": {"title": "oxygen study", "verdict": "refuted",
               "arms": [{"id": "a1", "claim": "O2 is rate-limiting"},
                        {"id": "a2", "claim": "O2 is not rate-limiting"}]},
        "s2": {},  # no proposition -> a stub claim, must round-trip as a stub
    }


def test_lift_produces_separate_records():
    recs = lift_knowledge({"studies": ["s0", "s1", "s2"]}, _studies())
    # one Claim + one Assessment per claim node (s0, s1, s1:a1, s1:a2, s2)
    ids = {c["id"] for c in recs["claims"]}
    assert ids == {"claim:s0", "claim:s1", "claim:s1:a1", "claim:s1:a2", "claim:s2"}
    assert len(recs["assessments"]) == len(recs["claims"])
    s0 = next(c for c in recs["claims"] if c["id"] == "claim:s0")
    assert s0["proposition"] == "Glucose limits growth"
    assert s0["content_key"].startswith("clm-")           # non-stub gets a content key
    s2 = next(c for c in recs["claims"] if c["id"] == "claim:s2")
    assert s2["proposition_stub"] is True and s2["content_key"] is None
    a0 = next(a for a in recs["assessments"] if a["claim_id"] == "claim:s0")
    assert a0["verdict"] == "supported"


def test_parity_is_lossless_on_a_fixture():
    inv = {"studies": ["s0", "s1", "s2"]}
    diffs = parity(inv, _studies())
    assert diffs == [], f"record decomposition dropped fields: {diffs}"


def test_reprojection_matches_derive_argument_claim_ids():
    inv = {"studies": ["s0", "s1"]}
    from discovery_engine.projection.argument import derive_argument
    studies = _studies()
    reproj = project_claims_from_records(lift_knowledge(inv, studies))
    original = derive_argument(inv, studies)["claims"]
    assert set(reproj) == set(original)                    # same claim nodes, incl. arms


# --- the real gate: parity on viva-genesis (skipped if not checked out) ------

def _load_yaml(p):
    try:
        return yaml.safe_load(Path(p).read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        return {}


def test_parity_is_lossless_on_genesis():
    from discovery_engine.projection.shared_claims import _studies_by_slug
    from discovery_engine.investigation.import_ import investigations_dir
    ws = Path.home() / "code" / "viva-genesis"
    if not (ws / "workspace.yaml").is_file():
        pytest.skip("viva-genesis not checked out")
    inv_root = investigations_dir(ws)
    checked = 0
    for inv_dir in sorted(inv_root.iterdir()):
        iy = inv_dir / "investigation.yaml"
        if not iy.is_file():
            continue
        diffs = parity(_load_yaml(iy), _studies_by_slug(inv_dir))
        assert diffs == [], f"{inv_dir.name}: lift is not lossless: {diffs[:3]}"
        checked += 1
    assert checked >= 1
