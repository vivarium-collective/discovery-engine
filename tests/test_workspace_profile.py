"""Workspace profile audit, incl. the shared-membership vs independent split."""
import yaml

from discovery_engine.projection.workspace_profile import profile_workspace, render_profile


def _study(ws, inv, slug, claim, verdict="supported"):
    sd = ws / "investigations" / inv / "studies" / slug
    sd.mkdir(parents=True, exist_ok=True)
    sd.joinpath("study.yaml").write_text(
        yaml.safe_dump({"claim": claim, "verdict": verdict}), encoding="utf-8")


def _mk(ws):
    (ws / "workspace.yaml").write_text("name: prof\n", encoding="utf-8")
    for inv in ("alpha", "beta"):
        (ws / "investigations" / inv).mkdir(parents=True, exist_ok=True)
        (ws / "investigations" / inv / "investigation.yaml").write_text(
            yaml.safe_dump({"name": inv}), encoding="utf-8")
    # P: same study slug in both investigations -> MEMBERSHIP sharing
    _study(ws, "alpha", "shared-study", "Glucose limits growth")
    _study(ws, "beta", "shared-study", "Glucose limits growth")
    # Q: distinct slugs, same proposition -> INDEPENDENT sharing
    _study(ws, "alpha", "alpha-q", "Oxygen is the bottleneck exchange")
    _study(ws, "beta", "beta-q", "oxygen is the bottleneck exchange.")


def test_profile_counts_and_sharing_split(tmp_path):
    _mk(tmp_path)
    prof = profile_workspace(tmp_path)
    assert prof["investigations"] == 2
    assert prof["studies"] == 4
    assert prof["claims"] == 4 and prof["stub_claims"] == 0
    assert prof["concepts"] == 4                     # one singleton Concept per study
    assert prof["verdicts"] == {"supported": 4}

    sc = prof["shared_claims"]
    assert sc["total"] == 2
    assert sc["membership"] == 1                      # the shared-study slug, both investigations
    assert sc["independent"] == 1                     # alpha-q vs beta-q, same proposition
    assert sc["genuine"] == 2                         # both verdict-backed, non-boilerplate


def test_empty_workspace_is_safe(tmp_path):
    (tmp_path / "workspace.yaml").write_text("name: empty\n", encoding="utf-8")
    prof = profile_workspace(tmp_path)
    assert prof["investigations"] == 0
    assert prof["shared_claims"]["total"] == 0


def test_render_is_a_string(tmp_path):
    _mk(tmp_path)
    out = render_profile(profile_workspace(tmp_path))
    assert "shared-claim candidates" in out and "membership" in out
