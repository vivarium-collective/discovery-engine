"""Tests for discovery_engine.investigation_import (selection guard + parsing)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from discovery_engine.investigation_import import (
    _pkg_stem,
    _preferred_spelling,
    assert_selection_ok,
    check,
    detect_missing_deps,
    investigation_members,
    load_selection,
    pinned_rev,
    present_slugs,
    sync,
)


def _make_ws(tmp_path: Path, workspace_yaml: str, inv_slugs: list[str]) -> Path:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(textwrap.dedent(workspace_yaml))
    for slug in inv_slugs:
        d = ws / "investigations" / slug
        d.mkdir(parents=True)
        (d / "investigation.yaml").write_text(f"name: {slug}\n")
    return ws


def test_present_slugs_requires_investigation_yaml(tmp_path: Path) -> None:
    ws = _make_ws(tmp_path, "name: t\n", ["alpha", "beta"])
    # a bare dir without investigation.yaml is not counted
    (ws / "investigations" / "stray-dir").mkdir()
    assert present_slugs(ws) == {"alpha", "beta"}


def test_load_selection_plain_list(tmp_path: Path) -> None:
    ws = _make_ws(
        tmp_path,
        """
        name: t
        imported_investigations:
          - alpha
          - beta
        native_investigations:
          - gamma
        """,
        [],
    )
    sel = load_selection(ws)
    assert sel.allow == ["alpha", "beta"]
    assert sel.native == ["gamma"]
    assert sel.source is None


def test_load_selection_source_form(tmp_path: Path) -> None:
    ws = _make_ws(
        tmp_path,
        """
        name: t
        imported_investigations:
          from: v2ecoli
          git: https://github.com/vivarium-collective/v2ecoli.git
          allow:
            - alpha
        """,
        [],
    )
    sel = load_selection(ws)
    assert sel.allow == ["alpha"]
    assert sel.source is not None
    assert sel.source.package == "v2ecoli"
    assert sel.source.subtree == "workspace/investigations"  # default
    assert sel.source.rev_token == "v2ecoli"


def test_check_passes_when_all_declared(tmp_path: Path) -> None:
    ws = _make_ws(
        tmp_path,
        """
        name: t
        imported_investigations: [alpha, beta]
        native_investigations: [gamma]
        """,
        ["alpha", "beta", "gamma"],
    )
    assert check(ws) == []
    assert_selection_ok(ws)  # does not raise


def test_check_flags_undeclared_investigation(tmp_path: Path) -> None:
    # `intruder` is present on disk but in neither list — must fail.
    ws = _make_ws(
        tmp_path,
        """
        name: t
        imported_investigations: [alpha]
        native_investigations: []
        """,
        ["alpha", "intruder"],
    )
    problems = check(ws)
    assert len(problems) == 1
    assert "intruder" in problems[0].message
    with pytest.raises(AssertionError, match="intruder"):
        assert_selection_ok(ws)


def test_check_flags_overlap(tmp_path: Path) -> None:
    ws = _make_ws(
        tmp_path,
        """
        name: t
        imported_investigations: [alpha]
        native_investigations: [alpha]
        """,
        ["alpha"],
    )
    problems = check(ws)
    assert any("BOTH imported and native" in p.message for p in problems)


def test_pinned_rev_from_uv_lock(tmp_path: Path) -> None:
    ws = _make_ws(tmp_path, "name: t\n", [])
    sha = "a" * 40
    (ws / "uv.lock").write_text(
        'source = { git = "https://github.com/vivarium-collective/v2ecoli.git?rev=main#'
        + sha
        + '" }\n'
    )
    assert pinned_rev(ws, "v2ecoli") == sha
    assert pinned_rev(ws, "nonesuch") is None


# ---------------------------------------------------------------------------
# Dependency propagation: member studies + package deps (sync from a real rev)
# ---------------------------------------------------------------------------

import subprocess


def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", *args],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return r.stdout


_FLAT_STUDY_YAML = textwrap.dedent(
    """
    schema_version: 4
    name: ketchup-exchange-comparison
    investigation: ketchup-baseline-comparison
    conditions:
      baseline:
        composite: pbg_ketchup.composites.estimation.ketchup_baseline
      variants:
      - name: v2e
        composite: v2ecoli.composites.ecoli_baseline.ecoli_baseline
    runs:
    - composite: pbg_ketchup.composites.dynamic.ketchup_dynamic
      name: dyn
    """
)

_NESTED_STUDY_YAML = textwrap.dedent(
    """
    schema_version: 4
    name: nested-study
    investigation: nested-inv
    conditions:
      baseline:
        composite: pbg_ketchup.composites.estimation.ketchup_baseline
    """
)

_UPSTREAM_WS_YAML = textwrap.dedent(
    """
    schema_version: 2
    name: v2ecoli
    package_path: v2ecoli
    layout:
      studies: workspace/studies
      investigations: workspace/investigations
    imports:
      pbg_ketchup:
        source: https://github.com/vivarium-collective/pbg-ketchup
        ref: main
        mode: reference
        description: |
          KETCHUP/IPOPT kinetic-parameter estimators used by the
          ketchup-baseline-comparison investigation.
    dashboard:
      registry:
        include: [v2ecoli, pbg_ketchup]
    """
)


def _make_upstream_repo(tmp_path: Path) -> tuple[Path, str]:
    """A git repo mirroring v2ecoli: nested investigations, one FLAT + one NESTED study."""
    repo = tmp_path / "upstream"
    repo.mkdir()
    (repo / "workspace.yaml").write_text(_UPSTREAM_WS_YAML)

    inv = repo / "workspace" / "investigations" / "ketchup-baseline-comparison"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(
        "name: ketchup-baseline-comparison\nmembers:\n- ketchup-exchange-comparison\n"
    )
    # flat member study
    flat = repo / "workspace" / "studies" / "ketchup-exchange-comparison"
    flat.mkdir(parents=True)
    (flat / "study.yaml").write_text(_FLAT_STUDY_YAML)

    # a second investigation whose member study is NESTED under the investigation
    ninv = repo / "workspace" / "investigations" / "nested-inv"
    (ninv / "studies" / "nested-study").mkdir(parents=True)
    (ninv / "investigation.yaml").write_text("name: nested-inv\nmembers:\n- nested-study\n")
    (ninv / "studies" / "nested-study" / "study.yaml").write_text(_NESTED_STUDY_YAML)

    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "seed")
    sha = _git(repo, "rev-parse", "HEAD").strip()
    return repo, sha


def _downstream_ws(tmp_path: Path, sha: str, allow: list[str], extra: str = "") -> Path:
    ws = tmp_path / "downstream"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(
        textwrap.dedent(
            f"""
            schema_version: 2
            name: sms-ecoli
            package_path: pbg_v2ecoli
            imported_investigations:
              from: v2ecoli
              git: https://github.com/vivarium-collective/v2ecoli.git
              subtree: workspace/investigations
              allow:
            """
        )
        + "".join(f"    - {s}\n" for s in allow)
        + extra
    )
    (ws / "uv.lock").write_text(
        'source = { git = "https://github.com/vivarium-collective/v2ecoli.git?rev=main#'
        + sha
        + '" }\n'
    )
    return ws


def test_investigation_members_shape() -> None:
    # unit: members drawn from members/acceptance_criteria/at_a_glance, deduped.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "investigation.yaml"
        p.write_text(
            textwrap.dedent(
                """
                name: inv
                members: [a, b]
                acceptance_criteria:
                - study: b
                - study: c
                at_a_glance:
                  studies:
                  - a: does a thing
                  - d: does d thing
                """
            )
        )
        assert investigation_members(p) == ["a", "b", "c", "d"]


def test_investigation_members_at_a_glance_as_list() -> None:
    # regression: at_a_glance can be a bare LIST of `{study, role}` rows (the cd2
    # shape), not a `{studies: [...]}` mapping. Take the study value, not the keys.
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        p = Path(td) / "investigation.yaml"
        p.write_text(
            textwrap.dedent(
                """
                name: inv
                at_a_glance:
                - study: alpha
                  role: does the alpha thing
                - study: beta
                  role: does the beta thing
                """
            )
        )
        assert investigation_members(p) == ["alpha", "beta"]


def test_alias_aware_package_mapping() -> None:
    assert _pkg_stem("pbg_ketchup") == "ketchup"
    assert _pkg_stem("viva_ketchup") == "ketchup"
    assert _pkg_stem("viva-ketchup") == "ketchup"
    assert _pkg_stem("v2ecoli") == "v2ecoli"
    assert _preferred_spelling("pbg_ketchup") == "viva_ketchup"
    assert _preferred_spelling("viva_ketchup") == "viva_ketchup"
    assert _preferred_spelling("v2ecoli") == "v2ecoli"


def test_sync_brings_flat_member_study(tmp_path: Path) -> None:
    repo, sha = _make_upstream_repo(tmp_path)
    ws = _downstream_ws(tmp_path, sha, ["ketchup-baseline-comparison"])
    res = sync(ws, upstream_src=repo, rev=sha)
    assert res.added == ["ketchup-baseline-comparison"]
    # flat member study landed under the downstream (flat) studies dir
    assert (ws / "studies" / "ketchup-exchange-comparison" / "study.yaml").is_file()
    assert res.studies_added == {
        "ketchup-baseline-comparison": ["ketchup-exchange-comparison"]
    }


def test_sync_brings_nested_member_study(tmp_path: Path) -> None:
    # Partial-sync starting state: the investigation.yaml is already local (as after
    # an old sync that copied only investigation.yaml) but its NESTED member study is
    # absent. sync must reconcile it from the pinned rev via the member-study step.
    repo, sha = _make_upstream_repo(tmp_path)
    ws = _downstream_ws(tmp_path, sha, ["nested-inv"])
    inv = ws / "investigations" / "nested-inv"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text("name: nested-inv\nmembers:\n- nested-study\n")
    assert not (inv / "studies" / "nested-study").exists()

    res = sync(ws, upstream_src=repo, rev=sha)
    # nested upstream study lands nested under the local investigation
    assert (inv / "studies" / "nested-study" / "study.yaml").is_file()
    assert res.studies_added == {"nested-inv": ["nested-study"]}


def test_sync_is_additive_for_existing_study(tmp_path: Path) -> None:
    repo, sha = _make_upstream_repo(tmp_path)
    ws = _downstream_ws(tmp_path, sha, ["ketchup-baseline-comparison"])
    # pre-place a DIVERGENT local copy of the member study
    local = ws / "studies" / "ketchup-exchange-comparison"
    local.mkdir(parents=True)
    (local / "study.yaml").write_text("name: ketchup-exchange-comparison\nlocal_edit: true\n")
    res = sync(ws, upstream_src=repo, rev=sha)
    assert "ketchup-baseline-comparison" not in res.studies_added
    # local divergent copy left untouched
    assert "local_edit: true" in (local / "study.yaml").read_text()


def test_dep_detection_maps_to_viva_ketchup_and_excludes_source(tmp_path: Path) -> None:
    repo, sha = _make_upstream_repo(tmp_path)
    ws = _downstream_ws(tmp_path, sha, ["ketchup-baseline-comparison"])
    sync(ws, upstream_src=repo, rev=sha)
    findings = detect_missing_deps(ws, ["ketchup-baseline-comparison"])
    pkgs = {f.package for f in findings}
    assert pkgs == {"viva_ketchup"}  # v2ecoli (source) excluded; pbg->viva normalized
    dep = findings[0]
    assert dep.satisfied is False
    assert dep.referenced_by == ["ketchup-exchange-comparison"]


def test_dep_detection_alias_satisfied_by_viva_declaration(tmp_path: Path) -> None:
    repo, sha = _make_upstream_repo(tmp_path)
    # downstream already declares the viva_ spelling -> pbg_ addresses are satisfied
    ws = _downstream_ws(
        tmp_path, sha, ["ketchup-baseline-comparison"],
        extra="imports:\n  viva_ketchup:\n    source: x\n",
    )
    sync(ws, upstream_src=repo, rev=sha)
    findings = detect_missing_deps(ws, ["ketchup-baseline-comparison"])
    assert all(f.satisfied for f in findings)


def test_sync_add_deps_mirrors_source_import_block(tmp_path: Path) -> None:
    repo, sha = _make_upstream_repo(tmp_path)
    ws = _downstream_ws(tmp_path, sha, ["ketchup-baseline-comparison"])
    res = sync(ws, upstream_src=repo, rev=sha, add_deps=True)
    assert res.deps_added == ["pbg_ketchup"]  # mirrors the source's canonical key
    data = load_selection(ws)  # side effect: ensure workspace.yaml still parses
    assert data.allow == ["ketchup-baseline-comparison"]
    import yaml as _yaml
    doc = _yaml.safe_load((ws / "workspace.yaml").read_text())
    assert "pbg_ketchup" in doc["imports"]
    assert doc["imports"]["pbg_ketchup"]["source"].endswith("pbg-ketchup")
    assert "pbg_ketchup" in doc["dashboard"]["registry"]["include"]
    # now that it's declared, detection reports it satisfied
    assert all(f.satisfied for f in detect_missing_deps(ws, ["ketchup-baseline-comparison"]))


def test_check_errors_when_member_exists_upstream_but_missing(tmp_path: Path) -> None:
    # A real partial sync: investigation.yaml present, its member study exists upstream
    # at the pinned rev but was dropped locally → ERROR (sync fixes it).
    repo, sha = _make_upstream_repo(tmp_path)
    ws = _downstream_ws(tmp_path, sha, ["ketchup-baseline-comparison"])
    inv = ws / "investigations" / "ketchup-baseline-comparison"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(
        "name: ketchup-baseline-comparison\nmembers:\n- ketchup-exchange-comparison\n"
    )
    problems = check(ws, upstream_src=repo, rev=sha)
    assert any(
        p.severity == "error" and "ketchup-exchange-comparison" in p.message
        for p in problems
    )
    with pytest.raises(AssertionError, match="ketchup-exchange-comparison"):
        assert_selection_ok(ws, upstream_src=repo, rev=sha)


def test_check_warns_not_errors_when_member_not_upstream(tmp_path: Path) -> None:
    # Over-declared / design-spec: the investigation declares a member study that does
    # NOT exist upstream at the pinned rev → WARNING, never an error (so a design-spec
    # investigation like genotype/pdmp doesn't break CI).
    repo, sha = _make_upstream_repo(tmp_path)
    ws = _downstream_ws(tmp_path, sha, ["ketchup-baseline-comparison"])
    inv = ws / "investigations" / "ketchup-baseline-comparison"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(
        "name: ketchup-baseline-comparison\nmembers:\n"
        "- ketchup-exchange-comparison\n- never-built-aspirational-study\n"
    )
    # materialize the real one so only the aspirational member is missing
    sync(ws, upstream_src=repo, rev=sha)
    problems = check(ws, upstream_src=repo, rev=sha)
    aspir = [p for p in problems if "never-built-aspirational-study" in p.message]
    assert aspir and all(p.severity == "warning" for p in aspir)
    assert not any(p.severity == "error" for p in problems)
    assert_selection_ok(ws, upstream_src=repo, rev=sha)  # warnings don't raise


def test_check_warns_unverified_when_no_source(tmp_path: Path) -> None:
    # Guard-only workspace (plain-list imported_investigations, no sync source): a missing
    # member can't be verified against upstream → WARNING, not a hard error.
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "workspace.yaml").write_text(
        textwrap.dedent(
            """
            name: t
            imported_investigations: [ketchup-baseline-comparison]
            native_investigations: []
            """
        )
    )
    inv = ws / "investigations" / "ketchup-baseline-comparison"
    inv.mkdir(parents=True)
    (inv / "investigation.yaml").write_text(
        "name: ketchup-baseline-comparison\nmembers:\n- ketchup-exchange-comparison\n"
    )
    problems = check(ws)
    miss = [p for p in problems if "ketchup-exchange-comparison" in p.message]
    assert miss and all(p.severity == "warning" for p in miss)
    assert_selection_ok(ws)  # warnings don't raise
