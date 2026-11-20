"""Derived candidate verdicts: an evidence-complete study the coded roll-up can't resolve
surfaces a `candidate` (source: derived) for promotion, WITHOUT closing anything itself."""
import yaml

from discovery_engine.projection.knowledge import (
    _authored_from_conclusion_verdicts,
    _candidate_verdict,
    canonical_verdict,
)


def _calibrated(gate="passed", statuses=("confirms",)):
    """A study with gate_status + findings but no authored verdict and no coded run →
    the roll-up can't resolve it (derived unassessed) → calibrated."""
    return {"gate_status": gate, "evaluation_status": "evaluated",
            "findings": [{"id": f"f{i}", "status": s} for i, s in enumerate(statuses)]}


def test_candidate_gate_passed_is_supported_even_for_novel_findings():
    assert _candidate_verdict(_calibrated("passed", ("confirms",))) == "supported"
    assert _candidate_verdict(_calibrated("passed", ("novel",))) == "supported"       # novel ≠ refute
    assert _candidate_verdict(_calibrated("passed", ("novel", "confirms"))) == "supported"


def test_candidate_refute_and_failed_gate():
    assert _candidate_verdict(_calibrated("passed", ("refutes",))) == "refuted"
    assert _candidate_verdict(_calibrated("passed", ("refutes", "confirms"))) == "qualified"
    assert _candidate_verdict(_calibrated("failed", ("confirms",))) == "refuted"


def test_candidate_no_passed_gate_is_inconclusive():
    assert _candidate_verdict(_calibrated("", ("novel",))) == "inconclusive"
    assert _candidate_verdict(_calibrated("pending", ("novel",))) == "inconclusive"


def test_canonical_verdict_surfaces_candidate_without_overclaiming():
    rec = canonical_verdict(_calibrated("passed", ("confirms",)))
    # honesty: the TRACKED verdict stays unassessed + calibrated, so closure/AC is unchanged
    assert rec["verdict"] == "unassessed"
    assert rec["calibrated"] is True
    # the derived candidate rides alongside as a promotion proposal
    assert rec["candidate"] == "supported"
    assert rec["candidate_source"] == "derived"
    assert "promote with set-verdict" in rec["rationale"]


def test_authored_verdict_wins_and_suppresses_the_candidate():
    spec = _calibrated("passed", ("confirms",))
    spec["verdict"] = "supported"
    rec = canonical_verdict(spec)
    assert rec["verdict"] == "supported" and rec["verdict_source"] == "authored"
    assert rec.get("candidate") is None


def test_reads_conclusion_verdicts_block_as_authored():
    # the older plural block, worst-wins over its entries
    assert _authored_from_conclusion_verdicts([{"verdict": "supported"}]) == "supported"
    assert _authored_from_conclusion_verdicts(
        [{"verdict": "supported"}, {"verdict": "refuted"}]) == "refuted"
    spec = _calibrated("passed", ("confirms",))
    spec["conclusion_verdicts"] = [{"claim": "x", "verdict": "supported", "confidence": "Accepted"}]
    rec = canonical_verdict(spec)
    assert rec["verdict"] == "supported" and rec["verdict_source"] == "authored"
    assert rec.get("candidate") is None


def test_set_verdict_preserves_comments_and_block_scalars(tmp_path):
    """The reported set-verdict writer bug is already fixed on main (ruamel round-trip);
    lock it: a 3-key verdict edit must not reflow block scalars or drop comments."""
    from discovery_engine.discover.cycle import set_verdict
    p = tmp_path / "study.yaml"
    p.write_text(
        "# authored header -- keep me\n"
        "name: s\n"
        "description: |\n"
        "  a block scalar\n"
        "  that must stay block\n"
        "findings: []\n"
    )
    set_verdict(p, "supported", "evidence is sufficient")
    text = p.read_text()
    assert "# authored header -- keep me" in text          # comment survived
    assert "description: |" in text                        # block scalar stayed block
    assert "that must stay block" in text
    d = yaml.safe_load(text)
    assert d["verdict"] == "supported" and d["verdict_rationale"] == "evidence is sufficient"
